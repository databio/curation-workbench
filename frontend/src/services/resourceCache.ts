/**
 * A tiny generic resource cache.
 *
 * This module is deliberately **domain-agnostic**: no app types, no query
 * key factory, no HTTP client. It knows only "a key is an array of unknowns,
 * a fetcher is `(ctx) => Promise<T>`".
 *
 * The shape is the house `useSyncExternalStore` store from
 * `researcher-profiles/rp-explore/src/store.ts`, generalised one step: instead
 * of ONE piece of module-level state with ONE `Set` of listeners, there is one
 * immutable snapshot and one listener `Set` **per cache key**. Everything else
 * is the same idea — state lives in a plain module object, mutation replaces
 * the snapshot and notifies the listeners, and React reads it through
 * `useSyncExternalStore` (see `useResource.ts`).
 *
 * What it deliberately does NOT do, because nothing needs it:
 * garbage collection, structural sharing, window-focus refetching, offline
 * detection, suspense, infinite queries, optimistic updates.
 *
 * `createResourceCache()` is a factory rather than a module singleton so apps
 * can create isolated caches when key spaces must not collide.
 */

// ---------------------------------------------------------------------------
// Public types
// ---------------------------------------------------------------------------

/** Cache identity. Compared by a stable hash, never by reference. */
export type ResourceKey = readonly unknown[];

/**
 * `pending` = no data yet (including "never asked, because it is disabled").
 * `error`   = the last attempt failed; any previously loaded `data` is kept.
 */
export type ResourceStatus = 'pending' | 'success' | 'error';

/** The immutable value handed to `useSyncExternalStore`. */
export interface ResourceSnapshot<T> {
  data: T | undefined;
  error: Error | undefined;
  status: ResourceStatus;
  /** `status === 'pending'`. True while disabled, matching a gated read. */
  isPending: boolean;
  /** A request is in flight right now (first load or refresh). */
  isFetching: boolean;
  isError: boolean;
  isSuccess: boolean;
  /** `Date.now()` of the last successful settle; 0 when never loaded. */
  dataUpdatedAt: number;
}

export interface FetchContext {
  /** Aborted when the request is superseded or its last reader unmounts. */
  readonly signal: AbortSignal;
}

export type ResourceFetcher<T> = (ctx: FetchContext) => Promise<T>;

/**
 * Return true to attempt again. `failureCount` is how many attempts have
 * ALREADY failed, so it is 0 the first time — the same convention TanStack
 * Query used, which is what makes `failureCount < 2` mean two retries.
 */
export type RetryPolicy = (failureCount: number, error: unknown) => boolean;

/** Milliseconds to wait before the next attempt, given the failures so far. */
export type RetryDelay = (failureCount: number, error: unknown) => number;

export interface ResourceOptions {
  /** How long a successful result is served without a background refresh. */
  staleTime?: number;
  retry?: RetryPolicy;
  retryDelay?: RetryDelay;
}

/** Cache-wide fallbacks. A per-read option always wins over these. */
export type ResourceCacheDefaults = ResourceOptions;

export interface ResourceCache {
  /** Register interest in a key. Returns the unsubscribe function. */
  subscribe(key: ResourceKey, hash: string, listener: () => void): () => void;
  /** The current snapshot. Referentially stable until the entry changes. */
  getSnapshot<T>(hash: string): ResourceSnapshot<T>;
  /** Fetch if there is nothing fresh under this key. Safe to call often. */
  ensure<T>(
    key: ResourceKey,
    hash: string,
    fetcher: ResourceFetcher<T>,
    options?: ResourceOptions,
  ): Promise<void>;
  /** Fetch unconditionally, cancelling an in-flight request for this key. */
  refetch<T>(
    key: ResourceKey,
    hash: string,
    fetcher: ResourceFetcher<T>,
    options?: ResourceOptions,
  ): Promise<void>;
  /**
   * Mark every entry whose key STARTS WITH `prefix` stale, and refresh the ones
   * somebody is currently reading. `invalidate(['genomes'])` hits
   * `['genomes', {limit: 20}]` and `['genomes', {q: 'hg38'}]` alike.
   */
  invalidate(prefix: ResourceKey): void;
  /**
   * Write data directly into the cache, as if a fetch had just succeeded.
   * Aborts any in-flight request for this key and notifies all listeners.
   * Use after a mutation whose response IS the fresh state, so the UI updates
   * instantly without a redundant GET.
   */
  set<T>(key: ResourceKey, hash: string, data: T): void;
  /**
   * Memoized `select`. Keyed on the selector's identity, so one module-level
   * transform per key is computed once and shared by every reader of it — two
   * different selectors over one entry each keep their own result.
   */
  select<T, S>(hash: string, selector: (data: T) => S, input: T): S;
  /** Drop everything and abort every in-flight request. */
  clear(): void;
  /** Read a snapshot without subscribing. For tests and diagnostics. */
  peek(hash: string): ResourceSnapshot<unknown> | undefined;
}

