// Building the decision a one-click answer saves (ported from ReviewItem.astro).
import type { Action, DecisionDraft, Item, SubjectIndex } from '../api/types';

export type DraftResult = { draft: DecisionDraft } | { error: string };

/**
 * The draft for clicking `action` on `item`: the action's operation (plus the
 * row's editor text for `input` actions), the row's note as the rationale
 * when one is written, the finding as evidence, and the hash of every
 * subject touched.
 */
export function buildDraft(
  item: Pick<Item, 'sid' | 'hash'>,
  action: Action,
  note: string,
  editorText: string,
  index: SubjectIndex | undefined,
): DraftResult {
  const operation: Record<string, unknown> = { ...(action.operation || {}) };
  if (action.input === 'description') {
    const text = editorText.trim();
    if (!text) return { error: 'Write the definition first.' };
    operation.description = text;
  }
  if (action.input === 'examples') {
    const values = editorText.split('\n').map((s) => s.trim()).filter(Boolean);
    if (!values.length) return { error: 'Add at least one example (one per line).' };
    operation.examples = values.map((value) => ({ value }));
  }
  const also = action.also ?? [];
  const hashes: Record<string, string> = { [item.sid]: item.hash };
  for (const s of also) if (index?.[s]) hashes[s] = index[s].hash;
  return {
    draft: {
      subject: item.sid,
      verdict: action.verdict,
      operation: Object.keys(operation).length ? { op: action.verdict, ...operation } : null,
      rationale: note.trim() || action.rationale,
      also_affects: also,
      evidence: action.evidence ? [{ kind: 'finding', id: action.evidence }] : [],
      subject_hashes: hashes,
      supersedes: null,
    },
  };
}

/** The action a vote's answer key names on this item, if any. */
export function actionFor(item: Pick<Item, 'choices' | 'actions'>, answer: string): Action | undefined {
  for (const c of item.choices) for (const a of c.actions) if (a.answer === answer) return a;
  return item.actions.find((a) => a.answer === answer);
}

export const verdictLabel = (v: string) => v.replace(/_/g, ' ');
