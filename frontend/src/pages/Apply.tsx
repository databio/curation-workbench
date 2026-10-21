// Pending decisions grouped by verdict, with what a dry run says applying
// them would do. Local mode adds Dry run / Apply all, streamed from the
// daemon; a reload during an apply follows the daemon's copy of the log.
// Hosted mode is read-only (decided, waiting to reach the repository).
import { useEffect, useRef, useState } from 'react';
import { Link } from 'react-router';
import { CurationError, useBackend } from '../api/backend';
import type { PendingDecision } from '../api/types';
import { ConfirmDialog } from '../components/ConfirmDialog';
import { QueryState } from '../components/QueryState';
import { SubjectLink } from '../components/SubjectLink';
import { OperationSummary } from '../components/ReviewItem';
import { usePending, useRebuilt, useSubjectIndex, useVerdicts } from '../hooks/useData';
import { kindOfSid, verdictLabel } from '../lib/draft';
import { projectPath } from '../lib/paths';
import { sessionGet, sessionSet } from '../lib/session';

const DIFF_PAGE = 400;

export function lineClass(l: string): string {
  if (l.startsWith('+++') || l.startsWith('---')) return 'diff__line diff__line--file';
  if (l.startsWith('+')) return 'diff__line diff__line--add';
  if (l.startsWith('-')) return 'diff__line diff__line--del';
  if (l.startsWith('@@')) return 'diff__line diff__line--hunk';
  return 'diff__line';
}

function Diff({ path, diff, open }: { path: string; diff: string; open: boolean }) {
  const [all, setAll] = useState(false);
  const lines = diff.split('\n');
  const shown = all ? lines : lines.slice(0, DIFF_PAGE);
  return (
    <details className="card diff" open={open}>
      <summary className="font-mono text-sm cursor-pointer">{path}</summary>
      <pre className="diff__body">
        {shown.map((l, i) => (
          <div key={i} className={lineClass(l)}>
            {l || ' '}
          </div>
        ))}
      </pre>
      {!all && lines.length > DIFF_PAGE ? (
        <button type="button" className="btn btn--sm" onClick={() => setAll(true)}>
          Show all {lines.length} lines
        </button>
      ) : null}
    </details>
  );
}

function Effects({ d }: { d: PendingDecision }) {
  const e = d.effects;
  if (!e || !(e.created?.length || e.retired?.length || Object.keys(e.renamed || {}).length)) return null;
  return (
    <div className="muted text-xs mt-1">
      {e.created?.length ? <span>creates <span className="font-mono">{e.created.join(', ')}</span>. </span> : null}
      {e.retired?.length ? <span>retires <span className="font-mono">{e.retired.join(', ')}</span>. </span> : null}
      {Object.keys(e.renamed || {}).length ? (
        <span>
          renames <span className="font-mono">{Object.entries(e.renamed!).map(([a, b]) => `${a} → ${b}`).join(', ')}</span>.
        </span>
      ) : null}
    </div>
  );
}

