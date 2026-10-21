// One row of a review type's page. Shows the subject in plain terms (name,
// where it lives, its definition, why it is here) and the type's ready-made
// answers as buttons. A click is a vote; curators also see the tally and can
// turn it into a decision. Anything the buttons don't cover is one link away on the
// subject page. Layout and save flow ported from ReviewItem.astro.
import { memo, useState } from 'react';
import { Link } from 'react-router';
import { saveErrorMessage, useBackend } from '../api/backend';
import type { Action, Answer, Decision, DecisionDraft, Item, MyVote, Question, SubjectIndex, Vote } from '../api/types';
import { useVerdicts } from '../hooks/useData';
import { actionFor, actionForDecision, buildDraft, describeOperation, verdictLabel } from '../lib/draft';
import { subjectPath } from '../lib/paths';
import { ConfirmDialog } from './ConfirmDialog';

export interface RowState {
  /** My vote on this row's question. */
  mine?: MyVote | null;
  /** The row's question (tally), when it has votes or a resolution. */
  question?: Question | null;
  /** The decision a curator made from the question. */
  resolved?: Decision | null;
}

interface ReviewItemProps {
  item: Item;
  reviewType: string;
  index: SubjectIndex | undefined;
  state: RowState;
  /** Called after any change this row made (saved, undone, voted, resolved). */
  onChanged: (sid: string, answer: Answer | null) => void;
}

const TONE: Record<string, string> = { primary: 'btn btn--sm btn--primary', neutral: 'btn btn--sm', danger: 'btn btn--sm btn--danger-outline' };

/** Is the row answered (by my current vote, or a curator's resolution)? */
export function rowDone(s: RowState): boolean {
  return !!((s.mine && !s.mine.stale) || s.resolved || s.question?.state === 'resolved');
}

/** `{field: value}` pairs of an operation as a short inline list. */
export function OperationSummary({ op, className = '' }: { op: Record<string, unknown> | null | undefined; className?: string }) {
  const pairs = describeOperation(op);
  if (!pairs.length) return null;
  return (
    <span className={`op-list ${className}`.trim()}>
      {pairs.map(([k, v]) => (
        <span key={k} className="op-list__pair">
          <span className="muted">{k}:</span> {v}
        </span>
      ))}
    </span>
  );
}

