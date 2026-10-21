// Every decision, newest first, with filters (port of the first project's Astro log.astro).
import { useMemo, useState } from 'react';
import type { HistoryRow } from '../api/types';
import { LazyList } from '../components/LazyList';
import { QueryState } from '../components/QueryState';
import { SubjectLink } from '../components/SubjectLink';
import { OperationSummary } from '../components/ReviewItem';
import { useHistory, useSubjectIndex, useVerdicts } from '../hooks/useData';
import { verdictLabel } from '../lib/draft';

const KEYS = [
  ['kind', 'Kind', (r: HistoryRow) => r.kind],
  ['layer', 'Layer', (r: HistoryRow) => (r.layer === null || r.layer === undefined ? '' : String(r.layer))],
  ['verdict', 'Verdict', (r: HistoryRow) => r.verdict],
  ['origin', 'Origin', (r: HistoryRow) => r.origin?.kind ?? ''],
  ['status', 'Status', (r: HistoryRow) => r.status],
] as const;

export function filterHistory(rows: HistoryRow[], filters: Record<string, string>, q: string): HistoryRow[] {
  const needle = q.trim().toLowerCase();
  return rows.filter((r) => KEYS.every(([k, , get]) => !filters[k] || get(r) === filters[k])
    && (!needle || `${r.id} ${r.subject} ${r.now ?? ''} ${r.name} ${r.rationale ?? ''}`.toLowerCase().includes(needle)));
}

export default function History() {
  const history = useHistory();
  const index = useSubjectIndex();
  const verdicts = useVerdicts();
  const [filters, setFilters] = useState<Record<string, string>>({});
  const [q, setQ] = useState('');
  const rows = history.data ?? [];
  const options = useMemo(() => Object.fromEntries(KEYS.map(([k, , get]) => [k,
    [...new Set(rows.map(get))].filter(Boolean).sort((a, b) => (k === 'layer' ? Number(a) - Number(b) : a.localeCompare(b)))])), [rows]);
  const shown = filterHistory(rows, filters, q);

  return (
    <div className="page">
      <div>
        <h1 className="m-0">History</h1>
        <p className="page__lede text-sm mt-1">
          Every decision ever recorded on this project, newest first. <span data-testid="count">Showing {shown.length} of {rows.length}.</span>
        </p>
      </div>
      <form className="filters card" onSubmit={(e) => e.preventDefault()}>
        {KEYS.map(([k, label]) => (
          <label key={k} className="filters__field">
            <span className="muted text-xs">{label}</span>
            <select className={`form__input${k === 'verdict' ? ' form__input--md' : ''}`} aria-label={label} value={filters[k] ?? ''}
              onChange={(e) => setFilters((f) => ({ ...f, [k]: e.target.value }))}>
              <option value="">All</option>
              {(options[k] ?? []).map((o) => <option key={o} value={o}>{k === 'verdict' ? verdictLabel(o, null, verdicts.data) : o}</option>)}
            </select>
          </label>
        ))}
        <label className="filters__field filters__field--grow">
          <span className="muted text-xs">Search</span>
          <input type="search" className="form__input" aria-label="Search" placeholder="subject, id, rationale…" value={q}
            onChange={(e) => setQ(e.target.value)} />
        </label>
      </form>
      <QueryState isPending={history.isPending} error={history.error} onRetry={history.refetch}>
        <div className="card overflow-x-auto p-0">
          <table className="table">
            <thead>
              <tr><th>Id</th><th>Item</th><th>Decision</th><th>Status</th><th>Who / when</th><th>Why</th></tr>
            </thead>
            <LazyList as="tbody" columns={6} pageSize={100} items={shown} keyOf={(r) => r.id} resetKey={`${JSON.stringify(filters)}|${q}`}
              render={(r) => (
                <tr className="align-top">
                  <td className="font-mono nowrap">{r.id}</td>
                  <td>
                    <SubjectLink sid={r.now ?? r.subject} index={index.data}>{r.name}</SubjectLink>
                    <div className="font-mono text-xs muted">{r.subject}</div>
                    {r.now ? <div className="text-xs muted">now <span className="font-mono">{r.now}</span></div> : null}
                    {(r.also_affects || []).length > 0 ? (
                      <div className="text-xs muted">also <span className="font-mono">{r.also_affects.join(', ')}</span></div>
                    ) : null}
                  </td>
                  <td>
                    <span className="badge badge--neutral badge--wrap" title={r.verdict}>{verdictLabel(r.verdict, r.kind, verdicts.data)}</span>
                    <OperationSummary op={r.operation} className="block text-xs mt-1 op-code" />
                  </td>
                  <td><span className={`badge status-badge--${r.status}`}>{r.status}</span></td>
                  <td className="text-xs muted nowrap">{r.decided_by}<br />{r.decided_on}<br />{r.origin?.kind}</td>
                  <td>{r.rationale}</td>
                </tr>
              )} />
          </table>
        </div>
      </QueryState>
    </div>
  );
}
