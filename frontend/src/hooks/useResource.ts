/**
 * React bindings for `resourceCache.ts`.
 *
 * `useResource` is the single read primitive for the house resource cache.
 * Domain-agnostic — works with any fetcher and any key shape.
 */

import { useCallback, useContext, useEffect, useMemo, useRef, useSyncExternalStore } from 'react';
import { ResourceCacheContext } from '../services/resourceCacheContext';
import { hashResourceKey } from '../services/resourceCache';
import type {
  ResourceCache,
  ResourceFetcher,
  ResourceKey,
  ResourceStatus,
  RetryPolicy,
} from '../services/resourceCache';

export interface UseResourceOptions<T, S> {
  /** False parks the read: no request, and the result stays `isPending`. */
  enabled?: boolean;
  staleTime?: number;
  retry?: RetryPolicy;
  /**
   * Transform applied outside the cache entry, so several selectors can share
   * one stored payload. MUST be a stable reference (a module-level function),
   * or it is recomputed on every render.
   */
  select?: (data: T) => S;
}

export interface UseResourceResult<S> {
  data: S | undefined;
  error: Error | undefined;
  status: ResourceStatus;
  isPending: boolean;
  isFetching: boolean;
  isError: boolean;
  isSuccess: boolean;
  dataUpdatedAt: number;
  refetch: () => Promise<void>;
}

/** The cache for the current scope. Throws outside a provider. */
export function useResourceCache(): ResourceCache {
  const cache = useContext(ResourceCacheContext);
  if (!cache) {
    throw new Error('useResource must be used inside a <ResourceCacheContext.Provider>');
  }
  return cache;
}

/** `invalidate(['genomes'])` — prefix match, see `ResourceCache.invalidate`. */
export function useInvalidateResources(): (prefix: ResourceKey) => void {
  const cache = useResourceCache();
  return useCallback((prefix: ResourceKey) => cache.invalidate(prefix), [cache]);
}

/**
 * Write data directly into the cache for a key, as if a fetch had just succeeded.
 * Use after a mutation whose response IS the fresh state, so the UI updates
 * instantly without a redundant GET.
 */
export function useSetResourceData(): <T>(key: ResourceKey, data: T) => void {
  const cache = useResourceCache();
  return useCallback(
    <T,>(key: ResourceKey, data: T) => cache.set(key, hashResourceKey(key), data),
    [cache],
  );
}

export function useResource<T, S = T>(
  key: ResourceKey,
  fetcher: ResourceFetcher<T>,
  options: UseResourceOptions<T, S> = {},
): UseResourceResult<S> {
  const cache = useResourceCache();
  const { enabled = true, select } = options;
  const hash = hashResourceKey(key);

  const keyRef = useRef(key);
  keyRef.current = key;
  const fetcherRef = useRef(fetcher);
  fetcherRef.current = fetcher;
  const optionsRef = useRef(options);
  optionsRef.current = options;

  const stableFetcher = useCallback<ResourceFetcher<T>>((ctx) => fetcherRef.current(ctx), []);

  const subscribe = useCallback(
    (listener: () => void) => cache.subscribe(keyRef.current, hash, listener),
    [cache, hash],
  );
  const getSnapshot = useCallback(() => cache.getSnapshot<T>(hash), [cache, hash]);
  const snapshot = useSyncExternalStore(subscribe, getSnapshot, getSnapshot);

  useEffect(() => {
    if (!enabled) return;
    const { staleTime, retry } = optionsRef.current;
    void cache.ensure(keyRef.current, hash, stableFetcher, { staleTime, retry });
  }, [cache, hash, enabled, stableFetcher]);

  const refetch = useCallback(() => {
    const { staleTime, retry } = optionsRef.current;
    return cache.refetch(keyRef.current, hash, stableFetcher, { staleTime, retry });
  }, [cache, hash, stableFetcher]);

  const raw = snapshot.data;
  const data = useMemo(() => {
    if (!select) return raw as unknown as S | undefined;
    if (raw === undefined) return undefined;
    return cache.select<T, S>(hash, select, raw);
  }, [cache, hash, select, raw]);

  return useMemo(
    () => ({
      data,
      error: snapshot.error,
      status: snapshot.status,
      isPending: snapshot.isPending,
      isFetching: snapshot.isFetching,
      isError: snapshot.isError,
      isSuccess: snapshot.isSuccess,
      dataUpdatedAt: snapshot.dataUpdatedAt,
      refetch,
    }),
    [data, snapshot, refetch],
  );
}
