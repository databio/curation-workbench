// Approved decisions waiting to reach the project's repository. The site never
// edits the repository: a GitHub Action in the repo (or `cwb pull` on a
// laptop) fetches these with a read-only key, applies them and opens a pull
// request. The list comes first; how it works sits under a closed <details>.
import { useState, type ReactNode } from 'react';
import { Link } from 'react-router';
import { useBackend } from '../api/backend';
import type { Decision, PendingDecision } from '../api/types';
import { QueryState } from '../components/QueryState';
import { SubjectLink } from '../components/SubjectLink';
import { OperationSummary, personName } from '../components/ReviewItem';
import { usePending, useSubjectIndex, useVerdicts } from '../hooks/useData';
import { kindOfSid, verdictLabel } from '../lib/draft';
import { projectPath } from '../lib/paths';

/** Why a decision will not be sent yet, in plain words (null: it will be). */
export function heldReason(d: Decision, errors: Record<string, string[] | string>): string | null {
  if (d.stale) return 'Held back: the item changed since this was decided. A curator should decide again.';
  const errs = errors[d.id];
  if (errs && (!Array.isArray(errs) || errs.length)) {
    return `Held back: it fails the project's checks (${Array.isArray(errs) ? errs.join('; ') : errs}).`;
  }
  return null;
}

function RepoKey() {
  const backend = useBackend();
  const [key, setKey] = useState<string | null>(null);
  const [msg, setMsg] = useState('');
  if (!backend.makeRepoKey) return null;
  return (
    <div className="mt-2" data-testid="repo-key">
      {key ? (
        <>
          <p className="m-0">
            Copy this key now; it is shown only once. In the repository on GitHub, add it under Settings → Secrets
            and variables → Actions as <code>CURATE_API_KEY</code>.
          </p>
          <pre className="apply-log">{key}</pre>
        </>
      ) : (
        <button type="button" className="btn btn--sm" onClick={async () => {
          try {
            setKey(await backend.makeRepoKey!());
          } catch (e) {
            setMsg(`No key made: ${(e as Error).message}`);
          }
        }}>
          Make a key for the repository
        </button>
      )}
      {msg ? <p className="review-item__msg" role="status">{msg}</p> : null}
    </div>
  );
}

export default function Approved() {
  const { project } = useBackend();
  const pending = usePending();
  const index = useSubjectIndex();
  const verdicts = useVerdicts();
  const p = pending.data;
  const repo = p?.repository;
  const errors = p?.errors ?? {};

  const row = (d: PendingDecision | Decision, extra: ReactNode) => (
    <li key={d.id} className="pending-entry">
      <div className="flex flex-wrap items-center gap-2">
        <span title={d.subject}><SubjectLink sid={d.subject} index={index.data} className="font-semibold" /></span>
        <span title={d.verdict}>{verdictLabel(d.verdict, kindOfSid(d.subject), verdicts.data)}</span>
        <span className="muted text-xs" title={`${d.id} · ${d.decided_by}`}>{personName(d.decided_by)} · {d.decided_on}</span>
      </div>
      <OperationSummary op={d.operation} className="block text-xs mt-1" />
      {extra}
    </li>
  );

  return (
    <div className="page">
      <div>
        <h1 className="m-0">Approved</h1>
        {p ? (
          <p className="page__lede text-sm mt-1 tabular">
            {p.decisions.length} waiting to reach the repository.
          </p>
        ) : null}
      </div>

      <QueryState isPending={pending.isPending} error={pending.error} onRetry={pending.refetch}
        isEmpty={!!p && p.decisions.length === 0 && !(p.applied ?? []).length}
        empty={<>Nothing approved yet. Votes become decisions on the <Link to={projectPath(project.slug)}>Review</Link> pages.</>}>
        {p && p.decisions.length > 0 ? (
          <ul className="card pending-list" data-testid="waiting">
            {p.decisions.map((d) => {
              const held = heldReason(d, errors);
              return row(d, held ? <p className="review-item__stale mt-1 mb-0">{held}</p> : null);
            })}
          </ul>
        ) : null}
        {p && (p.applied ?? []).length > 0 ? (
          <section className="stack" data-testid="reached">
            <h2 className="m-0">Reached the repository</h2>
            <ul className="card pending-list">
              {(p.applied ?? []).map((d) => row(d, (
                <p className="muted text-xs mt-1 mb-0">
                  Now <span className="font-mono">{d.applied?.repo_id ?? d.id}</span>
                  {d.applied?.on ? <> · {d.applied.on}</> : null}
                </p>
              )))}
            </ul>
          </section>
        ) : null}
      </QueryState>

      <details className="card details text-sm" data-testid="apply-explainer">
        <summary className="details__summary">How decisions reach the repository</summary>
        <p>
          The schema lives in the project's git repository; this site never edits it.
        </p>
        <ol className="details__steps">
          <li>
            The repository's <strong>curate</strong> GitHub Action fetches the approved decisions (daily, or when
            someone runs it), applies them, runs the project's checks, and opens a pull request.
            {repo?.workflow_url ? <> <a href={repo.workflow_url} target="_blank" rel="noreferrer">See its runs ↗</a></> : null}
          </li>
          <li>Someone reviews and merges the pull request.</li>
          <li>This site sees the new commit and moves the decisions to "Reached the repository".</li>
        </ol>
        <p className="mb-0">
          On a laptop, <code>cwb pull {project.slug}</code> in a checkout does step 1 without the pull request.
          {repo?.commit ? (
            <>
              {' '}Last synced commit:{' '}
              {repo.commit_url ? (
                <a href={repo.commit_url} target="_blank" rel="noreferrer"><code>{repo.commit.slice(0, 10)}</code></a>
              ) : <code>{repo.commit.slice(0, 10)}</code>}
              {repo.branch ? <> on <code>{repo.branch}</code></> : null}.
            </>
          ) : null}
        </p>
        <RepoKey />
      </details>
    </div>
  );
}
