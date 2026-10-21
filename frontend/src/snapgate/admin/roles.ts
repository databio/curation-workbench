/**
 * One shared GET /auth/roles per (apiUrl, user). UserMenu, a nav badge,
 * RoleRequestForm and an app's own "Apply" card may all ask on one page;
 * they share a single request through this module-level store.
 */
import type { AuthError, SiteRole } from '../types';
import { UserAdminAPI } from './api';
import type { AppRoleInfo, RoleRequest, RolesInfo } from './types';

export interface RoleStoreEntry {
  /** null when not loaded yet, or when the backend has no user-admin module (404). */
  data: RolesInfo | null;
  error: AuthError | null;
  loading: boolean;
  /** True once a load has finished (with data, a 404, or an error). */
  loaded: boolean;
}

interface Slot {
  entry: RoleStoreEntry;
  promise: Promise<RolesInfo | null> | null;
  apiUrl: string;
  userId: number;
  listeners: Set<() => void>;
}

const EMPTY: RoleStoreEntry = { data: null, error: null, loading: false, loaded: false };
const slots = new Map<string, Slot>();

export function rolesKey(apiUrl: string, userId: number): string {
  return `${apiUrl}|${userId}`;
}

function slotFor(apiUrl: string, userId: number): Slot {
  const key = rolesKey(apiUrl, userId);
  let slot = slots.get(key);
  if (!slot) {
    slot = { entry: EMPTY, promise: null, apiUrl, userId, listeners: new Set() };
    slots.set(key, slot);
  }
  return slot;
}

function set(slot: Slot, entry: RoleStoreEntry) {
  slot.entry = entry;
  slot.listeners.forEach((fn) => fn());
}

/** Load (or reuse) the roles for this user. Single-flight per key. */
export function loadRoles(apiUrl: string, userId: number, force = false): Promise<RolesInfo | null> {
  const slot = slotFor(apiUrl, userId);
  // A forced reload waits for the request in flight, then asks again, so it
  // never hands back an answer from before the change that prompted it.
  if (slot.promise) {
    return force ? slot.promise.then(() => loadRoles(apiUrl, userId, true)) : slot.promise;
  }
  if (slot.entry.loaded && !force) return Promise.resolve(slot.entry.data);
  set(slot, { ...slot.entry, loading: true });
  const promise = new UserAdminAPI(apiUrl)
    .getRoles()
    .then(
      (data) => {
        set(slot, { data, error: null, loading: false, loaded: true });
        return data;
      },
      (e: unknown) => {
        const error = e as AuthError;
        // 404: the backend did not mount the module. Not an error, no retry loop.
        if (error?.status === 404) {
          set(slot, { data: null, error: null, loading: false, loaded: true });
        } else {
          set(slot, { data: slot.entry.data, error, loading: false, loaded: true });
        }
        return null;
      },
    )
    .finally(() => {
      slot.promise = null;
    });
  slot.promise = promise;
  return promise;
}

/**
 * Forget every cached /auth/roles answer, and reload the ones something on
 * the page is subscribed to. Call after a submit, withdraw or approval.
 */
export function invalidateRoles(): void {
  for (const [key, slot] of slots) {
    if (slot.listeners.size === 0) {
      slots.delete(key);
      continue;
    }
    slot.entry = { ...slot.entry, loaded: false };
    void loadRoles(slot.apiUrl, slot.userId, true);
  }
}

export function subscribe(key: string, fn: () => void): () => void {
  let slot = slots.get(key);
  if (!slot) {
    const [apiUrl, id] = splitKey(key);
    slot = slotFor(apiUrl, id);
  }
  slot.listeners.add(fn);
  return () => {
    slots.get(key)?.listeners.delete(fn);
  };
}

export function snapshot(key: string): RoleStoreEntry {
  return slots.get(key)?.entry ?? EMPTY;
}

function splitKey(key: string): [string, number] {
  const at = key.lastIndexOf('|');
  return [key.slice(0, at), Number(key.slice(at + 1))];
}

// ---------------------------------------------------------------- badges

export interface RoleBadgeItem {
  kind: 'superadmin' | 'admin' | 'app' | 'pending';
  label: string;
}

/** Site role first, then held app roles by label, then "Pending: <label>" per pending request. */
export function roleBadgeItems(s: {
  siteRole: SiteRole | null;
  held: AppRoleInfo[];
  pending: RoleRequest[];
}): RoleBadgeItem[] {
  const items: RoleBadgeItem[] = [];
  if (s.siteRole === 'superadmin') items.push({ kind: 'superadmin', label: 'Superadmin' });
  else if (s.siteRole === 'admin') items.push({ kind: 'admin', label: 'Admin' });
  [...s.held]
    .sort((a, b) => a.label.localeCompare(b.label))
    .forEach((r) => items.push({ kind: 'app', label: r.label }));
  s.pending.forEach((r) => items.push({ kind: 'pending', label: `Pending: ${r.role_label}` }));
  return items;
}
