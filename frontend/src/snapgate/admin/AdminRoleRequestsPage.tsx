import React, { useCallback, useEffect, useState } from 'react';
import { Link, useSearchParams } from 'react-router';
import type { AuthError } from '../types';
import { useUserAdminAPI } from './api';
import { errorText, formatDateTime, humanizeKey, valueText } from './format';
import { invalidateRoles } from './roles';
import type { RoleRequest, RoleRequestStatus } from './types';
import { useSiteRole } from './useSiteRole';

export interface AdminRoleRequestsPageProps {
  /** Default "Role applications". */
  title?: string;
  /** Per-role custom rendering of the app's details; default is a key/value list. */
  renderDetails?: Record<string, (request: RoleRequest) => React.ReactNode>;
  /** Href for a user's detail page, e.g. id => `/app/admin?user=${id}`. When set, names link there. */
  userHref?: (userId: number) => string;
  /** Called after any successful approve / reject (e.g. app refetches its nav pending count). */
  onChanged?: () => void;
  className?: string;
}

const TABS: { status: RoleRequestStatus; label: string }[] = [
  { status: 'pending', label: 'Pending' },
  { status: 'approved', label: 'Approved' },
  { status: 'rejected', label: 'Rejected' },
  { status: 'withdrawn', label: 'Withdrawn' },
];

function asStatus(value: string | null): RoleRequestStatus {
  return TABS.some((t) => t.status === value) ? (value as RoleRequestStatus) : 'pending';
}

/** The app's details as a <dl>: keys humanized, values as text (objects as JSON). Never HTML. */
function DefaultDetails({ details }: { details: Record<string, unknown> }) {
  const entries = Object.entries(details || {});
  if (entries.length === 0) return null;
  return (
    <dl className="sg-admin-card__details">
      {entries.map(([k, v]) => (
        <React.Fragment key={k}>
          <dt>{humanizeKey(k)}</dt>
          <dd>{valueText(v)}</dd>
        </React.Fragment>
      ))}
    </dl>
  );
}

/**
 * The role-application queue: status tabs and a role filter (both in the
 * URL as ?status= and ?role=), one card per application, Approve / Reject
 * with an optional note on pending ones. Server messages (including app
 * codes from on_approve hooks) are shown as sent.
 */
