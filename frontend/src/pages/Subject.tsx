// One subject: its contents (what a decision covers), related items, links,
// plugin panels, findings in plain words, decision history and the full
// decision form. Ported from the first project's Astro subject/[slug].astro,
// without its project-only blocks (those come from plugin panels).
import { Link, useParams } from 'react-router';
import { useBackend } from '../api/backend';
import type { Decision, Panel, SubjectIndex } from '../api/types';
import { DecisionControl } from '../components/DecisionControl';
import { QueryState } from '../components/QueryState';
import { OperationSummary } from '../components/ReviewItem';
import { StateChip } from '../components/StateChip';
import { nameOf, SubjectLink } from '../components/SubjectLink';
import { useReview, useSubject, useSubjectIndex, useVerdicts } from '../hooks/useData';
import { kindOfSid, verdictLabel } from '../lib/draft';
import { projectPath, typePath } from '../lib/paths';

const isScalar = (v: unknown) => v === null || ['string', 'number', 'boolean'].includes(typeof v);
const isScalarList = (v: unknown): v is unknown[] => Array.isArray(v) && v.every(isScalar);

/** Relation fields shown when the subject has them, with how to turn a value into a SID. */
const RELATIONS: [string, string, (v: string) => string][] = [
  ['container', 'Container', (v) => v],
  ['members', 'Members', (v) => v],
  ['owner_class', 'Owner class', (v) => (v.includes(':') ? v : `class:${v}`)],
  ['used_by', 'Listed by', (v) => (v.includes(':') ? v : `class:${v}`)],
  ['inherited_by', 'Inherited by', (v) => (v.includes(':') ? v : `class:${v}`)],
  ['referenced_by', 'Referenced by', (v) => v],
  ['defined_in', 'Defined in module', (v) => (v.includes(':') ? v : `module:${v}`)],
  ['module', 'Module', (v) => (v.includes(':') ? v : `module:${v}`)],
];

/** Payload keys shown elsewhere on the page in a friendlier form. */
const SHOWN_ELSEWHERE = new Set(['sssom']);

export function Payload({ payload, hash, kind }: { payload: Record<string, unknown>; hash: string; kind: string }) {
  const rows = Object.entries(payload).filter(([k]) => !SHOWN_ELSEWHERE.has(k) || !Array.isArray(payload[k]));
  return (
    <section className="card">
      <h2 className="card__title">Contents</h2>
      <p className="muted text-xs mt-0">
        What the schema says about this {kind}. A decision is tied to this content: if any of it changes later, the
        decision is marked stale and needs another look.{' '}
        <span title={hash}>(content id {hash.slice(0, 8)})</span>
      </p>
      <dl className="payload">
        {rows.map(([k, v]) => (
          <div key={k} className="payload__row">
            <dt className="muted">{k.replace(/_/g, ' ')}</dt>
            <dd>
              {isScalar(v) ? (
                v === '' || v === null ? <span className="muted">none</span> : <span className="pre-wrap">{String(v)}</span>
              ) : isScalarList(v) ? (
                v.length === 0 ? (
                  <span className="muted">none</span>
                ) : (
                  <ul className="tag-list">
                    {v.map((x, i) => (
                      <li key={i} className="tag">
                        {String(x)}
                      </li>
                    ))}
                  </ul>
                )
              ) : (
                <pre className="code-block">{JSON.stringify(v, null, 2)}</pre>
              )}
            </dd>
          </div>
        ))}
      </dl>
    </section>
  );
}

