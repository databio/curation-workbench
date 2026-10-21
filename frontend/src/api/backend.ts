// The one seam between pages and a server. Local mode (local.ts) talks to the
// loopback daemon (`cwb serve`); hosted mode (hosted.ts) to the hosted API.
// Pages check `can`, never the mode string, except for wording ("Save" vs
// "Vote").
import { createContext, useContext } from 'react';
import type {
  Action,
  Answer,
  ApplyRun,
  ApprovedUser,
  Decision,
  DecisionDraft,
  Finding,
  Health,
  HistoryRow,
  Pending,
  Question,
  ReviewIndex,
  ReviewTypePage,
  SubjectIndex,
  SubjectView,
  TeamMember,
  Verdicts,
  Vote,
} from './types';

export interface Can {
  decide: boolean;
  vote: boolean;
  apply: boolean;
  export: boolean;
  adminTeam: boolean;
  adminAccounts: boolean;
}

export interface AnswerQuestion {
  sid: string;
  /** `"<track>/<type>"`, or `"subject"` from a subject page. */
  reviewType: string;
  action: Action;
  /** The hash of the subject the person saw (the item's `hash`). */
  hash: string;
  /** Hosted: an optional comment sent with the vote. */
  comment?: string;
}

export interface ResolveRequest {
  sid: string;
  reviewType: string;
  outcome: 'decision' | 'no_action';
  fromVote?: number;
  decision?: DecisionDraft;
  note?: string;
}

export interface Backend {
  mode: 'local' | 'hosted';
  project: { slug: string; title: string };
  me: { name: string; role?: string | null };
  can: Can;
  review(): Promise<ReviewIndex>;
  reviewType(track: string, type: string): Promise<ReviewTypePage>;
  subjectIndex(): Promise<SubjectIndex>;
  subject(sid: string): Promise<SubjectView>;
  verdicts(): Promise<Verdicts>;
  findings(): Promise<Finding[]>;
  history(): Promise<HistoryRow[]>;
  pending(): Promise<Pending>;
  /** The live decision store, for done-state. */
  decisions(): Promise<Decision[]>;
  /** Local: a decision. Hosted: a vote. */
  answer(q: AnswerQuestion, draft: DecisionDraft): Promise<Answer>;
  /** Local: withdraw the decision. Hosted: retract the vote. */
  undo(answer: Answer): Promise<void>;
  questions?(reviewType?: string, sid?: string): Promise<Question[]>;
  votes?(sid: string, reviewType: string): Promise<Vote[]>;
  /** A direct decision (can.decide). */
  decide(draft: DecisionDraft): Promise<Decision>;
  resolve?(q: ResolveRequest): Promise<Decision | null>;
  withdraw(d: Decision): Promise<void>;
  apply?(opts: { dryRun: boolean; onLine: (line: string) => void }): Promise<number>;
  applyStatus?(): Promise<ApplyRun>;
  /** Local: the daemon's health (branch, dirty tree, fingerprint). */
  health?(): Promise<Health>;
  /** Hosted, with can.export: where the pending decisions download from. */
  exportUrl?: string;
  team?(): Promise<TeamMember[]>;
  setMember?(userId: number, role: 'voter' | 'curator'): Promise<void>;
  removeMember?(userId: number): Promise<void>;
  approvedUsers?(): Promise<ApprovedUser[]>;
}

/** A server error: `status` is the HTTP code (409: the store or a subject changed). */
export class CurationError extends Error {
  status: number;
  messages: string[];
  code?: string;
  body: unknown;

  constructor(status: number, messages: string[], code?: string, body?: unknown) {
    super(messages.join('; ') || `Request failed (${status})`);
    this.name = 'CurationError';
    this.status = status;
    this.messages = messages;
    this.code = code;
    this.body = body;
  }
}

/** The message a page shows when a save fails (409 gets the reload hint). */
export function saveErrorMessage(e: unknown): string {
  if (e instanceof CurationError && e.status === 409) return `Conflict: ${e.message}. Reload the page.`;
  return `Not saved: ${(e as Error)?.message ?? String(e)}`;
}

export const BackendContext = createContext<Backend | null>(null);

export function useBackend(): Backend {
  const b = useContext(BackendContext);
  if (!b) throw new Error('useBackend must be used inside a <BackendContext.Provider>');
  return b;
}

/** Fired on `window` after any answer, undo, decide, resolve or withdraw. */
export const DECISION_EVENT = 'cwb:decision';

export function notifyDecision(): void {
  window.dispatchEvent(new CustomEvent(DECISION_EVENT));
}
