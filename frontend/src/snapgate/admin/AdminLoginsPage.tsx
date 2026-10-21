import { useCallback, useEffect, useState } from 'react';
import { useSearchParams } from 'react-router';
import { useUserAdminAPI } from './api';
import { errorText } from './format';
import { LoginEventsTable } from './LoginEventsTable';
import type { LoginEvent } from './types';

export interface AdminLoginsPageProps {
  /** Default "Sign-ins". */
  title?: string;
  /** How many to fetch first. Default 100. */
  limit?: number;
  /** Href for a user's detail page; when set, names link there. */
  userHref?: (userId: number) => string;
  /** Accepted so all admin pages share props; this page is read-only and never calls it. */
  onChanged?: () => void;
  className?: string;
}

type Filter = 'all' | 'allowed' | 'refused';
const LIMIT_STEPS = [100, 250, 500];

/**
 * The sign-in log, newest first. `?user_id=<id>` narrows it to one person.
 * The server has no offset for logins, so "Show more" raises the limit.
 */
export function AdminLoginsPage({ title = 'Sign-ins', limit = 100, userHref, className }: AdminLoginsPageProps) {
  const api = useUserAdminAPI();
  const [params] = useSearchParams();
  const userId = Number(params.get('user_id')) || undefined;
  const [max, setMax] = useState(limit);
  const [filter, setFilter] = useState<Filter>('all');
  const [logins, setLogins] = useState<LoginEvent[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      setLogins(await api.listLogins({ userId, limit: max }));
      setError(null);
    } catch (e) {
      setError(errorText(e));
      setLogins((prev) => prev ?? []);
    }
  }, [api, userId, max]);

  useEffect(() => {
    load();
  }, [load]);

  const nextMax = LIMIT_STEPS.find((n) => n > max);
  const shown = (logins ?? []).filter((e) =>
    filter === 'all' ? true : filter === 'allowed' ? e.allowed : !e.allowed,
  );

  return (
    <section className={['sg-admin', className].filter(Boolean).join(' ')}>
      <div className="sg-admin__header">
        <h2 className="sg-admin__title">{title}</h2>
      </div>
      <div className="sg-admin__toolbar">
        <select
          className="sg-admin__filter"
          aria-label="Filter sign-ins"
          value={filter}
          onChange={(e) => setFilter(e.target.value as Filter)}
        >
          <option value="all">All</option>
          <option value="allowed">Allowed</option>
          <option value="refused">Refused</option>
        </select>
        <span className="sg-admin__count">{shown.length} shown</span>
      </div>
      {error && (
        <p className="sg-admin__error" role="alert">
          {error}
        </p>
      )}
      {logins === null ? (
        <p className="sg-admin__empty">Loading…</p>
      ) : (
        <LoginEventsTable logins={shown} userHref={userHref} />
      )}
      {logins !== null && logins.length >= max && nextMax !== undefined && (
        <div className="sg-admin__pager">
          <button type="button" className="sg-button sg-button--secondary" onClick={() => setMax(nextMax)}>
            Show more
          </button>
        </div>
      )}
    </section>
  );
}