function PanelView({ panel, index }: { panel: Panel; index: SubjectIndex | undefined }) {
  return (
    <section className="card">
      <h2 className="card__title">{panel.title}</h2>
      {panel.text ? <p className="m-0 text-sm">{panel.text}</p> : null}
      {panel.links?.length ? (
        <ul className="text-sm">
          {panel.links.map((l, i) => (
            <li key={i}>
              {l.sid ? <SubjectLink sid={l.sid} index={index}>{l.label}</SubjectLink>
                : l.href ? <a href={l.href} target="_blank" rel="noreferrer">{l.label}</a> : l.label}
            </li>
          ))}
        </ul>
      ) : null}
      {panel.table ? (
        <table className="table mt-2">
          <thead>
            <tr>{panel.table.columns.map((c) => <th key={c}>{c}</th>)}</tr>
          </thead>
          <tbody>
            {panel.table.rows.map((r, i) => (
              <tr key={i}>{r.map((c, j) => <td key={j}>{String(c ?? '')}</td>)}</tr>
            ))}
          </tbody>
        </table>
      ) : null}
    </section>
  );
}

export function DecisionRow({ d, sid, label }: { d: Decision; sid: string; label: string }) {
  return (
    <li className="history-entry">
      <div className="flex flex-wrap items-center gap-2">
        <span className="badge badge--neutral" title={d.verdict}>{label}</span>
        <span className={`badge status-badge--${d.status}`}>{d.status}</span>
        <span className="muted text-xs">
          {d.decided_by} · {d.decided_on} · <span className="font-mono">{d.id}</span>
        </span>
        {d.subject !== sid ? (
          <span className="muted text-xs">
            on <span className="font-mono">{d.subject}</span>
          </span>
        ) : null}
      </div>
      <OperationSummary op={d.operation} className="block text-xs mt-1" />
      <p className="mt-1 mb-0">{d.rationale}</p>
    </li>
  );
}

