import { Link } from 'react-router';
import { useBackend } from '../api/backend';
import { useReview } from '../hooks/useData';
import { allPath, findingsPath, historyPath, teamPath } from '../lib/paths';

/** The More menu's entries, as a page. */
export default function More() {
  const { project, can } = useBackend();
  const review = useReview();
  const links = review.data?.more_links ?? [];
  return (
    <div className="page">
      <h1 className="m-0">More</h1>
      <ul className="more-list">
        <li><Link to={historyPath(project.slug)}>History</Link> <span className="muted">Every decision, newest first.</span></li>
        <li><Link to={allPath(project.slug)}>All items</Link> <span className="muted">Every item still waiting, ranked.</span></li>
        <li><Link to={findingsPath(project.slug)}>Findings</Link> <span className="muted">What the checks flagged, by check.</span></li>
        {can.adminTeam ? (
          <li><Link to={teamPath(project.slug)}>Team</Link> <span className="muted">Who votes and who decides.</span></li>
        ) : null}
        {can.adminAccounts ? (
          <li><Link to="/admin/accounts">Accounts</Link> <span className="muted">Approve sign-ups.</span></li>
        ) : null}
      </ul>
      {links.length > 0 ? (
        <>
          <h2 className="m-0 text-base muted">On the project's site</h2>
          <ul className="more-list">
            {links.map((l) => (
              <li key={l.url}><a href={l.url} target="_blank" rel="noreferrer">{l.label} ↗</a></li>
            ))}
          </ul>
        </>
      ) : null}
    </div>
  );
}
