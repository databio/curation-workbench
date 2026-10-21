// An in-memory Backend for page tests: configurable mode and `can`, every
// method a vi.fn so tests can assert what was sent.
import { vi } from 'vitest';
import type { Backend, Can } from '../src/api/backend';
import type {
  Action, ApprovedUser, Decision, DecisionDraft, HistoryRow, Item, Question, ReviewIndex, ReviewTypePage, SubjectIndex,
  SubjectView, TeamMember, Verdicts, Vote,
} from '../src/api/types';

export const LOCAL: Can = { decide: true, vote: false, apply: true, export: false, adminTeam: false, adminAccounts: false };
export const VOTER: Can = { decide: false, vote: true, apply: false, export: false, adminTeam: false, adminAccounts: false };
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
    title: 'Proj', decisions_next_id: 'DEC-0010', items, ...over,
  };
}

export function decision(over: Partial<Decision> = {}): Decision {
  return {
    id: 'DEC-0010', subject: 'term:T0', verdict: 'keep', operation: null, rationale: 'r', also_affects: [],
    evidence: [], subject_hashes: {}, supersedes: null, decided_by: 'me', decided_on: '2026-10-21', status: 'pending',
    origin: { kind: 'local', ref: null }, ...over,
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

export function fakeBackend(mode: 'local' | 'hosted' = 'local', can: Can = LOCAL, data: FakeData = {}) {
  let id = 10;
  const decisions: Decision[] = [...(data.decisions ?? [])];
  const b = {
    mode,
    project: { slug: 'proj', title: 'Proj' },
    me: { name: 'Tester', role: mode === 'hosted' ? (can.decide ? 'curator' : 'voter') : null },
    can,
    review: vi.fn(async () => data.review!),
    reviewType: vi.fn(async () => data.page!),
    subjectIndex: vi.fn(async () => data.index ?? {}),
    subject: vi.fn(async () => data.subject!),
    verdicts: vi.fn(async () => data.verdicts ?? {}),
    findings: vi.fn(async () => []),
    history: vi.fn(async (): Promise<HistoryRow[]> => []),
    pending: vi.fn(async () => ({ decisions: [], errors: {}, files: [], ok: true })),
    decisions: vi.fn(async () => [...decisions]),
    answer: vi.fn(async (q: { sid: string; reviewType: string; action: Action }, draft: DecisionDraft) => {
      if (mode === 'hosted') {
        return { kind: 'vote' as const, vote: { id: 1, sid: q.sid, review_type: q.reviewType, answer: q.action.answer } as Vote };
      }
      const d = decision({ ...draft, id: `DEC-${String(id++).padStart(4, '0')}` });
      decisions.push(d);
      return { kind: 'decision' as const, decision: d };
    }),
    undo: vi.fn(async (a: { kind: string; decision?: Decision }) => {
      if (a.kind === 'decision') {
        const d = decisions.find((x) => x.id === a.decision!.id);
        if (d) d.status = 'withdrawn';
      }
    }),
    questions: mode === 'hosted' ? vi.fn(async () => data.questions ?? []) : undefined,
    votes: mode === 'hosted' ? vi.fn(async () => data.votes ?? []) : undefined,
    decide: vi.fn(async (draft: DecisionDraft) => {
      const d = decision({ ...draft, id: `DEC-${String(id++).padStart(4, '0')}` });
      decisions.push(d);
      return d;
    }),
    resolve: mode === 'hosted' ? vi.fn(async () => null) : undefined,
    withdraw: vi.fn(async () => undefined),
    team: vi.fn(async (): Promise<TeamMember[]> => []),
    setMember: vi.fn(async () => undefined),
    removeMember: vi.fn(async () => undefined),
    approvedUsers: vi.fn(async (): Promise<ApprovedUser[]> => []),
  };
  return b as typeof b & Backend;
}