function ReviewItemInner({ item, reviewType, index, state, onChanged }: ReviewItemProps) {
  const backend = useBackend();
  const { can, project } = backend;
  const verdicts = useVerdicts();
  const [note, setNote] = useState('');
  const [editor, setEditor] = useState(item.editor?.value ?? '');
  const [msg, setMsg] = useState('');
  const [saving, setSaving] = useState(false);
  const [confirming, setConfirming] = useState<Action | null>(null);
  const [changing, setChanging] = useState(false);
  const href = item.slug ? subjectPath(project.slug, item.slug) : null;

  const done = rowDone(state);
  const open = !done || changing;

  async function save(action: Action) {
    // Hashes of the other subjects an answer touches come from the subject
    // index; fetch it now if the page has not loaded it yet.
    const idx = index ?? (action.also?.length ? await backend.subjectIndex().catch(() => undefined) : undefined);
    const built = buildDraft(item, action, note, editor, idx);
    if ('error' in built) {
      setMsg(built.error);
      return;
    }
    setSaving(true);
    setMsg('Voting…');
    try {
      const answer = await backend.answer(
        { sid: item.sid, reviewType, action, hash: item.hash, comment: note.trim() || undefined },
        built.draft,
      );
      setMsg('');
      setChanging(false);
      onChanged(item.sid, answer);
    } catch (e) {
      setMsg(saveErrorMessage(e));
    } finally {
      setSaving(false);
    }
  }

  function click(action: Action) {
    if (action.confirm) setConfirming(action);
    else void save(action);
  }

  async function undo() {
    try {
      if (state.mine) await backend.undo({ kind: 'vote', vote: { sid: item.sid, review_type: reviewType } as Vote });
      onChanged(item.sid, null);
    } catch (e) {
      setMsg(`Not undone: ${(e as Error).message}`);
    }
  }

  const button = (a: Action) => (
    <button key={a.answer + a.label} type="button" className={TONE[a.tone ?? 'neutral']} disabled={saving || !(can.decide || can.vote)}
      title={a.rationale} onClick={() => click(a)}>
      {a.label}
    </button>
  );

  /** A vote's or decision's answer in the words of the button that makes it. */
  const voteLabel = (answer: string, verdict?: string) =>
    actionFor(item, answer)?.label ?? (verdict ? verdictLabel(verdict, item.kind, verdicts.data) : answer);
  const decisionLabel = (d: Decision) => actionForDecision(item, d)?.label ?? verdictLabel(d.verdict, item.kind, verdicts.data);

  return (
    <li className={`review-item${done ? ' review-item--done' : ''}`} data-sid={item.sid}>
      <div className="review-item__head">
        <div className="min-w-0">
          {href ? (
            <Link to={href} className="review-item__title" title="Open this item's page">
              {item.title}
            </Link>
          ) : (
            <span className="review-item__title">{item.title}</span>
          )}
          <span className="review-item__where">
            {item.kind}
            {item.container_label ? (
              <>
                {' '}in <span className="font-medium">{item.container_label}</span>
              </>
            ) : null}
          </span>
          {item.browse ? (
            <a href={item.browse} className="review-item__browse" target="_blank" rel="noreferrer">
              view in schema site
            </a>
          ) : null}
        </div>
        <DoneLine state={state} voteLabel={voteLabel} decisionLabel={decisionLabel} onUndo={undo}
          onChange={() => setChanging(true)} canDecide={can.decide} onWithdrawn={() => onChanged(item.sid, null)} />
      </div>

      {item.description && item.editor?.field !== 'description' ? <p className="review-item__desc">{item.description}</p> : null}

      {item.reasons.length > 0 ? (
        <ul className="review-item__reasons">
          {item.reasons.map((r) => (
            <li key={r}>{r}</li>
          ))}
        </ul>
      ) : null}

      {state.mine?.stale ? <p className="review-item__stale">Your vote is out of date: the term changed. Vote again.</p> : null}

      {open ? (
        <div className="review-item__body">
          {item.choices.map((c) => (
            <div key={c.text} className="review-item__choice">
              <div className="min-w-0 text-sm">
                <div className="font-medium">{c.text}</div>
                {c.detail ? (
                  <div className="review-item__detail" title={c.detail}>
                    {c.detail}
                  </div>
                ) : null}
              </div>
              <div className="flex flex-wrap gap-2">{c.actions.map(button)}</div>
            </div>
          ))}

          {item.editor ? (
            <label className="review-item__editor-label">
              <span>{item.editor.field === 'description' ? 'Edit the text here, then save it:' : 'Examples, one per line:'}</span>
              <textarea
                className="form__input review-item__editor"
                aria-label={item.editor.field === 'description' ? 'New definition' : 'Examples'}
                rows={item.editor.field === 'description' ? 3 : 2}
                placeholder={item.editor.placeholder}
                value={editor}
                onChange={(e) => setEditor(e.target.value)}
              />
            </label>
          ) : null}

          <div className="review-item__actions">
            {item.actions.map(button)}
            <input
              type="text"
              className="form__input review-item__note"
              aria-label="Note"
              placeholder="Why? (optional, saved with your answer)"
              value={note}
              onChange={(e) => setNote(e.target.value)}
            />
          </div>
          {msg ? <p className="review-item__msg" role="status">{msg}</p> : null}
          {href ? (
            <Link to={href} className="review-item__more">
              Need another action (rename, merge, move, add a link…)? Open its page →
            </Link>
          ) : null}
        </div>
      ) : msg ? (
        <p className="review-item__msg" role="status">{msg}</p>
      ) : null}

      {state.question && state.question.total + state.question.stale > 0 ? (
        <Tally item={item} reviewType={reviewType} index={index} question={state.question} voteLabel={voteLabel}
          resolved={!!state.resolved || state.question.state === 'resolved'} onChanged={() => onChanged(item.sid, null)} />
      ) : null}

      <ConfirmDialog
        isOpen={!!confirming}
        title="Are you sure?"
        message={confirming?.confirm ?? ''}
        confirmLabel={confirming?.label ?? 'Confirm'}
        destructive={confirming?.tone === 'danger'}
        onCancel={() => setConfirming(null)}
        onConfirm={async () => {
          const a = confirming!;
          setConfirming(null);
          await save(a);
        }}
      />
    </li>
  );
}

