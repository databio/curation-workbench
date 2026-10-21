import { useCallback, useEffect, useMemo, useSyncExternalStore } from 'react';
import { useSnapgate } from '../context';
import type { AuthError, SiteRole } from '../types';
import { invalidateRoles, loadRoles, rolesKey, snapshot, subscribe } from './roles';
import type { AppRoleInfo, RoleRequest } from './types';

export interface SiteRoleState {
  /** From /auth/roles when loaded, else user.site_role (always on /auth/me). */
  siteRole: SiteRole | null;
  /** siteRole is 'admin' or 'superadmin'. */
  isAdmin: boolean;
  isSuperadmin: boolean;
  /** App roles people may apply for ([] when the module is off). */
  roles: AppRoleInfo[];
  /** App roles the user holds, with labels. */
  held: AppRoleInfo[];
  /** The user's pending role requests. */
  pending: RoleRequest[];
  holds: (role: string) => boolean;
  pendingFor: (role: string) => RoleRequest | undefined;
  /** True only while the first /auth/roles load is in flight. */
  loading: boolean;
  error: AuthError | null;
  /** Drop cached roles and reload. */
  refresh: () => Promise<void>;
}

const NO_KEY = '';
const noop = () => () => {};

/**
 * The signed-in user's site role and app roles. With `<SnapgateProvider
 * userAdmin>` it shares one GET /auth/roles across the page; without it, it
 * never fetches and reports only user.site_role. Signed out: the empty state.
 */
export function useSiteRole(): SiteRoleState {
  return useRolesState(true);
}

/** useSiteRole, with `fetchRoles` false meaning "never touch /auth/roles" (RoleBadge with app roles). */
export function useRolesState(fetchRoles: boolean): SiteRoleState {
  const { apiUrl, user, userAdmin } = useSnapgate();
  const key = user && userAdmin && fetchRoles ? rolesKey(apiUrl, user.id) : NO_KEY;

  const sub = useCallback((fn: () => void) => (key ? subscribe(key, fn) : noop()), [key]);
  const get = useCallback(() => snapshot(key), [key]);
  const entry = useSyncExternalStore(sub, get, get);

  useEffect(() => {
    if (key && user) void loadRoles(apiUrl, user.id);
  }, [key, apiUrl, user]);

  const refresh = useCallback(async () => {
    invalidateRoles();
    if (key && user) await loadRoles(apiUrl, user.id, true);
  }, [key, apiUrl, user]);

  return useMemo(() => {
    const data = entry.data;
    const siteRole = data ? data.site_role : (user?.site_role ?? null);
    const roles = data?.roles ?? [];
    const heldNames = new Set(data?.held ?? []);
    const held = roles.filter((r) => heldNames.has(r.name));
    const pending = data?.pending ?? [];
    return {
      siteRole,
      isAdmin: siteRole === 'admin' || siteRole === 'superadmin',
      isSuperadmin: siteRole === 'superadmin',
      roles,
      held,
      pending,
      holds: (role: string) => heldNames.has(role),
      pendingFor: (role: string) => pending.find((r) => r.role === role),
      loading: Boolean(key) && !entry.loaded && !entry.data,
      error: entry.error,
      refresh,
    };
  }, [entry, user, key, refresh]);
}
