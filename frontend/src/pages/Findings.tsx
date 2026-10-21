// Open findings grouped by rule, each rule expandable into its findings.
import { useMemo, useState } from 'react';
import type { Finding } from '../api/types';
import { LazyList } from '../components/LazyList';
import { QueryState } from '../components/QueryState';
import { SubjectLink } from '../components/SubjectLink';
import { useFindings, useSubjectIndex } from '../hooks/useData';

const SEVERITY_ORDER: Record<string, number> = { error: 0, warn: 1, info: 2 };

export function groupFindings(findings: Finding[]): [string, Finding[]][] {
  const by = new Map<string, Finding[]>();
  for (const f of findings) if (f.open !== false) by.set(f.rule, [...(by.get(f.rule) ?? []), f]);
  return [...by.entries()].sort((a, b) => (SEVERITY_ORDER[a[1][0].severity] ?? 3) - (SEVERITY_ORDER[b[1][0].severity] ?? 3)
    || b[1].length - a[1].length || a[0].localeCompare(b[0]));
}

export default function Findings() {
  const findings = useFindings();
  const index = useSubjectIndex();
  const groups = useMemo(() => groupFindings(findings.data ?? []), [findings.data]);
  const [open, setOpen] = useState<string | null>(null);
  return (
    <div className="page">
      <div>
        <h1 className="m-0">Findings</h1>
        <p className="page__lede text-sm mt-1">Open findings from the linter and the project's own checks, by rule.</p>
      </div>
      <QueryState isPending={findings.isPending} error={findings.error} onRetry={findings.refetch} isEmpty={groups.length === 0}
        empty="No open findings.">
        <ul className="stack finding-groups">
          {groups.map(([rule, list]) => (
            <li key={rule} className="card">
              <button type="button" className="finding-group__head" aria-expanded={open === rule}
                onClick={() => setOpen(open === rule ? null : rule)}>
                <span className={`rule-chip rule-chip--${list[0].severity}`}>{rule}</span>
                <span className="muted">{list[0].severity}</span>
                <span className="font-semibold">{list.length}</span>
              </button>
              {open === rule ? (
                <LazyList as="ul" className="finding-list mt-2" items={list} keyOf={(f) => f.id}
                  render={(f) => (
                    <li>
                      <span className="flex-1 min-w-0">{f.message}</span>
                      <span className="flex flex-wrap gap-2">
                        {f.subjects.map((s) => <SubjectLink key={s} sid={s} index={index.data} />)}
                      </span>
                    </li>
                  )} />
              ) : null}
            </li>
          ))}
        </ul>
      </QueryState>
    </div>
  );
}
