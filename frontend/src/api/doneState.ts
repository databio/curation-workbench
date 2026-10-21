// The one "does this decision or vote answer this row" rule, ported from the
// first project's Astro ReviewItem.astro.
import type { Decision, Item, Question } from './types';

/**
 * A decision answers a row when it targets the row's subject (or lists it in
 * `also_affects`) and cites one of the row's findings, or cites none (a
 * blanket judgment). Withdrawn decisions answer nothing.
 */
export function decisionAnswers(item: Pick<Item, 'sid' | 'findings'>, d: Decision): boolean {
  if (d.status === 'withdrawn') return false;
  if (d.subject !== item.sid && !(d.also_affects || []).includes(item.sid)) return false;
  const cited = (d.evidence || []).map((e) => e.id).filter(Boolean) as string[];
  return cited.length === 0 || cited.some((id) => item.findings.includes(id));
}

/** A vote (question) answers a row when its sid and review type are the row's. */
export function questionAnswers(item: Pick<Item, 'sid'>, reviewType: string, q: Question): boolean {
  return q.sid === item.sid && q.review_type === reviewType;
}

function split(id: string): [string, number] | null {
  const m = /^(.*)-(\d+)$/.exec(id);
  return m ? [m[1], Number(m[2])] : null;
}

/**
 * True when `d` was made after the list was built: same id prefix as
 * `nextId` (`DEC-0160`, `H-0012`) and a number at or above it. Ids with
 * another prefix never count.
 */
export function madeSinceBuild(d: Pick<Decision, 'id'>, nextId: string | null | undefined): boolean {
  if (!nextId) return false;
  const a = split(d.id);
  const b = split(nextId);
  return !!a && !!b && a[0] === b[0] && a[1] >= b[1];
}

/** The newest live decision made since the build that answers the row, or null. */
export function doneBy(item: Pick<Item, 'sid' | 'findings'>, decisions: Decision[], nextId: string | null): Decision | null {
  let out: Decision | null = null;
  for (const d of decisions) {
    if (madeSinceBuild(d, nextId) && decisionAnswers(item, d)) out = d;
  }
  return out;
}
