import { useCallback, useEffect, useState } from 'react';
import { useSearchParams } from 'react-router';
import type { UserStatus } from '../types';
import { useUserAdminAPI } from './api';
import { AdminUserDetail } from './AdminUserDetail';
import { errorText, formatDate, formatDateTime, statusLabel } from './format';
import type { AdminUser } from './types';
import { UserActions } from './UserActions';

export interface AdminUsersPageProps {
  /** Default "Users". */
  title?: string;
  /** Default 50. */
  pageSize?: number;
  className?: string;
  /** Called after any successful approve / suspend / unsuspend (e.g. app refetches its /me or nav counts). */
  onChanged?: () => void;
}

const STATUSES: UserStatus[] = ['pending', 'approved', 'suspended', 'denied'];
const SEARCH_DELAY_MS = 300;

function asStatus(value: string | null): UserStatus | undefined {
  return STATUSES.includes(value as UserStatus) ? (value as UserStatus) : undefined;
}

/**
 * Every account: search, status filter, paging, approve / suspend /
 * unsuspend, and a detail view at `?user=<id>`. All state lives in the URL
 * query (q, status, offset, user), so views can be linked and Back works.
 * Needs the backend's create_useradmin_router; the server enforces access.
 */
export function AdminUsersPage({ title = 'Users', pageSize = 50, className, onChanged }: AdminUsersPageProps) {
  const api = useUserAdminAPI();
  const [params, setParams] = useSearchParams();
  const q = params.get('q') ?? '';
  const status = asStatus(params.get('status'));
  const offset = Math.max(0, Number(params.get('offset')) || 0);
  const openId = Number(params.get('user')) || null;

  const [search, setSearch] = useState(q);
  const [users, setUsers] = useState<AdminUser[] | null>(null);
  const [total, setTotal] = useState(0);
  const [error, setError] = useState<string | null>(null);

  const update = useCallback(
    (changes: Record<string, string | null>) =>
      setParams((prev) => {
        const next = new URLSearchParams(prev);
        for (const [k, v] of Object.entries(changes)) {
          if (v === null || v === '') next.delete(k);
          else next.set(k, v);
        }
        return next;
      }),
    [setParams],
  );

  // The URL changed under us (Back, a link): show its query in the box.
  useEffect(() => {
    setSearch((current) => (current.trim() === q ? current : q));
  }, [q]);

  // Debounced search box -> ?q= (and back to the first page).
  useEffect(() => {
    if (search.trim() === q) return;
    const t = setTimeout(() => update({ q: search.trim() || null, offset: null }), SEARCH_DELAY_MS);
    return () => clearTimeout(t);
  }, [search, q, update]);

  const load = useCallback(async () => {
    try {
      const page = await api.listUsers({ q, status, limit: pageSize, offset });
      setUsers(page.users);
      setTotal(page.total);
      setError(null);
    } catch (e) {
      setError(errorText(e));
      setUsers((prev) => prev ?? []);
    }
  }, [api, q, status, pageSize, offset]);

  useEffect(() => {
    if (openId === null) load();
  }, [load, openId]);

  const replace = (updated: AdminUser) => {
    setUsers((prev) => (prev || []).map((u) => (u.id === updated.id ? updated : u)));
    onChanged?.();
  };

  const rootClass = ['sg-admin', className].filter(Boolean).join(' ');

  if (openId !== null) {
    return (
      <section className={rootClass}>
        <AdminUserDetail userId={openId} onBack={() => update({ user: null })} onChanged={() => onChanged?.()} />
      </section>
    );
  }

  return (
    <section className={rootClass}>
      <div className="sg-admin__header">
        <h2 className="sg-admin__title">{title}</h2>
      </div>
      <div className="sg-admin__toolbar">
        <input
          type="search"
          className="sg-admin__search"
          placeholder="Search name or email"
          aria-label="Search users"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
        />
        <select
          className="sg-admin__filter"
          aria-label="Filter by status"
          value={status ?? ''}
          onChange={(e) => update({ status: e.target.value || null, offset: null })}
        >
          <option value="">All</option>
          {STATUSES.map((s) => (
            <option key={s} value={s}>
              {statusLabel(s)}
            </option>
          ))}
        </select>
        <span className="sg-admin__count">
          {total} {total === 1 ? 'user' : 'users'}
        </span>
      </div>
      {error && (
        <p className="sg-admin__error" role="alert">
          {error}
        </p>
      )}
      {users === null ? (
        <p className="sg-admin__empty">Loading…</p>
      ) : users.length === 0 ? (
        <p className="sg-admin__empty">No users found.</p>
      ) : (
        <div className="sg-admin-table-wrap">
          <table className="sg-admin-table">
            <thead>
              <tr>
                <th scope="col">Person</th>
                <th scope="col">Status</th>
                <th scope="col">Joined</th>
                <th scope="col">Last sign-in</th>
                <th scope="col">Sign-ins</th>
                <th scope="col">Applications</th>
                <th scope="col">Actions</th>
              </tr>
            </thead>
            <tbody>
              {users.map((u) => (
                <tr key={u.id}>
                  <td>
                    <div className="sg-admin__person">
                      {u.picture && <img src={u.picture} alt="" className="sg-avatar" />}
                      <div className="sg-identity">
                        <button type="button" className="sg-link sg-admin__name" onClick={() => update({ user: String(u.id) })}>
                          {u.name || u.email}
                        </button>
                        <p className="sg-identity__email">{u.email}</p>
                      </div>
                    </div>
                  </td>
                  <td>
                    <span
                      className={`sg-status sg-status--${u.status}`}
                      title={u.status === 'suspended' && u.status_reason ? u.status_reason : undefined}
                    >
                      {statusLabel(u.status)}
                    </span>
                    {u.site_role && (
                      <span className="sg-role-badge">
                        <span className="sg-role-badge__item sg-role-badge__item--admin">
                          {u.site_role === 'superadmin' ? 'Superadmin' : 'Admin'}
                        </span>
                      </span>
                    )}
                  </td>
                  <td>{formatDate(u.created_at)}</td>
                  <td>{formatDateTime(u.last_sign_in_at ?? u.last_login)}</td>
                  <td>{u.login_count}</td>
                  <td>
                    {u.pending_requests.map((role) => (
                      <span key={role} className="sg-status sg-status--pending">
                        {role}
                      </span>
                    ))}
                  </td>
                  <td>
                    <UserActions user={u} onChange={replace} onStale={load} />
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {total > pageSize && (
        <div className="sg-admin__pager">
          <button
            type="button"
            className="sg-button sg-button--secondary"
            disabled={offset === 0}
            onClick={() => update({ offset: offset - pageSize > 0 ? String(offset - pageSize) : null })}
          >
            Previous
          </button>
          <span className="sg-admin__count">
            {offset + 1}–{Math.min(offset + pageSize, total)} of {total}
          </span>
          <button
            type="button"
            className="sg-button sg-button--secondary"
            disabled={offset + pageSize >= total}
            onClick={() => update({ offset: String(offset + pageSize) })}
          >
            Next
          </button>
        </div>
      )}
    </section>
  );
}
