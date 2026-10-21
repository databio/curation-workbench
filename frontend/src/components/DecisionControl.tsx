// The full decision form for one subject: any verdict of its kind, with
// operation fields built from the verdict's field schema (verdicts.yaml).
// Ported from the first project's Astro DecisionControl.astro. Local: Save is a pending
// decision. Hosted: voters see "Vote"; curators see "Vote" and "Decide".
import { useId, useMemo, useState } from 'react';
import { saveErrorMessage, useBackend } from '../api/backend';
import type { Action, Decision, DecisionDraft, SubjectIndex, Verdicts } from '../api/types';
import { useChanged, useSubject } from '../hooks/useData';
import { fieldSpec, readValue, showValue, type FieldSpec } from '../lib/fields';

interface DecisionControlProps {
  sid: string;
  kind: string;
  index: SubjectIndex | undefined;
  verdicts: Verdicts | undefined;
  /** The finding the decision answers; sent as evidence. */
  findingId?: string | null;
  suggestedVerdict?: string | null;
  suggestedOperation?: Record<string, unknown> | null;
}

/** The newest non-withdrawn decision on the SID (subject, also_affects or created). */
export function latestFor(sid: string, decisions: Decision[]): Decision | undefined {
  const touching = decisions.filter((d) => d.status !== 'withdrawn'
    && (d.subject === sid || (d.also_affects || []).includes(sid) || (d.applied?.created || []).includes(sid)));
  const key = (id: string) => {
    const m = /^(.*)-(\d+)$/.exec(id);
    return m ? [m[1], Number(m[2])] as const : [id, -1] as const;
  };
  touching.sort((a, b) => {
    const [pa, na] = key(a.id);
    const [pb, nb] = key(b.id);
    return pa === pb ? na - nb : pa.localeCompare(pb);
  });
  return touching[touching.length - 1];
}

