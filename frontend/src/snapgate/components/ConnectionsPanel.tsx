import { useCallback, useEffect, useMemo, useState } from 'react';
import { useSnapgate } from '../context';
import { AuthAPI } from '../api';
import { ConnectionInfo, ScopeGroupInfo } from '../types';
import { errorText, formatDate } from '../admin/format';

export interface ConnectionsPanelProps {
  /** Heading text. Default "Connected apps". */
  title?: string;
  /** Shown when nothing is connected. Default "No AI apps are connected." */
  emptyText?: string;
  /** Extra class on the outer element. */
  className?: string;
}

/**
 * "Connected apps": the AI apps (OAuth connections) on the signed-in
 * account. Each row can change the permissions it holds (checkboxes) or be revoked.
 * A fixed connection (the app's own command-line login) can only be revoked.
 *
 * Needs the backend's snapgate OAuth server (create_oauth_router).
 * Styling comes from snapgate.css (sg-connections* classes).
 */
export function ConnectionsPanel({
  title = 'Connected apps',
  emptyText = 'No AI apps are connected.',
  className,
}: ConnectionsPanelProps) {
  const { apiUrl } = useSnapgate();
  const api = useMemo(() => new AuthAPI(apiUrl), [apiUrl]);
  const [connections, setConnections] = useState<ConnectionInfo[] | null>(null);
  const [groups, setGroups] = useState<ScopeGroupInfo[]>([]);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const [list, vocab] = await Promise.all([api.listConnections(), api.getScopes()]);
      setConnections(list.connections);
      setGroups(vocab.groups);
      setError(null);
    } catch (e) {
      setError(errorText(e));
      setConnections((prev) => prev ?? []);
    }
  }, [api]);

  useEffect(() => {
    load();
  }, [load]);

  const replace = (updated: ConnectionInfo) =>
    setConnections((prev) => (prev || []).map((c) => (c.id === updated.id ? updated : c)));
  const remove = (id: number) =>
    setConnections((prev) => (prev || []).filter((c) => c.id !== id));

  return (
    <section className={['sg-connections', className].filter(Boolean).join(' ')}>
      <h3 className="sg-connections__title">{title}</h3>
      {error && (
        <p className="sg-connections__error" role="alert">
          {error}
        </p>
      )}
      {connections === null ? (
        <p className="sg-connections__empty">Loading…</p>
      ) : connections.length === 0 ? (
        <p className="sg-connections__empty">{emptyText}</p>
      ) : (
        <ul className="sg-connections__list">
          {connections.map((c) => (
            <ConnectionRow
              key={c.id}
              connection={c}
              groups={groups}
              api={api}
              onUpdated={replace}
              onRevoked={remove}
              onError={setError}
            />
          ))}
        </ul>
      )}
    </section>
  );
}

interface ConnectionRowProps {
  connection: ConnectionInfo;
  groups: ScopeGroupInfo[];
  api: AuthAPI;
  onUpdated: (c: ConnectionInfo) => void;
  onRevoked: (id: number) => void;
  onError: (message: string | null) => void;
}

function sameIds(a: string[], b: string[]): boolean {
  const set = new Set(a);
  return a.length === b.length && b.every((x) => set.has(x));
}

function ConnectionRow({ connection: c, groups, api, onUpdated, onRevoked, onError }: ConnectionRowProps) {
  const [choice, setChoice] = useState<string[]>(c.groups);
  const [busy, setBusy] = useState(false);
  const [confirming, setConfirming] = useState(false);

  useEffect(() => {
    setChoice(c.groups);
  }, [c.groups]);

  const unchanged = sameIds(choice, c.groups);
  const toggle = (id: string) =>
    setChoice((prev) => (prev.includes(id) ? prev.filter((x) => x !== id) : [...prev, id]));

  const save = async () => {
    if (choice.length === 0 || unchanged) return;
    setBusy(true);
    try {
      onUpdated(await api.updateConnection(c.id, choice));
      onError(null);
    } catch (e) {
      onError(errorText(e));
    } finally {
      setBusy(false);
    }
  };

  const revoke = async () => {
    setBusy(true);
    try {
      await api.revokeConnection(c.id);
      onError(null);
      onRevoked(c.id);
    } catch (e) {
      onError(errorText(e));
      setBusy(false);
      setConfirming(false);
    }
  };

  return (
    <li className="sg-connections__item">
      <div className="sg-connections__head">
        <span className="sg-connections__name">{c.client_name}</span>
        <span className="sg-connections__host">
          {c.fixed
            ? 'command-line sign-in'
            : c.client_host
              ? `described by ${c.client_host}`
              : 'registered app'}
        </span>
      </div>

      {!c.fixed && (
        <fieldset className="sg-connections__groups" disabled={busy}>
          <legend className="sg-connections__legend">What it may do</legend>
          {groups.map((g) => (
            <label key={g.id} className="sg-connections__group">
              <input type="checkbox" checked={choice.includes(g.id)} onChange={() => toggle(g.id)} />
              {g.label}
            </label>
          ))}
          <button
            type="button"
            className="sg-button sg-button--secondary sg-connections__save"
            onClick={save}
            disabled={busy || choice.length === 0 || unchanged}
          >
            Save
          </button>
        </fieldset>
      )}

      {c.fixed ? (
        <p className="sg-connections__dates">
          Signed in {formatDate(c.created_at)} · Last used{' '}
          {formatDate(c.last_refreshed_at ?? c.created_at)} · Lapses if unused by{' '}
          {formatDate(c.expires_at)}
        </p>
      ) : (
        <>
          <ul className="sg-connections__scopes">
            {c.scopes.map((s) => (
              <li key={s.name}>{s.description || s.name}</li>
            ))}
          </ul>

          <p className="sg-connections__dates">
            Connected {formatDate(c.created_at)} · Last active{' '}
            {formatDate(c.last_refreshed_at ?? c.created_at)}
          </p>
        </>
      )}

      <div className="sg-connections__actions">
        {confirming ? (
          <>
            <span className="sg-connections__confirm">Revoke {c.client_name}?</span>
            <button type="button" className="sg-button sg-connections__revoke" onClick={revoke} disabled={busy}>
              Revoke
            </button>
            <button
              type="button"
              className="sg-button sg-button--secondary"
              onClick={() => setConfirming(false)}
              disabled={busy}
            >
              Cancel
            </button>
          </>
        ) : (
          <button
            type="button"
            className="sg-button sg-button--secondary"
            onClick={() => setConfirming(true)}
            disabled={busy}
          >
            Revoke
          </button>
        )}
      </div>
    </li>
  );
}
