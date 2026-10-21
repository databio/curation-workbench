// Hosted home: the projects you are on (all of them for a superadmin).
import { Link } from 'react-router';
import { listHostedProjects } from '../../api/hosted';
import { QueryState } from '../../components/QueryState';
import { useResource } from '../../hooks/useResource';
import { projectPath } from '../../lib/paths';

export default function Projects() {
  const projects = useResource(['projects'], () => listHostedProjects());
  const list = projects.data ?? [];
  return (
    <div className="page">
      <div>
        <h1 className="m-0">Projects</h1>
        <p className="page__lede text-sm mt-1">Pick a project to review.</p>
      </div>
      <QueryState isPending={projects.isPending} error={projects.error} onRetry={projects.refetch} isEmpty={list.length === 0}
        empty="You are not on any project's team yet. Ask an admin to add you.">
        <ul className="type-grid">
          {list.map((p) => (
            <li key={p.slug}>
              <Link to={projectPath(p.slug)} className="type-card">
                <h3 className="m-0">{p.name}</h3>
                <p className="type-card__help">
                  {p.role === 'curator' ? 'You vote and decide' : p.role === 'voter' ? 'You vote' : 'Not on the team'}
                  {p.synced_at ? ` · synced ${p.synced_at.slice(0, 10)}` : ' · not synced yet'}
                </p>
              </Link>
            </li>
          ))}
        </ul>
      </QueryState>
    </div>
  );
}
