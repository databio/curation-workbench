// Hosted, superadmin only: who is on this project's curation team, and as
// what (voter or curator). The server enforces every rule; this page is
// hidden unless can.adminTeam.
import { useState } from 'react';
import { Navigate } from 'react-router';
import { saveErrorMessage, useBackend } from '../api/backend';
import type { TeamMember } from '../api/types';
import { ConfirmDialog } from '../components/ConfirmDialog';
import { QueryState } from '../components/QueryState';
import { useInvalidateResources, useResource } from '../hooks/useResource';
import { projectPath } from '../lib/paths';

export default function Team() {
  const backend = useBackend();
  const { project, can } = backend;
  const invalidate = useInvalidateResources();
  const team = useResource([project.slug, 'team'], () => backend.team!(), { enabled: can.adminTeam && !!backend.team });
  const users = useResource([project.slug, 'approvedUsers'], () => backend.approvedUsers!(), {
    enabled: can.adminTeam && !!backend.approvedUsers,
  });
  const [removing, setRemoving] = useState<TeamMember | null>(null);
  const [adding, setAdding] = useState('');
  const [role, setRole] = useState<'voter' | 'curator'>('voter');
  const [msg, setMsg] = useState('');

  if (!can.adminTeam) return <Navigate to={projectPath(project.slug)} replace />;

  const members = team.data ?? [];
  const candidates = (users.data ?? []).filter((u) => !members.some((m) => m.user_id === u.id));
  const refresh = () => invalidate([project.slug, 'team']);
  const act = async (fn: () => Promise<void>) => {
    try {
      await fn();
      setMsg('');
      refresh();
    } catch (e) {
      setMsg(saveErrorMessage(e));
    }
  };

  return (
    <div className="page">
      <div>
        <h1 className="m-0">Team</h1>
        <p className="page__lede text-sm mt-1">Voters answer review questions; curators also turn votes into decisions.</p>
      </div>
      {msg ? <div className="banner banner--error" role="alert">{msg}</div> : null}
      <QueryState isPending={team.isPending} error={team.error} onRetry={team.refetch} isEmpty={members.length === 0} empty="No one is on this team yet.">
        <div className="card p-0 overflow-x-auto">
          <table className="table">
            <thead>
              <tr><th>Name</th><th>Email</th><th>Role</th><th /></tr>
            </thead>
            <tbody>
              {members.map((m) => (
                <tr key={m.user_id}>
                  <td>{m.name}</td>
                  <td>{m.email}</td>
                  <td>
                    <select className="form__input" aria-label={`Role of ${m.name}`} value={m.role}
                      onChange={(e) => act(() => backend.setMember!(m.user_id, e.target.value as 'voter' | 'curator'))}>
                      <option value="voter">voter</option>
                      <option value="curator">curator</option>
                    </select>
                  </td>
                  <td>
                    <button type="button" className="btn btn--sm" onClick={() => setRemoving(m)}>
                      Remove
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </QueryState>
      <form className="card toolbar" onSubmit={(e) => {
        e.preventDefault();
        if (adding) void act(() => backend.setMember!(Number(adding), role)).then(() => setAdding(''));
      }}>
        <span className="font-semibold">Add member</span>
        <select className="form__input" aria-label="Person" value={adding} onChange={(e) => setAdding(e.target.value)}>
          <option value="">Choose an approved account…</option>
          {candidates.map((u) => <option key={u.id} value={u.id}>{u.name} ({u.email})</option>)}
        </select>
        <select className="form__input" aria-label="New role" value={role} onChange={(e) => setRole(e.target.value as 'voter' | 'curator')}>
          <option value="voter">voter</option>
          <option value="curator">curator</option>
        </select>
        <button type="submit" className="btn btn--primary btn--sm" disabled={!adding}>
          Add
        </button>
      </form>
      <ConfirmDialog
        isOpen={!!removing}
        title="Remove from team"
        message={removing ? `Remove ${removing.name} from this project's team? Their votes stay but stop counting.` : ''}
        confirmLabel="Remove"
        destructive
        onCancel={() => setRemoving(null)}
        onConfirm={async () => {
          const m = removing!;
          setRemoving(null);
          await act(() => backend.removeMember!(m.user_id));
        }}
      />
    </div>
  );
}
