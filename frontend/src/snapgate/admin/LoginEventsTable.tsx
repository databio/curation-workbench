import { Link } from 'react-router';
import { formatDateTime, shortUserAgent } from './format';
import type { LoginEvent } from './types';

interface LoginEventsTableProps {
  logins: LoginEvent[];
  /** Show the person column (off on a single user's detail view). Default true. */
  showPerson?: boolean;
  /** Href for a user's detail page; when set, names with a user_id link there. */
  userHref?: (userId: number) => string;
  emptyText?: string;
}

/** Sign-in attempts: time, person, allowed/refused, reason, IP, user agent. */
export function LoginEventsTable({
  logins,
  showPerson = true,
  userHref,
  emptyText = 'No sign-ins yet.',
}: LoginEventsTableProps) {
  if (logins.length === 0) return <p className="sg-admin__empty">{emptyText}</p>;
  return (
    <div className="sg-admin-table-wrap">
      <table className="sg-admin-table">
        <thead>
          <tr>
            <th scope="col">Time</th>
            {showPerson && <th scope="col">Person</th>}
            <th scope="col">Result</th>
            <th scope="col">Reason</th>
            <th scope="col">IP</th>
            <th scope="col">Browser</th>
          </tr>
        </thead>
        <tbody>
          {logins.map((e) => (
            <tr key={e.id}>
              <td>{formatDateTime(e.at)}</td>
              {showPerson && (
                <td>
                  {e.user_id !== null && userHref ? (
                    <Link to={userHref(e.user_id)}>{e.name || e.email}</Link>
                  ) : (
                    <span>{e.name || e.email}</span>
                  )}
                  {e.name && <span className="sg-admin-table__cell--muted"> {e.email}</span>}
                </td>
              )}
              <td>
                <span className={`sg-status sg-status--${e.allowed ? 'allowed' : 'refused'}`}>
                  {e.allowed ? 'Allowed' : 'Refused'}
                </span>
              </td>
              <td className="sg-admin-table__cell--muted">{e.reason || ''}</td>
              <td className="sg-admin-table__cell--muted">{e.ip || ''}</td>
              <td className="sg-admin-table__cell--muted" title={e.user_agent || undefined}>
                {shortUserAgent(e.user_agent)}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
