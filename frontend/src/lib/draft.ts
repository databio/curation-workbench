// Building the decision a one-click answer saves (ported from ReviewItem.astro),
// and the plain-words helpers for showing a decision back to a person.
import type { Action, Decision, DecisionDraft, Item, SubjectIndex, Verdicts } from '../api/types';

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

/** Every ready-made answer on an item, choices first. */
export function allActions(item: Pick<Item, 'choices' | 'actions'>): Action[] {
  return [...item.choices.flatMap((c) => c.actions), ...item.actions];
}

/** The keys an action's operation does not carry itself (added at save time). */
const SAVE_TIME_KEYS = new Set(['op', 'description', 'examples']);

/**
 * The button on `item` whose click would produce decision `d` (same verdict,
 * and every key of the button's operation equal in the decision's), so a saved
 * row can say "Saved: Looks right" instead of the verdict id. Undefined when no
 * button matches (a decision made from the subject page).
 */
export function actionForDecision(
  item: Pick<Item, 'choices' | 'actions'>,
  d: Pick<Decision, 'verdict' | 'operation'>,
): Action | undefined {
  const candidates = allActions(item).filter((a) => a.verdict === d.verdict);
  const op = d.operation ?? {};
  const fits = (a: Action) => {
    const mine = a.operation ?? {};
    for (const [k, v] of Object.entries(mine)) if (JSON.stringify(op[k]) !== JSON.stringify(v)) return false;
    // A button with no operation of its own only matches a decision with none (beyond save-time keys).
    if (!Object.keys(mine).length && Object.keys(op).some((k) => !SAVE_TIME_KEYS.has(k)) && !a.input) return false;
    return true;
  };
  return candidates.find(fits) ?? candidates[0];
}

const humanize = (v: string) => v.replace(/_/g, ' ');

/**
 * A verdict in words: its label from verdicts.yaml when the kind is known
 * (`keep` -> "Keep as is"), else the id with its underscores as spaces.
 */
export function verdictLabel(verdict: string, kind?: string | null, verdicts?: Verdicts | null): string {
  if (kind && verdicts?.[kind]?.[verdict]?.label) return verdicts[kind][verdict].label;
  if (!kind && verdicts) {
    for (const vs of Object.values(verdicts)) if (vs[verdict]?.label) return vs[verdict].label;
  }
  return humanize(verdict);
}

/** The kind a subject id names (`term:X` -> `term`). */
export const kindOfSid = (sid: string) => sid.split(':', 1)[0];

function showValue(v: unknown): string {
  if (v === true) return 'yes';
  if (v === false) return 'no';
  if (v === null || v === undefined) return '';
  if (typeof v === 'string' || typeof v === 'number') return String(v);
  if (Array.isArray(v) && v.every((x) => typeof x === 'string' || typeof x === 'number')) return v.map(String).join(', ');
  if (Array.isArray(v) && v.every((x) => x && typeof x === 'object' && 'value' in (x as object))) {
    return v.map((x) => String((x as { value: unknown }).value)).join(', ');
  }
  return JSON.stringify(v);
}

/**
 * An operation as short `field: value` pairs, without the `op` key (it repeats
 * the verdict): `{op: "set_cardinality", required: false}` -> [["required", "no"]].
 */
export function describeOperation(op: Record<string, unknown> | null | undefined): [string, string][] {
  if (!op) return [];
  return Object.entries(op)
    .filter(([k, v]) => k !== 'op' && v !== null && v !== undefined && v !== '')
    .map(([k, v]) => [humanize(k), showValue(v)]);
}