// ---------------------------------------------------------------------------
// Key hashing
// ---------------------------------------------------------------------------

function isPlainObject(value: unknown): value is Record<string, unknown> {
  if (typeof value !== 'object' || value === null || Array.isArray(value)) return false;
  const proto: unknown = Object.getPrototypeOf(value);
  return proto === Object.prototype || proto === null;
}

/**
 * A stable string for a key.
 *
 * Object keys are sorted, so `{limit: 20, offset: 0}` and
 * `{offset: 0, limit: 20}` are one cache entry; `undefined` members drop out,
 * so `{q: undefined, limit: 20}` is the same entry as `{limit: 20}`.
 */
export function hashResourceKey(key: ResourceKey): string {
  return JSON.stringify(key, (_field, value: unknown) =>
    isPlainObject(value)
      ? Object.keys(value)
          .sort()
          .reduce<Record<string, unknown>>((acc, name) => {
            acc[name] = value[name];
            return acc;
          }, {})
      : value,
  );
}

// ---------------------------------------------------------------------------
// Internals
// ---------------------------------------------------------------------------

const EMPTY_SNAPSHOT: ResourceSnapshot<unknown> = Object.freeze({
  data: undefined,
  error: undefined,
  status: 'pending' as const,
  isPending: true,
  isFetching: false,
  isError: false,
  isSuccess: false,
  dataUpdatedAt: 0,
});

interface Entry {
  key: ResourceKey;
  snapshot: ResourceSnapshot<unknown>;
  fetcher: ResourceFetcher<unknown> | null;
  promise: Promise<void> | null;
  controller: AbortController | null;
  fetchId: number;
  listeners: Set<() => void>;
  invalidated: boolean;
  selectCache: WeakMap<object, { input: unknown; output: unknown }>;
}

/** Default backoff: 1s, 2s, 4s … capped at 30s. */
const defaultRetryDelay: RetryDelay = (failureCount) =>
  Math.min(1000 * 2 ** failureCount, 30_000);

/** Default policy: two retries, so three attempts in all. */
const defaultRetry: RetryPolicy = (failureCount) => failureCount < 2;

/** Never retry. Exported because several reads want exactly this. */
export const NO_RETRY: RetryPolicy = () => false;

function toError(value: unknown): Error {
  return value instanceof Error ? value : new Error(String(value));
}

function sleep(ms: number, signal: AbortSignal): Promise<void> {
  if (ms <= 0) return Promise.resolve();
  return new Promise<void>((resolve) => {
    const timer = setTimeout(resolve, ms);
    signal.addEventListener(
      'abort',
      () => {
        clearTimeout(timer);
        resolve();
      },
      { once: true },
    );
  });
}

// ---------------------------------------------------------------------------
// Factory
// ---------------------------------------------------------------------------