export default function Apply() {
  const backend = useBackend();
  const { project, can } = backend;
  const pending = usePending();
  const index = useSubjectIndex();
  const verdicts = useVerdicts();
  const rebuilt = useRebuilt();
  const [log, setLog] = useState<string | null>(null);
  const [running, setRunning] = useState(false);
  const [confirm, setConfirm] = useState(false);
  const logRef = useRef<HTMLPreElement>(null);
  const WATCH = `cwb:${project.slug}:apply-watch`;

  const doneNote = (code: number | null, dryRun: boolean) => {
    if (code !== 0) return `\n[failed, exit ${code}] Nothing was changed.`;
    return dryRun
      ? '\n[done] Preview finished; nothing was written. The changes it would make are listed below.'
      : '\n[done] Applied. Nothing is committed yet: look over the changes and commit them.';
  };

  // After a reload mid-apply, follow the run through the daemon's log.
  useEffect(() => {
    if (!can.apply || !backend.applyStatus || sessionGet(WATCH) !== '1') return;
    let stop = false;
    (async () => {
      for (;;) {
        const run = await backend.applyStatus!().catch(() => null);
        if (!run || stop) return;
        setLog(run.log.join('\n') + (run.running ? '\n[still running…]' : doneNote(run.exit, run.dry_run)));
        if (!run.running) {
          sessionSet(WATCH, null);
          rebuilt();
          return;
        }
        await new Promise((r) => setTimeout(r, 1000));
      }
    })();
    return () => {
      stop = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  async function run(dryRun: boolean) {
    setRunning(true);
    setLog('');
    if (!dryRun) sessionSet(WATCH, '1');
    let text = '';
    try {
      const code = await backend.apply!({
        dryRun,
        onLine: (l) => {
          text += `${l}\n`;
          setLog(text);
          if (logRef.current) logRef.current.scrollTop = logRef.current.scrollHeight;
        },
      });
      sessionSet(WATCH, null);
      setLog(text + doneNote(code, dryRun));
    } catch (e) {
      const msg = e instanceof CurationError ? `${e.status}: ${e.message}` : String(e);
      setLog(`${text}\n[error] ${msg}`);
    } finally {
      setRunning(false);
      if (!dryRun) rebuilt();
    }
  }

  const p = pending.data;
  const groups = new Map<string, PendingDecision[]>();
  for (const d of p?.decisions ?? []) {
    const label = verdictLabel(d.verdict, kindOfSid(d.subject), verdicts.data);
    groups.set(label, [...(groups.get(label) ?? []), d]);
  }
  const errors = Object.entries(p?.errors ?? {});
  const files = p?.files ?? [];

  return (
    <div className="page">
      <div className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <h1 className="m-0">{can.apply ? 'Apply' : 'Decided, waiting to reach the repository'}</h1>
          {p ? (
            <p className="page__lede text-sm mt-1">
              {p.decisions.length} pending decision{p.decisions.length === 1 ? '' : 's'}.
              {can.apply ? ' Applying runs all of them together, all or nothing.' : ''}
            </p>
          ) : null}
        </div>
        {can.apply ? (
          <div className="toolbar">
            <button type="button" className="btn" disabled={running} title="A dry run: shows what would change, writes nothing"
              onClick={() => run(true)}>
              Preview changes
            </button>
            <button type="button" className="btn btn--primary" disabled={running || !p?.decisions.length}
              title="Edit the schema files for every pending decision" onClick={() => setConfirm(true)}>
              Apply all{p?.decisions.length ? ` (${p.decisions.length})` : ''}
            </button>
          </div>
        ) : can.export && backend.exportUrl ? (
          <a className="btn" href={backend.exportUrl}>
            Export
          </a>
        ) : null}
      </div>

      {can.apply ? (
        <section className="card text-sm apply-explainer" data-testid="apply-explainer">
          <h2 className="card__title">What applying does</h2>
          <p className="mt-0">
            A decision you make in Review is saved as <strong>pending</strong>: it is recorded, but the schema has not
            changed yet. Applying turns pending decisions into real edits:
          </p>
          <ol>
            <li>It edits the schema and mapping files the way each decision says (rename a term, add a link, make a field optional, ...).</li>
            <li>It regenerates every file built from them and runs the project's checks.</li>
            <li>It marks those decisions <strong>applied</strong>.</li>
          </ol>
          <p className="mb-0">
            It is all or nothing: if any step fails, every file is put back as it was. It does not commit or push.
            Afterwards, look over the changes (for example with <code>git diff</code>) and commit them.{' '}
            <strong>Preview changes</strong> (a dry run) shows the same changes without writing anything; the diff below
            is that preview.
          </p>
        </section>
      ) : (
        <section className="card text-sm apply-explainer" data-testid="apply-explainer">
          <p className="m-0">
            These decisions were made by curators here, but they have not changed the schema yet. The schema lives in the
            project's git repository; decisions reach it when they are exported and applied there.
          </p>
        </section>
      )}

      {log !== null ? (
        <pre ref={logRef} className="apply-log" data-testid="apply-log">
          {log}
        </pre>
      ) : null}

      <QueryState isPending={pending.isPending} error={pending.error} onRetry={pending.refetch}
        isEmpty={!!p && p.decisions.length === 0}
        empty={<>No pending decisions. Make some from <Link to={projectPath(project.slug)}>Review</Link>.</>}>
        {errors.length > 0 ? (
          <div className="banner banner--error text-sm">
            <div className="font-semibold mb-1">The dry run failed. Nothing can be applied until these are fixed:</div>
            <ul className="m-0">
              {errors.map(([id, reasons]) => (
                <li key={id}>
                  <span className="font-mono">{id}</span>: {Array.isArray(reasons) ? reasons.join('; ') : String(reasons)}
                </li>
              ))}
            </ul>
          </div>
        ) : null}
        <div className="stack">
          {[...groups.entries()].sort((a, b) => a[0].localeCompare(b[0])).map(([label, list]) => (
            <section key={label} className="card">
              <h2 className="card__title" title={list[0].verdict}>
                {label} <span className="muted text-sm">{list.length}</span>
              </h2>
              <ul className="pending-list">
                {list.map((d) => (
                  <li key={d.id} className="pending-entry">
                    <div className="flex flex-wrap items-center gap-2">
                      <span title={d.subject}><SubjectLink sid={d.subject} index={index.data} className="font-semibold" /></span>
                      <span className="muted text-xs">{kindOfSid(d.subject)}</span>
                      <span className="muted text-xs">
                        {d.decided_by} · {d.decided_on} · <span className="font-mono">{d.id}</span>
                      </span>
                    </div>
                    <OperationSummary op={d.operation} className="block text-xs mt-1" />
                    <p className="mt-1 mb-0">{d.rationale}</p>
                    <Effects d={d} />
                  </li>
                ))}
              </ul>
            </section>
          ))}
        </div>
        {files.length > 0 ? (
          <section className="stack">
            <h2 className="m-0">
              Diff <span className="muted text-sm">{files.length} file{files.length === 1 ? '' : 's'}</span>
            </h2>
            {files.map((f) => <Diff key={f.path} path={f.path} diff={f.diff} open={files.length <= 5} />)}
          </section>
        ) : null}
      </QueryState>

      <ConfirmDialog
        isOpen={confirm}
        title="Apply all"
        message={`Edit the schema files for all ${p?.decisions.length ?? 0} pending decisions? Nothing is committed: you look over the changes and commit them afterwards.`}
        confirmLabel="Apply all"
        onCancel={() => setConfirm(false)}
        onConfirm={async () => {
          setConfirm(false);
          await run(false);
        }}
      />
    </div>
  );
}