export default function Subject() {
  const { slug = '' } = useParams();
  const { project, can, mode } = useBackend();
  const index = useSubjectIndex();
  const review = useReview();
  const sid = index.data ? Object.entries(index.data).find(([, r]) => r.slug === slug)?.[0] : undefined;
  const view = useSubject(sid);
  const verdicts = useVerdicts();
  const notFound = index.data && !sid;
  const v = view.data;
  const rec = v?.subject;
  const suggested = v?.findings.find((f) => f.suggested_verdict && f.open !== false);
  const name = rec ? (rec.kind === 'term' ? rec.label ?? rec.name : rec.name) : '';
  // The review pages this item is waiting on (from the review index's per-type SID lists).
  const waitingOn = sid ? (review.data?.tracks ?? []).flatMap((t) => t.types
    .filter((ty) => ty.sids?.includes(sid))
    .map((ty) => ({ to: typePath(project.slug, t.id, ty.id), title: `${t.title}: ${ty.title}` }))) : [];
  const sssom = Array.isArray(rec?.payload.sssom) ? (rec!.payload.sssom as Record<string, string>[]) : [];

  return (
    <QueryState isPending={(index.isPending || view.isPending) && !notFound} error={index.error || view.error}
      isEmpty={!!notFound} empty={`There is no item called ${slug}.`}>
      {v && rec ? (
        <div className="page">
          <nav className="muted text-sm">
            <Link to={projectPath(project.slug)}>Review</Link> › {name}
          </nav>
          <header className="card">
            <div className="flex flex-wrap items-start justify-between gap-4">
              <div className="min-w-0">
                <div className="eyebrow">{rec.kind}</div>
                <h1 className="m-0 break-all">{name}</h1>
                <div className="font-mono text-sm muted mt-1">{rec.sid}</div>
              </div>
              <div className="flex flex-col items-end gap-2">
                <StateChip sid={rec.sid} index={index.data} />
                <div className="muted text-sm" title="Queue priority: higher comes first in All items">
                  priority <span className="font-semibold">{v.status.score.toFixed(2)}</span>
                </div>
              </div>
            </div>
            <dl className="kv mt-4 text-sm">
              <dt>Layer</dt>
              <dd>{rec.layer ?? '—'}</dd>
              <dt>Container</dt>
              <dd>
                {rec.container ? (
                  <span title={rec.container}><SubjectLink sid={rec.container} index={index.data} /></span>
                ) : '—'}
              </dd>
              <dt>Source file</dt>
              <dd className="font-mono">{rec.file ?? '—'}</dd>
              <dt>Web page</dt>
              <dd>{v.browse ? <a href={v.browse} target="_blank" rel="noreferrer">{v.browse}</a> : <span className="muted">none</span>}</dd>
            </dl>
            {waitingOn.length > 0 ? (
              <p className="text-sm mt-3 mb-0" data-testid="waiting-on">
                <span className="muted">Waiting for an answer under:</span>{' '}
                {waitingOn.map((w) => <Link key={w.to} to={w.to} className="type-tag">{w.title}</Link>)}
              </p>
            ) : null}
            {rec.retired ? (
              <div className="banner mt-4 mb-0 text-sm">
                Retired: this {rec.kind} no longer exists in the schema. Its page is kept so its history still reads.
                {v.aliases.forward ? (
                  <>
                    {' '}It is now <SubjectLink sid={v.aliases.forward} index={index.data}>{nameOf(v.aliases.forward, index.data)}</SubjectLink>.
                  </>
                ) : null}
              </div>
            ) : null}
            {v.aliases.former.length > 0 ? (
              <div className="muted text-xs mt-3">
                Formerly: <span className="font-mono">{v.aliases.former.join(', ')}</span>
              </div>
            ) : null}
          </header>

          <div className="subject-grid">
            <div className="stack">
              <Payload payload={rec.payload} hash={rec.hash} kind={rec.kind} />
              <Relations rec={rec} index={index.data} />
              {sssom.length > 0 ? (
                <section className="card">
                  <h2 className="card__title">Links to other ontologies</h2>
                  <div className="overflow-x-auto">
                    <table className="table">
                      <thead>
                        <tr><th>Relation</th><th>Linked term</th><th>Modifier</th><th>How it was made</th><th>Comment</th></tr>
                      </thead>
                      <tbody>
                        {sssom.map((m, i) => (
                          <tr key={i}>
                            <td className="font-mono text-xs">{m.predicate}</td>
                            <td className="font-mono text-xs">{m.object}</td>
                            <td className="text-xs">{m.modifier || ''}</td>
                            <td className="font-mono text-xs">{m.justification}</td>
                            <td className="text-xs">{m.comment}{m.fit ? ` (fit: ${m.fit})` : ''}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                </section>
              ) : null}
              {v.panels.map((p, i) => <PanelView key={i} panel={p} index={index.data} />)}
              <section className="card">
                <h2 className="card__title">
                  Findings <span className="muted text-sm">{v.findings.length}</span>
                </h2>
                <p className="muted text-xs mt-0">What the automatic checks flagged about this {rec.kind}.</p>
                {v.findings.length === 0 ? <p className="muted text-sm m-0">Nothing flagged.</p> : (
                  <ul className="finding-list">
                    {v.findings.map((f) => (
                      <li key={f.id} title={`${f.id} · ${f.message}`}>
                        <span className="flex-1 min-w-0">{f.text ?? f.message}</span>
                        <span className={`rule-chip rule-chip--${f.severity}`}>{f.rule}</span>
                      </li>
                    ))}
                  </ul>
                )}
              </section>
              {v.votes ? (
                <section className="card">
                  <h2 className="card__title">
                    Votes <span className="muted text-sm">{v.votes.length}</span>
                  </h2>
                  {v.votes.length === 0 ? <p className="muted text-sm m-0">No votes yet.</p> : (
                    <ul className="text-sm">
                      {v.votes.map((x) => (
                        <li key={x.id}>
                          {x.user.name} · {x.review_type} · {x.proposal ? verdictLabel(x.proposal.verdict, rec.kind, verdicts.data) : x.answer}
                          {x.stale ? ' (out of date)' : ''}
                          {x.comment ? <span className="muted"> · {x.comment}</span> : null}
                        </li>
                      ))}
                    </ul>
                  )}
                </section>
              ) : null}
              <section className="card">
                <h2 className="card__title">
                  Decision history <span className="muted text-sm">{v.decisions.length}</span>
                </h2>
                {v.decisions.length === 0 ? <p className="muted text-sm m-0">No decisions yet.</p> : (
                  <ol className="history-list">
                    {v.decisions.map((d) => (
                      <DecisionRow key={d.id} d={d} sid={rec.sid} label={verdictLabel(d.verdict, kindOfSid(d.subject), verdicts.data)} />
                    ))}
                  </ol>
                )}
              </section>
            </div>

            <aside className="stack">
              <section className="card">
                <h2 className="card__title">{mode === 'local' ? 'Record a decision' : can.decide ? 'Vote or decide' : 'Vote'}</h2>
                {rec.retired ? (
                  <p className="muted text-sm m-0">Retired items take no new decisions.</p>
                ) : can.decide || can.vote ? (
                  <>
                    <p className="muted text-xs mt-0">
                      Any action the review buttons do not offer: pick it, say why, and save.
                    </p>
                    <DecisionControl sid={rec.sid} kind={rec.kind} index={index.data} verdicts={verdicts.data}
                      findingId={suggested?.id} suggestedVerdict={suggested?.suggested_verdict} />
                  </>
                ) : (
                  <p className="muted text-sm m-0">You can read this project but not vote on it.</p>
                )}
              </section>
              {v.prompts.length > 0 ? (
                <section className="card stack">
                  <h2 className="card__title">Suggested actions</h2>
                  {v.prompts.map((p, i) => {
                    const onOwner = p.decide_on === 'owner_class' && rec.owner_class;
                    const target = onOwner ? `class:${rec.owner_class}` : rec.sid;
                    return (
                      <div key={p.id ?? i} className="prompt">
                        <div className="font-medium">{p.title}</div>
                        <p className="text-sm mt-1 mb-1">{p.text}</p>
                        {p.source ? <div className="muted text-xs">{p.source}</div> : null}
                        {p.suggested_verdict && !rec.retired && (can.decide || can.vote) ? (
                          <div className="mt-2">
                            {onOwner ? (
                              <div className="muted text-xs mb-1">
                                Decided on the owner class, <SubjectLink sid={target} index={index.data}>{nameOf(target, index.data)}</SubjectLink>:
                              </div>
                            ) : null}
                            <DecisionControl sid={target} kind={index.data?.[target]?.kind ?? rec.kind} index={index.data}
                              verdicts={verdicts.data} suggestedVerdict={p.suggested_verdict}
                              suggestedOperation={p.suggested_operation ?? undefined} />
                          </div>
                        ) : null}
                      </div>
                    );
                  })}
                </section>
              ) : null}
            </aside>
          </div>
        </div>
      ) : null}
    </QueryState>
  );
}

function Relations({ rec, index }: { rec: Record<string, unknown>; index: SubjectIndex | undefined }) {
  const rows = RELATIONS.flatMap(([field, label, toSid]) => {
    const raw = rec[field];
    if (raw === undefined || raw === null || field === 'container') return [];
    const values = (Array.isArray(raw) ? raw : [raw]).map(String);
    return [[label, values.map(toSid)] as const];
  });
  if (!rows.length) return null;
  return (
    <section className="card">
      <h2 className="card__title">Related items</h2>
      <dl className="kv text-sm">
        {rows.map(([label, sids]) => (
          <div key={label} className="kv__row">
            <dt>{label}</dt>
            <dd className="relation-list">
              {sids.length === 0 ? <span className="muted">none</span> : sids.map((s) => (
                <span key={s} className="relation">
                  <SubjectLink sid={s} index={index}>{nameOf(s, index)}</SubjectLink>
                  {index?.[s] ? <StateChip sid={s} index={index} /> : null}
                </span>
              ))}
            </dd>
          </div>
        ))}
      </dl>
    </section>
  );
}