function DoneLine({ state, voteLabel, decisionLabel, onUndo, onChange, canDecide, onWithdrawn }: {
  state: RowState;
  voteLabel: (answer: string, verdict?: string) => string;
  decisionLabel: (d: Decision) => string;
  onUndo: () => void;
  onChange: () => void;
  canDecide: boolean;
  onWithdrawn: () => void;
}) {
  const backend = useBackend();
  if (state.resolved) {
    const d = state.resolved;
    return (
      <span className="review-item__done" title={`${d.id} · ${d.verdict}`}>
        ✓ Decided by {d.decided_by}: {decisionLabel(d)}
        {canDecide && d.status === 'pending' ? (
          <button type="button" className="link-button ml-1" onClick={async () => {
            await backend.withdraw(d);
            onWithdrawn();
          }}>
            Withdraw
          </button>
        ) : null}
      </span>
    );
  }
  if (state.question?.state === 'resolved' && state.question.outcome === 'no_action') {
    return <span className="review-item__done">✓ Closed: nothing to do</span>;
  }
  if (state.mine) {
    return (
      <span className="review-item__done">
        ✓ Your vote: {voteLabel(state.mine.answer, state.mine.proposal?.verdict)} ·{' '}
        <button type="button" className="link-button" onClick={onChange}>
          Change
        </button>{' '}
        ·{' '}
        <button type="button" className="link-button" onClick={onUndo}>
          Retract
        </button>
      </span>
    );
  }
  return null;
}