export function DecisionControl({ sid, kind, index, verdicts, findingId, suggestedVerdict, suggestedOperation }: DecisionControlProps) {
  const backend = useBackend();
  const { can, mode } = backend;
  const changed = useChanged();
  const view = useSubject(sid);
  const options = Object.entries(verdicts?.[kind] ?? {}).filter(([, v]) => !v.seed_only);
  const suggested = options.some(([v]) => v === suggestedVerdict) ? suggestedVerdict! : '';
  const [verdict, setVerdict] = useState(suggested);
  const [values, setValues] = useState<Record<string, string>>({});
  const [rationale, setRationale] = useState('');
  const [msg, setMsg] = useState('');
  const [busy, setBusy] = useState(false);
  const listId = useId();

  const latest = latestFor(sid, view.data?.decisions ?? []);
  const stale = view.data?.status.state === 'stale';
  const schema = verdicts?.[kind]?.[verdict]?.operation;
  const specs: FieldSpec[] = useMemo(() => [
    ...Object.entries(schema?.required ?? {}).map(([f, t]) => fieldSpec(f, t, true)),
    ...Object.entries(schema?.optional ?? {}).map(([f, t]) => fieldSpec(f, t, false)),
  ], [schema]);

  const valueOf = (spec: FieldSpec) => values[`${verdict}:${spec.name}`]
    ?? (verdict === suggested ? showValue(spec, suggestedOperation?.[spec.name]) : '');

  function build(): DecisionDraft | null {
    const fields: Record<string, unknown> = {};
    try {
      for (const spec of specs) {
        const v = readValue(spec, valueOf(spec));
        if (v !== undefined) fields[spec.name] = v;
      }
    } catch (e) {
      setMsg(`Bad field value: ${(e as Error).message}`);
      return null;
    }
    const also = new Set<string>();
    for (const spec of specs) {
      if (spec.widget !== 'sid' || fields[spec.name] === undefined) continue;
      for (const s of ([] as string[]).concat(fields[spec.name] as string | string[])) if (s !== sid) also.add(s);
    }
    const hashes: Record<string, string> = {};
    if (index?.[sid]) hashes[sid] = index[sid].hash;
    for (const s of also) if (index?.[s]) hashes[s] = index[s].hash;
    return {
      subject: sid,
      verdict,
      operation: schema && Object.keys(fields).length ? { op: verdict, ...fields } : null,
      rationale: rationale.trim(),
      also_affects: [...also],
      evidence: findingId ? [{ kind: 'finding', id: findingId }] : [],
      subject_hashes: hashes,
      supersedes: latest?.id ?? null,
    };
  }

  async function run(how: 'decide' | 'vote') {
    const draft = build();
    if (!draft) return;
    setBusy(true);
    setMsg(how === 'vote' ? 'Voting…' : 'Saving…');
    try {
      if (how === 'decide') {
        const d = await backend.decide(draft);
        setMsg(`Saved ${d.id}.`);
      } else {
        const action: Action = {
          label: verdicts?.[kind]?.[verdict]?.label ?? verdict, verdict, operation: draft.operation,
          rationale: draft.rationale, evidence: findingId ?? null, also: draft.also_affects, tone: null,
          input: null, confirm: null, answer: verdict,
        };
        await backend.answer({ sid, reviewType: 'subject', action, hash: draft.subject_hashes[sid] ?? '' }, draft);
        setMsg('Vote saved.');
      }
      setRationale('');
      changed();
    } catch (e) {
      setMsg(saveErrorMessage(e));
    } finally {
      setBusy(false);
    }
  }

  async function withdraw() {
    if (!latest) return;
    try {
      await backend.withdraw(latest);
      setMsg(`Withdrew ${latest.id}.`);
      changed();
    } catch (e) {
      setMsg(`Not withdrawn: ${(e as Error).message}`);
    }
  }

  const ready = !!verdict && !!rationale.trim() && !busy && !!index;
  const kindOf = (spec: FieldSpec) => Object.entries(index ?? {})
    .filter(([, r]) => !r.retired && (!spec.sidKind || r.kind === spec.sidKind));

  return (
    <div className="decision-control" data-sid={sid}>
      <div className="decision-control__latest">
        {latest ? (
          <>
            <span className="font-mono">{latest.id}</span> · {latest.verdict} · {latest.status} · {latest.decided_by} ·{' '}
            {latest.decided_on}
            {latest.rationale ? <span className="decision-control__rationale">{latest.rationale}</span> : null}
          </>
        ) : (
          'No decision yet.'
        )}
      </div>
      {stale ? (
        <div className="banner banner--error text-sm">
          Stale: this subject changed after {latest?.id ?? 'its last decision'} was recorded. Review it again.
        </div>
      ) : null}
      <div className="decision-control__row">
        <select className="form__input" aria-label="Verdict" value={verdict} onChange={(e) => setVerdict(e.target.value)}>
          <option value="">Choose a verdict…</option>
          {options.map(([v, meta]) => (
            <option key={v} value={v}>
              {meta.label}
            </option>
          ))}
        </select>
        <div className="decision-control__fields">
          {specs.map((spec) => {
            const id = `${listId}-${spec.name}`;
            const set = (v: string) => setValues((x) => ({ ...x, [`${verdict}:${spec.name}`]: v }));
            const label = spec.required ? `${spec.name} *` : spec.name;
            return (
              <label key={spec.name} className="decision-control__field" htmlFor={id}>
                <span>{label}</span>
                {spec.widget === 'select' ? (
                  <select id={id} className="form__input" value={valueOf(spec)} onChange={(e) => set(e.target.value)}>
                    {['', ...spec.choices].map((c) => (
                      <option key={c} value={c}>
                        {c || '—'}
                      </option>
                    ))}
                  </select>
                ) : spec.widget === 'textarea' ? (
                  <textarea id={id} className="form__input" rows={2} placeholder={spec.placeholder} value={valueOf(spec)}
                    onChange={(e) => set(e.target.value)} />
                ) : (
                  <>
                    <input id={id} className="form__input" type={spec.widget === 'number' ? 'number' : 'text'}
                      placeholder={spec.placeholder} value={valueOf(spec)} onChange={(e) => set(e.target.value)}
                      list={spec.widget === 'sid' ? `${id}-list` : undefined} />
                    {spec.widget === 'sid' ? (
                      <datalist id={`${id}-list`}>
                        {kindOf(spec).map(([s, r]) => (
                          <option key={s} value={s} label={r.label} />
                        ))}
                      </datalist>
                    ) : null}
                  </>
                )}
              </label>
            );
          })}
          {schema?.one_of ? (
            <span className="muted text-xs">exactly one of: {schema.one_of.map((g) => g.join('+')).join(' | ')}</span>
          ) : null}
        </div>
      </div>
      <textarea className="form__input w-full" rows={2} aria-label="Rationale" placeholder="Rationale (required): why?"
        value={rationale} onChange={(e) => setRationale(e.target.value)} />
      <div className="toolbar">
        {mode === 'local' ? (
          <button type="button" className="btn btn--sm btn--primary" disabled={!ready} onClick={() => run('decide')}>
            Save
          </button>
        ) : (
          <>
            {can.vote ? (
              <button type="button" className={`btn btn--sm${can.decide ? '' : ' btn--primary'}`} disabled={!ready} onClick={() => run('vote')}>
                Vote
              </button>
            ) : null}
            {can.decide ? (
              <button type="button" className="btn btn--sm btn--primary" disabled={!ready} onClick={() => run('decide')}>
                Decide
              </button>
            ) : null}
          </>
        )}
        {can.decide && latest?.status === 'pending' ? (
          <button type="button" className="btn btn--sm" onClick={withdraw}>
            Withdraw
          </button>
        ) : null}
        {msg ? <span className="muted text-xs" role="status">{msg}</span> : null}
      </div>
    </div>
  );
}
