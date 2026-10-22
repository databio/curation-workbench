// Hosted home: the projects you are on (all of them for a superadmin). A
// superadmin also sees each project's GitHub repo and can add a project.
import { useState } from 'react';
import { Link } from 'react-router';
import { saveErrorMessage } from '../../api/backend';
import { adminCreateProject, adminListProjects, adminSyncProject, listHostedProjects, type NewProject } from '../../api/hosted';
import { QueryState } from '../../components/QueryState';
import { useInvalidateResources, useResource } from '../../hooks/useResource';
import { projectPath } from '../../lib/paths';
import { useHostedSession } from './session';

export default function Projects() {
  const { me } = useHostedSession();
  const superadmin = me?.site_role === 'superadmin';
  const projects = useResource(['projects'], () => listHostedProjects());
  const list = projects.data ?? [];
  return (
    <div className="page">
      <div>
        <h1 className="m-0">Projects</h1>
        <p className="page__lede text-sm mt-1">Pick a project to review.</p>
      </div>
      <QueryState isPending={projects.isPending} error={projects.error} onRetry={projects.refetch} isEmpty={list.length === 0}
        empty={superadmin ? 'No projects yet. Add one below.' : "You are not on any project's team yet. Ask an admin to add you."}>
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
      {superadmin ? <AdminProjects /> : null}
    </div>
  );
}

const EMPTY: NewProject = { slug: '', name: '', repo: '', branch: 'master' };

/** Superadmin: each project's repo and last fetch, "Fetch now", and the add form. */
function AdminProjects() {
  const invalidate = useInvalidateResources();
  const rows = useResource(['adminProjects'], () => adminListProjects());
  const [form, setForm] = useState<NewProject>(EMPTY);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<{ error: boolean; text: string } | null>(null);
  const set = (k: keyof NewProject) => (e: React.ChangeEvent<HTMLInputElement>) => setForm({ ...form, [k]: e.target.value });
  const act = async (fn: () => Promise<{ slug: string; last_error: string | null }>, done: string) => {
    setBusy(true);
    try {
      const p = await fn();
      setMsg(p.last_error ? { error: true, text: `${p.slug}: ${p.last_error}` } : { error: false, text: done });
      invalidate(['adminProjects']);
      invalidate(['projects']);
      return true;
    } catch (e) {
      setMsg({ error: true, text: saveErrorMessage(e) });
      return false;
    } finally {
      setBusy(false);
    }
  };
  const live = (rows.data ?? []).filter((p) => !p.archived_at);

  return (
    <>
      <h2 className="mb-0">Project repositories</h2>
      {msg ? <div className={msg.error ? 'banner banner--error' : 'banner'} role={msg.error ? 'alert' : 'status'}>{msg.text}</div> : null}
      <QueryState isPending={rows.isPending} error={rows.error} onRetry={rows.refetch} isEmpty={live.length === 0} empty="No projects yet.">
        <div className="card p-0 overflow-x-auto">
          <table className="table">
            <thead>
              <tr><th>Project</th><th>Repository</th><th>Commit</th><th>Last check</th><th /></tr>
            </thead>
            <tbody>
              {live.map((p) => (
                <tr key={p.slug}>
                  <td>{p.name}</td>
                  <td>{p.repo ? `${p.repo} (${p.branch})` : 'none'}</td>
                  <td>{p.commit ? p.commit.slice(0, 10) : '-'}</td>
                  <td>{p.last_error ? <span className="repo-error">{p.last_error}</span> : p.last_checked_at ? p.last_checked_at.slice(0, 16).replace('T', ' ') : '-'}</td>
                  <td>
                    {p.repo && !p.repo.startsWith('dev:') ? (
                      <button type="button" className="btn btn--sm" disabled={busy}
                        onClick={() => void act(() => adminSyncProject(p.slug), `${p.name} is up to date.`)}>
                        Fetch now
                      </button>
                    ) : null}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </QueryState>
      <form className="card form" onSubmit={(e) => {
        e.preventDefault();
        void act(() => adminCreateProject(form), `Added ${form.name}. Put people on its team from the project's Team page.`)
          .then((ok) => ok && setForm(EMPTY));
      }}>
        <h3 className="m-0">Add a project</h3>
        <p className="text-sm m-0">The repository must be public on GitHub and commit its snapshot file (<code>snapshot:</code> in its workbench.yaml).</p>
        <div className="form__row">
          <label className="form__label" htmlFor="np-slug">Short name</label>
          <input id="np-slug" className="form__input" required pattern="[a-z0-9][a-z0-9-]{1,40}" placeholder="my-schema" value={form.slug} onChange={set('slug')} />
        </div>
        <div className="form__row">
          <label className="form__label" htmlFor="np-name">Name</label>
          <input id="np-name" className="form__input" required placeholder="My schema" value={form.name} onChange={set('name')} />
        </div>
        <div className="form__row">
          <label className="form__label" htmlFor="np-repo">GitHub repository</label>
          <input id="np-repo" className="form__input" required placeholder="https://github.com/owner/repo" value={form.repo} onChange={set('repo')} />
        </div>
        <div className="form__row">
          <label className="form__label" htmlFor="np-branch">Branch</label>
          <input id="np-branch" className="form__input" required value={form.branch} onChange={set('branch')} />
        </div>
        <div>
          <button type="submit" className="btn btn--primary" disabled={busy}>Add project</button>
        </div>
      </form>
    </>
  );
}