/** The votes on this row's question, and (for curators) turning them into a decision. */
function Tally({ item, reviewType, index, question, voteLabel, resolved, onChanged }: {
  item: Item;
  reviewType: string;
  index: SubjectIndex | undefined;
  question: Question;
  voteLabel: (answer: string, verdict?: string) => string;
  resolved: boolean;
  onChanged: () => void;
}) {
  const backend = useBackend();
  const verdicts = useVerdicts();
  const [votes, setVotes] = useState<Vote[] | null>(null);
  const [showVotes, setShowVotes] = useState(false);
  const [deciding, setDeciding] = useState<{ answer: string; voteId: number; proposal: Question['answers'][number]['proposal'] } | null>(null);
  const [noAction, setNoAction] = useState(false);
  const [msg, setMsg] = useState('');
  const sorted = [...question.answers].sort((a, b) => b.count - a.count);
  const lead = sorted[0];
  const tie = sorted.length > 1 && sorted[1].count === lead?.count;

  const draftFrom = (proposal: NonNullable<Question['answers'][number]['proposal']>, rationale: string): DecisionDraft => {
    const also = proposal.also_affects ?? [];
    const hashes: Record<string, string> = { [item.sid]: item.hash };
    for (const s of also) if (index?.[s]) hashes[s] = index[s].hash;
    return {
      subject: item.sid,
      verdict: proposal.verdict,
      operation: proposal.operation ?? null,
      rationale,
      also_affects: also,
      evidence: proposal.evidence ?? [],
      subject_hashes: hashes,
      supersedes: null,
    };
  };

  async function toggleVotes() {
    setShowVotes((v) => !v);
    if (!votes) {
      try {
        setVotes(await backend.votes(item.sid, reviewType));
      } catch (e) {
        setMsg((e as Error).message);
      }
    }
  }

  return (
    <div className="tally">
      <div className="tally__line">
        <span className="muted">Votes so far:</span>
        {sorted.map((a) => (
          <span key={a.answer} className={`tally__answer${question.mine?.answer === a.answer ? ' tally__answer--mine' : ''}`}>
            {voteLabel(a.answer, a.proposal?.verdict)} ×{a.count}
            {a.stale ? <span className="muted"> ({a.stale} out of date)</span> : null}
            {backend.can.decide && !resolved && a.proposal ? (
              <button type="button" className="link-button ml-1" onClick={() => setDeciding({ answer: a.answer, voteId: a.vote_ids[0], proposal: a.proposal })}>
                Decide this
              </button>
            ) : null}
          </span>
        ))}
        <button type="button" className="link-button" onClick={toggleVotes}>
          {showVotes ? 'Hide votes' : 'Who voted'}
        </button>
      </div>
      {backend.can.decide && !resolved ? (
        <div className="tally__actions">
          <button type="button" className="btn btn--sm" disabled={!lead || tie || !lead.proposal}
            title={tie ? 'The vote is tied: pick one with "Decide this"' : 'Make the leading answer the decision'}
            onClick={() => lead && setDeciding({ answer: lead.answer, voteId: lead.vote_ids[0], proposal: lead.proposal })}>
            Decide from tally
          </button>
          <button type="button" className="btn btn--sm" title="Close this question without a decision" onClick={() => setNoAction(true)}>
            Nothing to do
          </button>
        </div>
      ) : null}
      {showVotes && votes ? (
        <ul className="tally__votes">
          {votes.map((v) => (
            <li key={v.id}>
              {v.user.name}: {voteLabel(v.answer, v.proposal?.verdict)}
              {v.stale ? ' (out of date)' : ''}
              {v.comment ? <span className="muted"> · {v.comment}</span> : null}
            </li>
          ))}
        </ul>
      ) : null}
      {msg ? <p className="review-item__msg">{msg}</p> : null}
      <ConfirmDialog
        isOpen={!!deciding}
        title="Turn votes into a decision"
        message={deciding?.proposal ? (
          <div className="stack">
            <div>
              <strong>{verdictLabel(deciding.proposal.verdict, item.kind, verdicts.data)}</strong>
              <OperationSummary op={deciding.proposal.operation} className="block text-sm mt-1" />
            </div>
            <div className="muted">Reason: {deciding.proposal.rationale}</div>
          </div>
        ) : ''}
        confirmLabel="Decide"
        textarea={{ label: 'Change the reason (optional)', maxLength: 2000, placeholder: deciding?.proposal?.rationale ?? '' }}
        onCancel={() => setDeciding(null)}
        onConfirm={async (text) => {
          const d = deciding!;
          setDeciding(null);
          try {
            const edited = text.trim();
            await backend.resolve({
              sid: item.sid,
              reviewType,
              outcome: 'decision',
              ...(edited && d.proposal ? { decision: draftFrom(d.proposal, edited) } : { fromVote: d.voteId }),
            });
            onChanged();
          } catch (e) {
            setMsg(saveErrorMessage(e));
          }
        }}
      />
      <ConfirmDialog
        isOpen={noAction}
        title="Nothing to do"
        message="Close this question with no decision? The votes stay on record."
        confirmLabel="Close it"
        textarea={{ label: 'Note (optional)', maxLength: 2000 }}
        onCancel={() => setNoAction(false)}
        onConfirm={async (note) => {
          setNoAction(false);
          try {
            await backend.resolve({ sid: item.sid, reviewType, outcome: 'no_action', note: note.trim() || undefined });
            onChanged();
          } catch (e) {
            setMsg(saveErrorMessage(e));
          }
        }}
      />
    </div>
  );
}

export const ReviewItem = memo(ReviewItemInner);
