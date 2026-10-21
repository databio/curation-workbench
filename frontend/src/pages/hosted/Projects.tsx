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
      <h1 className="m-0">Projects</h1>
      <QueryState isPending={projects.isPending} error={projects.error} onRetry={projects.refetch} isEmpty={list.length === 0}
        empty="You are not on any project's team yet. Ask an admin to add you.">
        <ul className="type-grid">
          {list.map((p) => (
            <li key={p.slug}>
              <Link to={projectPath(p.slug)} className="type-card">
                <h3 className="m-0">{p.name}</h3>
                <p className="type-card__help">
                  {p.role ?? 'not on the team'}{p.commit ? ` · commit ${p.commit.slice(0, 8)}` : ' · not synced yet'}
                </p>
              </Link>
            </li>
          ))}
        </ul>
      </QueryState>
    </div>
  );
}