export function createResourceCache(defaults: ResourceCacheDefaults = {}): ResourceCache {
  const entries = new Map<string, Entry>();

  const defaultStaleTime = defaults.staleTime ?? 0;

  function entryFor(key: ResourceKey, hash: string): Entry {
    let entry = entries.get(hash);
    if (!entry) {
      entry = {
        key,
        snapshot: EMPTY_SNAPSHOT,
        fetcher: null,
        promise: null,
        controller: null,
        fetchId: 0,
        listeners: new Set(),
        invalidated: false,
        selectCache: new WeakMap(),
      };
      entries.set(hash, entry);
    }
    return entry;
  }

  function publish(entry: Entry, snapshot: ResourceSnapshot<unknown>): void {
    entry.snapshot = snapshot;
    for (const listener of entry.listeners) listener();
  }

  function settleSuccess(entry: Entry, data: unknown): void {
    entry.invalidated = false;
    publish(entry, {
      data,
      error: undefined,
      status: 'success',
      isPending: false,
      isFetching: false,
      isError: false,
      isSuccess: true,
      dataUpdatedAt: Date.now(),
    });
  }

  function settleError(entry: Entry, error: Error): void {
    publish(entry, {
      data: entry.snapshot.data,
      error,
      status: 'error',
      isPending: false,
      isFetching: false,
      isError: true,
      isSuccess: false,
      dataUpdatedAt: entry.snapshot.dataUpdatedAt,
    });
  }

  function settleAborted(entry: Entry): void {
    publish(entry, { ...entry.snapshot, isFetching: false });
  }

  function startFetch(
    entry: Entry,
    fetcher: ResourceFetcher<unknown>,
    options: ResourceOptions,
    force: boolean,
  ): Promise<void> {
    // An in-flight request whose controller is already aborted is doomed: it
    // can only settle as an abort. `entry.promise` still points at it because
    // the rejection is a microtask that has not run yet, so joining it would
    // strand this reader in `pending` forever -- which is what happens when the
    // last listener leaves (aborting the request) and a new one subscribes in
    // the same tick, as StrictMode and any quick unmount/remount both do.
    // Fall through and start a fresh attempt instead. The old settle handler
    // sees a bumped `fetchId` and bails, so it cannot clobber this one.
    const alreadyAborted = entry.controller?.signal.aborted ?? false;
    if (entry.promise && !alreadyAborted) {
      if (!force) return entry.promise;
      entry.controller?.abort();
      entry.promise = null;
      entry.controller = null;
    }

    const retry = options.retry ?? defaults.retry ?? defaultRetry;
    const retryDelay = options.retryDelay ?? defaults.retryDelay ?? defaultRetryDelay;

    const id = (entry.fetchId += 1);
    const controller = new AbortController();
    entry.controller = controller;
    entry.fetcher = fetcher;
    publish(entry, { ...entry.snapshot, isFetching: true });

    const attempt = async (): Promise<unknown> => {
      let failureCount = 0;
      for (;;) {
        try {
          return await fetcher({ signal: controller.signal });
        } catch (error) {
          if (controller.signal.aborted) throw error;
          if (!retry(failureCount, error)) throw error;
          await sleep(retryDelay(failureCount, error), controller.signal);
          failureCount += 1;
          if (controller.signal.aborted) throw error;
        }
      }
    };

    const promise = attempt().then(
      (data) => {
        if (entry.fetchId !== id) return;
        entry.promise = null;
        entry.controller = null;
        settleSuccess(entry, data);
      },
      (error) => {
        if (entry.fetchId !== id) return;
        entry.promise = null;
        entry.controller = null;
        if (controller.signal.aborted) settleAborted(entry);
        else settleError(entry, toError(error));
      },
    );

    entry.promise = promise;
    return promise;
  }

  function isStale(entry: Entry, staleTime: number): boolean {
    if (entry.invalidated) return true;
    if (entry.snapshot.status !== 'success') return true;
    return Date.now() - entry.snapshot.dataUpdatedAt >= staleTime;
  }

  return {
    subscribe(key, hash, listener) {
      const entry = entryFor(key, hash);
      entry.listeners.add(listener);
      return () => {
        entry.listeners.delete(listener);
        if (entry.listeners.size === 0 && entry.controller) {
          entry.controller.abort();
        }
      };
    },

    getSnapshot<T>(hash: string): ResourceSnapshot<T> {
      const entry = entries.get(hash);
      return (entry?.snapshot ?? EMPTY_SNAPSHOT) as ResourceSnapshot<T>;
    },

    ensure(key, hash, fetcher, options = {}) {
      const entry = entryFor(key, hash);
      entry.fetcher = fetcher as ResourceFetcher<unknown>;
      const staleTime = options.staleTime ?? defaultStaleTime;
      if (!isStale(entry, staleTime)) return Promise.resolve();
      return startFetch(entry, fetcher as ResourceFetcher<unknown>, options, false);
    },

    refetch(key, hash, fetcher, options = {}) {
      const entry = entryFor(key, hash);
      return startFetch(entry, fetcher as ResourceFetcher<unknown>, options, true);
    },

    set(key, hash, data) {
      const entry = entryFor(key, hash);
      if (entry.controller) {
        entry.controller.abort();
        entry.promise = null;
        entry.controller = null;
      }
      settleSuccess(entry, data);
    },

    invalidate(prefix) {
      const wanted = hashResourceKey(prefix);
      for (const entry of entries.values()) {
        if (entry.key.length < prefix.length) continue;
        if (hashResourceKey(entry.key.slice(0, prefix.length)) !== wanted) continue;
        entry.invalidated = true;
        if (entry.listeners.size > 0 && entry.fetcher) {
          void startFetch(entry, entry.fetcher, {}, false);
        }
      }
    },

    select<T, S>(hash: string, selector: (data: T) => S, input: T): S {
      const entry = entries.get(hash);
      if (!entry) return selector(input);
      const cached = entry.selectCache.get(selector as unknown as object);
      if (cached && cached.input === input) return cached.output as S;
      const output = selector(input);
      entry.selectCache.set(selector as unknown as object, { input, output });
      return output;
    },

    clear() {
      const stale = [...entries.values()];
      entries.clear();
      for (const entry of stale) {
        entry.fetchId += 1;
        entry.controller?.abort();
        entry.promise = null;
        entry.controller = null;
        publish(entry, EMPTY_SNAPSHOT);
      }
    },

    peek(hash) {
      return entries.get(hash)?.snapshot;
    },
  };
}
