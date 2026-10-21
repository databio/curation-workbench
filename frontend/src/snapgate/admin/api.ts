import { useMemo } from 'react';
import { useSnapgate } from '../context';
import { expectOk, readJson, snapgateFetch } from '../api';
import type {
  AdminUser,
  AdminUserDetail,
  LoginEvent,
  RoleRequest,
  RoleRequestStatus,
  RolesInfo,
  UserListQuery,
} from './types';

type Params = Record<string, string | number | undefined | null>;

/** `?a=1&b=x`, leaving out empty values; '' when nothing is left. */
function query(params: Params): string {
  const qs = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value === undefined || value === null || value === '') continue;
    qs.set(key, String(value));
  }
  const text = qs.toString();
  return text ? `?${text}` : '';
}

const JSON_HEADERS = { 'Content-Type': 'application/json' };

/**
 * Client for snapgate's user-admin routes. Every call goes through
 * snapgateFetch, so it shares the refresh-on-401 with the rest of snapgate.
 * Errors are AuthError ({code, message, status, field?}); show `message` as sent.
 */
export class UserAdminAPI {
  constructor(private apiUrl: string) {}

  private get<T>(path: string): Promise<T> {
    return snapgateFetch(this.apiUrl, path).then((r) => readJson<T>(r));
  }

  private post<T>(path: string, body?: unknown): Promise<T> {
    return snapgateFetch(this.apiUrl, path, {
      method: 'POST',
      headers: JSON_HEADERS,
      body: body === undefined ? undefined : JSON.stringify(body),
    }).then((r) => readJson<T>(r));
  }

  // ---- admin ----------------------------------------------------------

  listUsers(q: UserListQuery = {}): Promise<{ users: AdminUser[]; total: number }> {
    return this.get(
      `/auth/admin/users${query({ q: q.q?.trim(), status: q.status, limit: q.limit, offset: q.offset })}`,
    );
  }

  getUser(id: number): Promise<AdminUserDetail> {
    return this.get(`/auth/admin/users/${id}`);
  }

  async approveUser(id: number): Promise<AdminUser> {
    return (await this.post<{ user: AdminUser }>(`/auth/admin/users/${id}/approve`)).user;
  }

  async suspendUser(id: number, reason: string): Promise<AdminUser> {
    return (await this.post<{ user: AdminUser }>(`/auth/admin/users/${id}/suspend`, { reason })).user;
  }

  async unsuspendUser(id: number): Promise<AdminUser> {
    return (await this.post<{ user: AdminUser }>(`/auth/admin/users/${id}/unsuspend`)).user;
  }

  async listLogins(opts: { userId?: number; limit?: number } = {}): Promise<LoginEvent[]> {
    const path = `/auth/admin/logins${query({ user_id: opts.userId, limit: opts.limit })}`;
    return (await this.get<{ logins: LoginEvent[] }>(path)).logins;
  }

  /** Default status (server side) is 'pending'; 'all' lists every status. */
  async listRoleRequests(
    opts: { status?: RoleRequestStatus | 'all'; role?: string } = {},
  ): Promise<RoleRequest[]> {
    const path = `/auth/admin/role-requests${query({ status: opts.status, role: opts.role })}`;
    return (await this.get<{ requests: RoleRequest[] }>(path)).requests;
  }

  async approveRoleRequest(id: number, note?: string): Promise<RoleRequest> {
    const body = note?.trim() ? { note: note.trim() } : {};
    return (await this.post<{ request: RoleRequest }>(`/auth/admin/role-requests/${id}/approve`, body)).request;
  }

  async rejectRoleRequest(id: number, note?: string): Promise<RoleRequest> {
    const body = note?.trim() ? { note: note.trim() } : {};
    return (await this.post<{ request: RoleRequest }>(`/auth/admin/role-requests/${id}/reject`, body)).request;
  }

  // ---- self -----------------------------------------------------------

  getRoles(): Promise<RolesInfo> {
    return this.get('/auth/roles');
  }

  async myRoleRequests(): Promise<RoleRequest[]> {
    return (await this.get<{ requests: RoleRequest[] }>('/auth/role-requests')).requests;
  }

  async submitRoleRequest<D>(role: string, message: string, details: D): Promise<RoleRequest<D>> {
    const r = await this.post<{ request: RoleRequest<D> }>('/auth/role-requests', { role, message, details });
    return r.request;
  }

  async withdrawRoleRequest(id: number): Promise<void> {
    await expectOk(await snapgateFetch(this.apiUrl, `/auth/role-requests/${id}`, { method: 'DELETE' }));
  }
}

/** A UserAdminAPI for the provider's apiUrl (memoized). */
export function useUserAdminAPI(): UserAdminAPI {
  const { apiUrl } = useSnapgate();
  return useMemo(() => new UserAdminAPI(apiUrl), [apiUrl]);
}