export function AdminRoleRequestsPage({
  title = 'Role applications',
  renderDetails,
  userHref,
  onChanged,
  className,
}: AdminRoleRequestsPageProps) {
  const api = useUserAdminAPI();
  const { roles } = useSiteRole();
  const [params, setParams] = useSearchParams();
  const status = asStatus(params.get('status'));
  const role = params.get('role') || undefined;
  const [requests, setRequests] = useState<RoleRequest[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  const setParam = (key: string, value: string | null) =>
    setParams((prev) => {
      const next = new URLSearchParams(prev);
      if (value) next.set(key, value);
      else next.delete(key);
      return next;
    });

  const load = useCallback(async () => {
    try {
      setRequests(await api.listRoleRequests({ status, role }));
      setError(null);
    } catch (e) {
      setError(errorText(e));
      setRequests((prev) => prev ?? []);
    }
  }, [api, status, role]);

  useEffect(() => {
    setRequests(null);
    load();
  }, [load]);

  const decided = (id: number) => {
    setRequests((prev) => (prev || []).filter((r) => r.id !== id));
    invalidateRoles();
    onChanged?.();
  };

  return (
    <section className={['sg-admin', className].filter(Boolean).join(' ')}>
      <div className="sg-admin__header">
        <h2 className="sg-admin__title">{title}</h2>
      </div>
      <div className="sg-admin__toolbar">
        <div className="sg-tabs" role="tablist">
          {TABS.map((t) => (
            <button
              key={t.status}
              type="button"
              role="tab"
              aria-selected={t.status === status}
              className={`sg-tabs__tab${t.status === status ? ' sg-tabs__tab--active' : ''}`}
              onClick={() => setParam('status', t.status === 'pending' ? null : t.status)}
            >
              {t.label}
            </button>
          ))}
        </div>
        {roles.length > 1 && (
          <select
            className="sg-admin__filter"
            aria-label="Filter by role"
            value={role ?? ''}
            onChange={(e) => setParam('role', e.target.value || null)}
          >
            <option value="">All roles</option>
            {roles.map((r) => (
              <option key={r.name} value={r.name}>
                {r.label}
              </option>
            ))}
          </select>
        )}
      </div>
      {error && (
        <p className="sg-admin__error" role="alert">
          {error}
        </p>
      )}
      {requests === null ? (
        <p className="sg-admin__empty">Loading…</p>
      ) : requests.length === 0 ? (
        <p className="sg-admin__empty">No {status} applications.</p>
      ) : (
        <ul className="sg-admin__list">
          {requests.map((r) => (
            <RequestCard
              key={r.id}
              request={r}
              details={renderDetails?.[r.role] ? renderDetails[r.role](r) : <DefaultDetails details={r.details} />}
              userHref={userHref}
              onDecided={decided}
              onStale={load}
            />
          ))}
        </ul>
      )}
    </section>
  );
}

interface RequestCardProps {
  request: RoleRequest;
  details: React.ReactNode;
  userHref?: (userId: number) => string;
  onDecided: (id: number) => void;
  onStale: () => void;
}

function RequestCard({ request: r, details, userHref, onDecided, onStale }: RequestCardProps) {
  const api = useUserAdminAPI();
  const [note, setNote] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const decide = async (approve: boolean) => {
    setBusy(true);
    setError(null);
    try {
      await (approve ? api.approveRoleRequest(r.id, note) : api.rejectRoleRequest(r.id, note));
      onDecided(r.id);
    } catch (e) {
      setBusy(false);
      if ((e as AuthError)?.code === 'not_pending') onStale();
      else setError(errorText(e));
    }
  };

  const who = r.user_name || r.user_email;
  return (
    <li className="sg-admin-card">
      <div className="sg-admin-card__head">
        <span>
          {userHref ? <Link to={userHref(r.user_id)}>{who}</Link> : <strong>{who}</strong>}
          {r.user_name && <span className="sg-admin-table__cell--muted"> {r.user_email}</span>}
        </span>
        <span className="sg-role-badge">
          <span className="sg-role-badge__item sg-role-badge__item--neutral">{r.role_label}</span>
        </span>
        <span className="sg-admin-table__cell--muted">Sent {formatDateTime(r.created_at)}</span>
      </div>
      {r.message && <p className="sg-admin-card__message">{r.message}</p>}
      {details}
      {r.status === 'pending' ? (
        <div className="sg-admin-card__actions">
          <label className="sg-field sg-admin-card__note">
            <span className="sg-field__label">Note (optional)</span>
            <textarea
              className="sg-field__input"
              value={note}
              onChange={(e) => setNote(e.target.value)}
              disabled={busy}
              rows={2}
              maxLength={1000}
            />
          </label>
          <div className="sg-admin-action-panel__buttons">
            <button type="button" className="sg-button" disabled={busy} onClick={() => decide(true)}>
              Approve
            </button>
            <button
              type="button"
              className="sg-button sg-button--secondary"
              disabled={busy}
              onClick={() => decide(false)}
            >
              Reject
            </button>
          </div>
        </div>
      ) : (
        <p className="sg-admin-table__cell--muted">
          <span className={`sg-status sg-status--${r.status}`}>{humanizeKey(r.status)}</span>{' '}
          {r.decided_at && formatDateTime(r.decided_at)}
          {r.decision_note && ` · ${r.decision_note}`}
        </p>
      )}
      {error && (
        <p className="sg-admin__error" role="alert">
          {error}
        </p>
      )}
    </li>
  );
}
