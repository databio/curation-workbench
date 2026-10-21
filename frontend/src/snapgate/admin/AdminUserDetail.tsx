import { useCallback, useEffect, useState } from 'react';
import { useUserAdminAPI } from './api';
import { auditActionLabel, errorText, formatDate, formatDateTime, humanizeKey, statusLabel, valueText } from './format';
import { LoginEventsTable } from './LoginEventsTable';
import type { AdminUser, AdminUserDetail as Detail } from './types';
import { UserActions } from './UserActions';

interface AdminUserDetailProps {
  userId: number;
  onBack: () => void;
  /** A successful action on this account. */
  onChanged?: (updated: AdminUser) => void;
}

/** One account: header, actions, sign-ins, role applications and the audit log. */
export function AdminUserDetail({ userId, onBack, onChanged }: AdminUserDetailProps) {
  const api = useUserAdminAPI();
  const [detail, setDetail] = useState<Detail | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      setDetail(await api.getUser(userId));
      setError(null);
    } catch (e) {
      setError(errorText(e));
    }
  }, [api, userId]);

  useEffect(() => {
    setDetail(null);
    load();
  }, [load]);

  const u = detail?.user;
  return (
    <div className="sg-admin__detail">
      <button type="button" className="sg-link sg-admin__back" onClick={onBack}>
        ← Back to users
      </button>
      {error && (
        <p className="sg-admin__error" role="alert">
          {error}
        </p>
      )}
      {!detail || !u ? (
        !error && <p className="sg-admin__empty">Loading…</p>
      ) : (
        <>
          <div className="sg-admin__header">
            <h3 className="sg-admin__title">{u.name || u.email}</h3>
            <p className="sg-admin-table__cell--muted">{u.email}</p>
            <p>
              <span className={`sg-status sg-status--${u.status}`}>{statusLabel(u.status)}</span>
              {u.site_role && (
                <span className="sg-role-badge">
                  <span className="sg-role-badge__item sg-role-badge__item--admin">
                    {u.site_role === 'superadmin' ? 'Superadmin' : 'Admin'}
                  </span>
                </span>
              )}
            </p>
            <p className="sg-admin-table__cell--muted">
              Joined {formatDate(u.created_at)}
              {u.status_changed_at && (
                <>
                  {' · '}
                  {statusLabel(u.status)} {formatDateTime(u.status_changed_at)}
                  {u.status_changed_by !== null && ` by user #${u.status_changed_by}`}
                </>
              )}
            </p>
            {u.status_reason && <p className="sg-admin-card__message">Reason: {u.status_reason}</p>}
          </div>

          <UserActions
            user={u}
            onChange={(updated) => {
              onChanged?.(updated);
              load();
            }}
            onStale={load}
          />

          <section className="sg-admin__section">
            <h4 className="sg-admin__section-title">Sign-ins</h4>
            <LoginEventsTable logins={detail.logins} showPerson={false} />
          </section>

          <section className="sg-admin__section">
            <h4 className="sg-admin__section-title">Role applications</h4>
            {detail.role_requests.length === 0 ? (
              <p className="sg-admin__empty">No role applications.</p>
            ) : (
              <div className="sg-admin-table-wrap">
                <table className="sg-admin-table">
                  <thead>
                    <tr>
                      <th scope="col">Role</th>
                      <th scope="col">Status</th>
                      <th scope="col">Sent</th>
                      <th scope="col">Decided</th>
                      <th scope="col">Note</th>
                    </tr>
                  </thead>
                  <tbody>
                    {detail.role_requests.map((r) => (
                      <tr key={r.id}>
                        <td>{r.role_label}</td>
                        <td>
                          <span className={`sg-status sg-status--${r.status}`}>{humanizeKey(r.status)}</span>
                        </td>
                        <td>{formatDateTime(r.created_at)}</td>
                        <td>{formatDateTime(r.decided_at)}</td>
                        <td className="sg-admin-table__cell--muted">{r.decision_note || ''}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </section>

          <section className="sg-admin__section">
            <h4 className="sg-admin__section-title">Audit log</h4>
            {detail.audit.length === 0 ? (
              <p className="sg-admin__empty">Nothing recorded yet.</p>
            ) : (
              <div className="sg-admin-table-wrap">
                <table className="sg-admin-table">
                  <thead>
                    <tr>
                      <th scope="col">Time</th>
                      <th scope="col">Action</th>
                      <th scope="col">By</th>
                      <th scope="col">Details</th>
                    </tr>
                  </thead>
                  <tbody>
                    {detail.audit.map((a) => (
                      <tr key={a.id}>
                        <td>{formatDateTime(a.at)}</td>
                        <td>{auditActionLabel(a.action)}</td>
                        <td>{a.actor_label}</td>
                        <td className="sg-admin-table__cell--muted">
                          {Object.entries(a.details || {})
                            .map(([k, v]) => `${humanizeKey(k)}: ${valueText(v)}`)
                            .join('; ')}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </section>
        </>
      )}
    </div>
  );
}
