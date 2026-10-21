// An in-memory Backend for page tests: configurable `can`, every method a
// vi.fn so tests can assert what was sent. A vote updates the question list
// (`mine`), as the server would.
import { vi } from 'vitest';
import type { Backend, Can } from '../src/api/backend';
import type {
  Action, ApprovedUser, Decision, DecisionDraft, HistoryRow, Item, Pending, Question, ReviewIndex, ReviewTypePage,
  SubjectIndex, SubjectView, TeamMember, Verdicts, Vote,
} from '../src/api/types';

export const VOTER: Can = { decide: false, vote: true, adminTeam: false, adminAccounts: false };
export const CURATOR: Can = { ...VOTER, decide: true };

let n = 0;
export function action(over: Partial<Action> = {}): Action {
  n += 1;
  return {
    label: 'Looks right', verdict: 'keep', operation: null, rationale: 'Reviewed; no problems found.',
    evidence: null, also: null, tone: 'primary', input: null, confirm: null, answer: `a${n.toString(16).padStart(11, '0')}`,
    ...over,
  };
}

export function item(i: number, over: Partial<Item> = {}): Item {
  return {
    sid: `term:T${i}`, kind: 'term', slug: `term-t${i}`, title: `term ${i}`, container: 'enum:E', container_label: 'E',
    browse: null, description: `Description ${i}`, reasons: [`Reason ${i}`], findings: [`f:${i}`], choices: [],
    actions: [action({ evidence: `f:${i}` })], editor: null, group: null, hash: `h${i}`, score: 1, ...over,
  };
}

export function page(items: Item[], over: Partial<ReviewTypePage> = {}): ReviewTypePage {
  return {
    track: { id: 'vocabulary', title: 'Vocabulary', kinds: ['term'], total: items.length },
    type: { id: 'signoff', title: 'Sign-off', question: 'Is this right?', help: 'Help text.', count: items.length },
    title: 'Proj', decisions_next_id: 'H-0010', items, ...over,
  };
}

export function decision(over: Partial<Decision> = {}): Decision {
  return {
    id: 'H-0010', subject: 'term:T0', verdict: 'keep', operation: null, rationale: 'r', also_affects: [],
    evidence: [], subject_hashes: {}, supersedes: null, decided_by: 'me', decided_on: '2026-10-21', status: 'pending',
    origin: { kind: 'hosted', ref: over.id ?? 'H-0010' }, ...over,
  };
}

export interface FakeData {
  review?: ReviewIndex;
  page?: ReviewTypePage;
  index?: SubjectIndex;
  subject?: SubjectView;
  verdicts?: Verdicts;
  decisions?: Decision[];
  questions?: Question[];
  votes?: Vote[];
}

export function fakeBackend(can: Can = CURATOR, data: FakeData = {}) {
  let id = 10;
  let voteId = 100;
  const decisions: Decision[] = [...(data.decisions ?? [])];
  const questions: Question[] = (data.questions ?? []).map((q) => ({ ...q }));
  const b = {
    project: { slug: 'proj', title: 'Proj' },
    me: { name: 'Tester', role: can.decide ? 'curator' : 'voter' },
    can,
    review: vi.fn(async () => data.review!),
    reviewType: vi.fn(async () => data.page!),
    subjectIndex: vi.fn(async () => data.index ?? {}),
    subject: vi.fn(async () => data.subject!),
    verdicts: vi.fn(async () => data.verdicts ?? {}),
    findings: vi.fn(async () => []),
    history: vi.fn(async (): Promise<HistoryRow[]> => []),
    pending: vi.fn(async (): Promise<Pending> => ({ decisions: [], errors: {}, ok: true })),
    decisions: vi.fn(async () => [...decisions]),
    answer: vi.fn(async (q: { sid: string; reviewType: string; action: Action }, draft: DecisionDraft) => {
      const vote = { id: voteId++, sid: q.sid, review_type: q.reviewType, answer: q.action.answer } as Vote;
      const mine = { vote_id: vote.id, answer: q.action.answer, proposal: { verdict: draft.verdict }, comment: null, stale: false };
      const existing = questions.find((x) => x.sid === q.sid && x.review_type === q.reviewType);
      if (existing) existing.mine = mine;
      else {
        questions.push({ sid: q.sid, review_type: q.reviewType, state: 'open', outcome: null, decision_id: null,
          answers: [], total: 0, stale: 0, mine });
      }
      return { kind: 'vote' as const, vote };
    }),
    undo: vi.fn(async (a: { kind: string; vote: Vote }) => {
      const q = questions.find((x) => x.sid === a.vote.sid && x.review_type === a.vote.review_type);
      if (q) q.mine = null;
    }),
    questions: vi.fn(async () => questions.map((q) => ({ ...q }))),
    votes: vi.fn(async () => data.votes ?? []),
    decide: vi.fn(async (draft: DecisionDraft) => {
      const d = decision({ ...draft, id: `H-${String(id++).padStart(4, '0')}` });
      decisions.push(d);
      return d;
    }),
    resolve: vi.fn(async () => null),
    withdraw: vi.fn(async () => undefined),
    team: vi.fn(async (): Promise<TeamMember[]> => []),
    setMember: vi.fn(async () => undefined),
    removeMember: vi.fn(async () => undefined),
    approvedUsers: vi.fn(async (): Promise<ApprovedUser[]> => []),
  };
  return b as typeof b & Backend;
}
