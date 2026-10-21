// One review type's page: breadcrumb, title, question, then every item of
// that type with ready-made answers (ReviewItem); each click is a vote. Items
// with a `group` (sign-off) render under group headings with "Approve all".
import { useCallback, useMemo, useState } from 'react';
import { Link, useParams } from 'react-router';
import { saveErrorMessage, useBackend } from '../api/backend';
import type { Answer, Item } from '../api/types';
import { ConfirmDialog } from '../components/ConfirmDialog';
import { LazyList } from '../components/LazyList';
import { QueryState } from '../components/QueryState';
import { ReviewItem, rowDone, type RowState } from '../components/ReviewItem';
import { useChanged, useDecisions, useQuestions, useReviewType, useSubjectIndex } from '../hooks/useData';
import { buildDraft } from '../lib/draft';
import { projectPath } from '../lib/paths';
import { useSessionValue } from '../lib/session';

export function matches(item: Item, q: string): boolean {
  if (!q) return true;
  const hay = [item.title, item.container_label ?? '', item.container ?? '', item.description, ...item.reasons,
    ...item.choices.map((c) => c.text)].join('\n').toLowerCase();
  return hay.includes(q.toLowerCase().trim());
}

/** The sign-off answer "Approve all" saves: a primary `keep`. */
const approveAction = (item: Item) => item.actions.find((a) => a.verdict === 'keep' && a.tone === 'primary');

export default function ReviewType() {
  const { track = '', type = '' } = useParams();
  const backend = useBackend();
  const { project, can } = backend;
  const reviewType = `${track}/${type}`;
  const page = useReviewType(track, type);
  const index = useSubjectIndex();
  const decisions = useDecisions();
  const questions = useQuestions(reviewType);
  const changed = useChanged();
  const [hideDone, setHideDone] = useSessionValue(`cwb:${project.slug}:${reviewType}:hide`, true);
  const [search, setSearch] = useSessionValue(`cwb:${project.slug}:${reviewType}:q`, '');
  // Rows answered on this page stay in view (dimmed) so Undo stays in reach.
  const [touched, setTouched] = useState<Set<string>>(() => new Set());
  const [approving, setApproving] = useState<{ group: string; rows: Item[] } | null>(null);
  const [progress, setProgress] = useState('');

  const states = useMemo(() => {
    const out = new Map<string, RowState>();
    for (const it of page.data?.items ?? []) {
      const q = (questions.data ?? []).find((x) => x.sid === it.sid && x.review_type === reviewType) ?? null;
      const resolved = q?.decision_id ? (decisions.data ?? []).find((d) => d.id === q.decision_id) ?? null : null;
      out.set(it.sid, { question: q, mine: q?.mine ?? null, resolved });
    }
    return out;
  }, [page.data, decisions.data, questions.data, reviewType]);

  const onChanged = useCallback((sid: string, _a: Answer | null) => {
    setTouched((t) => new Set(t).add(sid));
    changed();
  }, [changed]);

  const items = page.data?.items ?? [];
  const isDone = (it: Item) => rowDone(states.get(it.sid) ?? {});
  const visible = items.filter((it) => matches(it, search) && (!hideDone || !isDone(it) || touched.has(it.sid)));
  const doneCount = items.filter(isDone).length;
  const grouped = items.some((it) => it.group);
  const groups: [string, Item[]][] = [];
  if (grouped) {
    const by = new Map<string, Item[]>();
    for (const it of visible) by.set(it.group ?? '', [...(by.get(it.group ?? '') ?? []), it]);
    groups.push(...[...by.entries()].sort((a, b) => b[1].length - a[1].length));
  } else {
    groups.push(['', visible]);
  }

  async function approveAll(rows: Item[]) {
    let n = 0;
    for (const it of rows) {
      const action = approveAction(it)!;
      const built = buildDraft(it, action, '', '', index.data);
      if ('error' in built) break;
      setProgress(`Approving ${n + 1} of ${rows.length}…`);
      try {
        await backend.answer({ sid: it.sid, reviewType, action, hash: it.hash }, built.draft);
      } catch (e) {
        setProgress(`Stopped after ${n} of ${rows.length}: ${saveErrorMessage(e)}`);
        changed();
        return;
      }
      n += 1;
      setTouched((t) => new Set(t).add(it.sid));
    }
    setProgress(`Approved ${n} of ${rows.length}.`);
    changed();
  }

  const answered = can.decide ? 'decided' : 'answered';
  const hideLabel = `Hide ${answered}`;
  const emptyText = items.length === 0
    ? 'No items of this kind right now.'
    : search.trim()
      ? 'Nothing here matches your search.'
      : `All ${items.length} are ${answered}. Untick "${hideLabel}" to see them.`;
  const approveLabel = approving ? approveAction(approving.rows[0])?.label ?? 'Looks right' : '';
  return (
    <QueryState isPending={page.isPending} error={page.error} onRetry={page.refetch}>
      {page.data ? (
        <div className="page">
          <div>
            <nav className="crumbs">
              <Link to={projectPath(project.slug)}>Review</Link> › {page.data.track.title}
            </nav>
            <h1 className="m-0">{page.data.type.title}</h1>
            {page.data.type.question ? <p className="text-lg mt-1 mb-0">{page.data.type.question}</p> : null}
            {page.data.type.help ? <p className="page__lede text-sm mt-1">{page.data.type.help}</p> : null}
            <div className="toolbar mt-3">
              <span className="muted tabular" data-testid="progress">
                {doneCount} of {items.length} {answered}
              </span>
              <label className="form__choice">
                <input type="checkbox" checked={hideDone} onChange={(e) => setHideDone(e.target.checked)} />{' '}
                {hideLabel}
              </label>
              <input type="search" className="form__input form__input--md" placeholder="Search…" aria-label="Search"
                value={search} onChange={(e) => setSearch(e.target.value)} />
            </div>
            {progress ? <p className="muted text-sm mb-0" role="status">{progress}</p> : null}
          </div>

          {visible.length === 0 ? (
            <div className="card query-state" data-testid="empty">{emptyText}</div>
          ) : (
            groups.map(([name, rows]) => {
              const undone = rows.filter((it) => !isDone(it) && approveAction(it));
              return (
                <section key={name || '_'} className="review-group">
                  {name ? (
                    <div className="review-group__head">
                      <h2 className="m-0">
                        {name} <span className="muted text-sm font-normal tabular">{rows.length}</span>
                      </h2>
                      {(can.decide || can.vote) && undone.length > 0 ? (
                        <button type="button" className="btn btn--sm" onClick={() => setApproving({ group: name, rows: undone })}>
                          Approve all {undone.length}
                        </button>
                      ) : null}
                    </div>
                  ) : null}
                  <LazyList
                    items={rows}
                    keyOf={(it) => it.sid}
                    resetKey={`${search}|${hideDone}`}
                    className="review-list"
                    render={(it) => (
                      <ReviewItem item={it} reviewType={reviewType} index={index.data} state={states.get(it.sid) ?? {}} onChanged={onChanged} />
                    )}
                  />
                </section>
              );
            })
          )}
          <ConfirmDialog
            isOpen={!!approving}
            title="Approve all"
            message={approving ? `Vote "${approveLabel}" on all ${approving.rows.length} items in ${approving.group}?` : ''}
            confirmLabel="Approve all"
            onCancel={() => setApproving(null)}
            onConfirm={async () => {
              const rows = approving!.rows;
              setApproving(null);
              await approveAll(rows);
            }}
          />
        </div>
      ) : null}
    </QueryState>
  );
}
