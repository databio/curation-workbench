// The whole ranked queue (open, stale or unreviewed subjects, highest score
// first) with filters; each row links to its subject and its review types.
// Port of the first project's Astro queue/all.astro.
import { useMemo, useState } from 'react';
import { Link } from 'react-router';
import { useBackend } from '../api/backend';
import type { ReviewIndex, SubjectIndex } from '../api/types';
import { LazyList } from '../components/LazyList';
import { QueryState } from '../components/QueryState';
import { StateChip } from '../components/StateChip';
import { nameOf, SubjectLink } from '../components/SubjectLink';
import { useReview, useSubjectIndex } from '../hooks/useData';
import { projectPath, typePath } from '../lib/paths';

const QUEUE_STATES = new Set(['open', 'stale', 'unreviewed']);

export interface QueueRow {
  sid: string;
  kind: string;
  state: string;
  score: number;
  layer: number | null;
  container: string | null;
  types: { track: string; type: string; title: string }[];
}

export function queueRows(index: SubjectIndex, review: ReviewIndex | undefined): QueueRow[] {
  const types = new Map<string, QueueRow['types']>();
  for (const t of review?.tracks ?? []) {
    for (const ty of t.types) {
      for (const sid of ty.sids ?? []) types.set(sid, [...(types.get(sid) ?? []), { track: t.id, type: ty.id, title: `${t.title}: ${ty.title}` }]);
    }
  }
  return Object.entries(index)
    .filter(([, r]) => !r.retired && QUEUE_STATES.has(r.state))
    .map(([sid, r]) => ({ sid, kind: r.kind, state: r.state, score: r.score, layer: r.layer, container: r.container, types: types.get(sid) ?? [] }))
    .sort((a, b) => b.score - a.score || (a.sid < b.sid ? -1 : 1));
}

export default function AllItems() {
  const { project } = useBackend();
  const index = useSubjectIndex();
  const review = useReview();
  const [f, setF] = useState({ track: '', type: '', kind: '', state: '', q: '' });
  const rows = useMemo(() => (index.data ? queueRows(index.data, review.data) : []), [index.data, review.data]);
  const tracks = review.data?.tracks ?? [];
  const typeOptions = tracks.filter((t) => !f.track || t.id === f.track).flatMap((t) => t.types.map((ty) => [`${t.id}/${ty.id}`, `${t.title}: ${ty.title}`]));
  const kinds = [...new Set(rows.map((r) => r.kind))].sort();
  const states = [...new Set(rows.map((r) => r.state))].sort();
  const q = f.q.trim().toLowerCase();
  const shown = rows.filter((r) => (!f.track || r.types.some((t) => t.track === f.track))
    && (!f.type || r.types.some((t) => `${t.track}/${t.type}` === f.type))
    && (!f.kind || r.kind === f.kind) && (!f.state || r.state === f.state)
    && (!q || `${r.sid} ${nameOf(r.sid, index.data)} ${r.container ?? ''}`.toLowerCase().includes(q)));
  const set = (k: keyof typeof f) => (e: React.ChangeEvent<HTMLSelectElement | HTMLInputElement>) => setF((x) => ({ ...x, [k]: e.target.value }));

  return (
    <div className="page">
      <div>
        <nav className="crumbs"><Link to={projectPath(project.slug)}>Review</Link> › All items</nav>
        <h1 className="m-0">All items</h1>
        <p className="page__lede text-sm mt-1 tabular">
          Every item still waiting, most urgent first. <span data-testid="count">Showing {shown.length} of {rows.length}.</span>
        </p>
      </div>
      <form className="filters card" onSubmit={(e) => e.preventDefault()}>
        <label className="filters__field"><span className="muted text-xs">Track</span>
          <select className="form__input" aria-label="Track" value={f.track} onChange={set('track')}>
            <option value="">All</option>
            {tracks.map((t) => <option key={t.id} value={t.id}>{t.title}</option>)}
          </select>
        </label>
        <label className="filters__field"><span className="muted text-xs">Type</span>
          <select className="form__input" aria-label="Type" value={f.type} onChange={set('type')}>
            <option value="">All</option>
            {typeOptions.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
          </select>
        </label>
        <label className="filters__field"><span className="muted text-xs">Kind</span>
          <select className="form__input" aria-label="Kind" value={f.kind} onChange={set('kind')}>
            <option value="">All</option>
            {kinds.map((k) => <option key={k} value={k}>{k}</option>)}
          </select>
        </label>
        <label className="filters__field"><span className="muted text-xs">Status</span>
          <select className="form__input" aria-label="State" value={f.state} onChange={set('state')}>
            <option value="">All</option>
            {states.map((s) => <option key={s} value={s}>{s}</option>)}
          </select>
        </label>
        <label className="filters__field filters__field--grow"><span className="muted text-xs">Search</span>
          <input type="search" className="form__input" aria-label="Search" value={f.q} onChange={set('q')} />
        </label>
      </form>
      <QueryState isPending={index.isPending} error={index.error} onRetry={index.refetch} isEmpty={!!index.data && shown.length === 0}
        empty="No subjects match these filters.">
        <LazyList items={shown} keyOf={(r) => r.sid} resetKey={JSON.stringify(f)} className="queue-list"
          render={(r) => (
            <li className="card queue-row">
              <div className="flex flex-wrap items-start justify-between gap-3">
                <div className="min-w-0">
                  <div className="flex flex-wrap items-center gap-2">
                    <span title={r.sid}><SubjectLink sid={r.sid} index={index.data} className="font-semibold" /></span>
                    {r.state === 'stale' ? <StateChip sid={r.sid} index={index.data} /> : null}
                  </div>
                  <div className="muted text-xs mt-1">
                    {r.kind}{r.layer !== null ? ` · layer ${r.layer}` : ''}
                    {r.container ? <> · <SubjectLink sid={r.container} index={index.data} /></> : null}
                  </div>
                  {r.types.length ? (
                    <div className="mt-1 text-xs">
                      {r.types.map((t, i) => (
                        <span key={`${t.track}/${t.type}`}>
                          {i > 0 ? ' · ' : ''}
                          <Link to={typePath(project.slug, t.track, t.type)}>{t.title}</Link>
                        </span>
                      ))}
                    </div>
                  ) : null}
                </div>
                <div className="text-right tabular" title="Queue priority: higher comes first">
                  <div className="text-lg font-semibold">{r.score.toFixed(2)}</div>
                  <div className="muted text-xs">priority</div>
                </div>
              </div>
            </li>
          )} />
      </QueryState>
    </div>
  );
}
