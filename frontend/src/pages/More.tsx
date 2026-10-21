import { Link } from 'react-router';
import { useBackend } from '../api/backend';
import { useReview } from '../hooks/useData';
import { allPath, findingsPath, historyPath, teamPath } from '../lib/paths';

/** The More menu's entries, as a page. */
export default function More() {
  const { project, can } = useBackend();
  const review = useReview();
  return (
    <div className="page">
      <h1 className="m-0">More</h1>
      <ul className="more-list">
        <li><Link to={historyPath(project.slug)}>History</Link> <span className="muted">Every decision ever recorded, newest first.</span></li>
        <li><Link to={allPath(project.slug)}>All items</Link> <span className="muted">Every item still waiting, in one ranked list with filters.</span></li>
        <li><Link to={findingsPath(project.slug)}>Findings</Link> <span className="muted">What the automatic checks flagged, grouped by check.</span></li>
        {(review.data?.more_links ?? []).map((l) => (
          <li key={l.url}><a href={l.url} target="_blank" rel="noreferrer">{l.label}</a> <span className="muted">On the project's own site.</span></li>
        ))}
        {can.adminTeam ? (
          <li><Link to={teamPath(project.slug)}>Team</Link> <span className="muted">Who can vote and decide on this project.</span></li>
        ) : null}
        {can.adminAccounts ? (
          <li><Link to="/admin/accounts">Accounts</Link> <span className="muted">Approve sign-ups.</span></li>
        ) : null}
      </ul>
    </div>
  );
}
