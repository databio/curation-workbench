// Bodies of the read routes (views.py on the server; the same JSON locally and
// hosted) and of the hosted vote/decision routes (plan 2, hosted/API.md).

export type State = 'unreviewed' | 'open' | 'deferred' | 'settled' | 'applied' | 'stale';

/** One ready-made answer on a review row (review.json version 2). */
export interface Action {
  label: string;
  verdict: string;
  operation: Record<string, unknown> | null;
  rationale: string;
  evidence: string | null;
  also: string[] | null;
  tone: 'primary' | 'neutral' | 'danger' | null;
  input: 'description' | 'examples' | null;
  confirm: string | null;
  /** The vote answer key (stable hash of verdict, operation, also, evidence). */
  answer: string;
}

export interface Choice {
  text: string;
  detail: string | null;
  actions: Action[];
}

export interface Editor {
  field: 'description' | 'examples';
  value: string;
  placeholder: string;
}

export interface Item {
  sid: string;
  kind: string;
  slug: string | null;
  title: string;
  container: string | null;
  container_label: string | null;
  browse: string | null;
  description: string;
  reasons: string[];
  findings: string[];
  choices: Choice[];
  actions: Action[];
  editor: Editor | null;
  group: string | null;
  hash: string;
  score: number;
}

export interface ReviewTypeMeta {
  id: string;
  title: string;
  question?: string;
  answers?: string;
  help?: string;
  answers_kind?: string;
  count: number;
  /** In the review index only: the SIDs of the type's items, in queue order. */
  sids?: string[];
}

export interface TrackMeta {
  id: string;
  title: string;
  blurb?: string;
  kinds: string[];
  total: number;
}

export interface MoreLink {
  label: string;
  url: string;
}

export interface ReviewIndex {
  version: number;
  title: string;
  schema_fingerprint: string;
  decisions_next_id: string | null;
  progress: { waiting: number; percent_settled: number };
  more_links: MoreLink[];
  tracks: (TrackMeta & { types: ReviewTypeMeta[] })[];
}

export interface ReviewTypePage {
  track: TrackMeta;
  type: ReviewTypeMeta;
  title: string;
  decisions_next_id: string | null;
  items: Item[];
}

export interface SubjectRow {
  kind: string;
  name: string;
  label: string;
  slug: string | null;
  hash: string;
  container: string | null;
  layer: number | null;
  retired: boolean;
  state: State;
  score: number;
}

export type SubjectIndex = Record<string, SubjectRow>;

export interface Finding {
  id: string;
  rule: string;
  severity: 'error' | 'warn' | 'info' | string;
  subjects: string[];
  message: string;
  source?: string;
  suggested_verdict?: string | null;
  object?: string;
  open?: boolean;
}

export interface Evidence {
  kind: string;
  id?: string;
  detail?: string;
}

/** What the browser sends; the server sets id, decided_by/on, status, origin, fingerprint. */
export interface DecisionDraft {
  subject: string;
  verdict: string;
  operation: Record<string, unknown> | null;
  rationale: string;
  also_affects: string[];
  evidence: Evidence[];
  subject_hashes: Record<string, string>;
  supersedes: string | null;
}

export interface Decision extends DecisionDraft {
  id: string;
  decided_by: string;
  decided_on: string;
  schema_fingerprint?: string;
  status: 'pending' | 'applied' | 'withdrawn';
  applied?: {
    on: string | null;
    subject_hashes_after: Record<string, string> | null;
    created: string[];
    retired: string[];
  } | null;
  origin?: { kind: string; ref: string | null } | null;
  /** Hosted only: compare-and-swap revision (If-Match). */
  rev?: number;
}

export interface Panel {
  title: string;
  text?: string;
  links?: { label: string; href?: string | null; sid?: string }[];
  table?: { columns: string[]; rows: unknown[][] };
}

export interface Prompt {
  id?: string;
  title: string;
  text: string;
  source?: string | null;
  suggested_verdict?: string | null;
  suggested_operation?: Record<string, unknown> | null;
  decide_on?: 'owner_class' | null;
}

export interface SubjectView {
  subject: Record<string, unknown> & {
    sid: string;
    kind: string;
    name: string;
    label?: string;
    slug: string;
    hash: string;
    payload: Record<string, unknown>;
    retired?: boolean;
    container?: string | null;
    layer?: number | null;
    file?: string | null;
    owner_class?: string | null;
  };
  status: { state: State; score: number; open_findings: string[]; latest?: string | null };
  findings: Finding[];
  decisions: Decision[];
  aliases: { forward: string | null; former: string[] };
  prompts: Prompt[];
  panels: Panel[];
  browse: string | null;
  /** Hosted only: current votes on this subject. */
  votes?: Vote[];
}

export interface FieldSchema {
  required?: Record<string, string>;
  optional?: Record<string, string>;
  one_of?: string[][];
}

export interface Verdict {
  label: string;
  terminal: boolean;
  writes?: string;
  seed_only?: boolean;
  operation?: FieldSchema;
}

export type Verdicts = Record<string, Record<string, Verdict>>;

export interface HistoryRow extends Decision {
  now: string | null;
  name: string;
  kind: string;
  layer: number | null;
  slug: string | null;
}

export interface PendingDecision extends Decision {
  effects: { created?: string[]; retired?: string[]; renamed?: Record<string, string> } | null;
}

export interface Pending {
  decisions: PendingDecision[];
  errors: Record<string, string[] | string>;
  /** Local only: the dry run's diff per file (absent in hosted mode). */
  files?: { path: string; diff: string }[] | null;
  ok: boolean;
}

export interface ApplyRun {
  running: boolean;
  dry_run: boolean;
  log: string[];
  exit: number | null;
}

export interface Health {
  ok: boolean;
  project: string;
  git_branch: string;
  schema_fingerprint: string;
  decision_count: number;
  working_tree_dirty: boolean;
  decided_by: string;
}

// ---------------------------------------------------------------- hosted (plan 2 step 8)

export interface VoteProposal {
  verdict: string;
  operation?: Record<string, unknown> | null;
  rationale?: string;
  also_affects?: string[];
  evidence?: Evidence[];
}

export interface Vote {
  id: number;
  sid: string;
  review_type: string;
  answer: string;
  proposal: VoteProposal | null;
  comment: string | null;
  user: { id: number; name: string };
  subject_hash: string;
  stale: boolean;
  stale_reason: string | null;
  rev: number;
  created_at: string;
  updated_at: string;
  former_member: boolean;
}

export interface QuestionAnswer {
  answer: string;
  count: number;
  stale: number;
  vote_ids: number[];
  proposal: VoteProposal | null;
}

export interface MyVote {
  vote_id: number;
  answer: string;
  proposal: VoteProposal | null;
  comment: string | null;
  stale: boolean;
}

export interface Question {
  sid: string;
  review_type: string;
  state: 'open' | 'resolved';
  outcome: 'decision' | 'no_action' | null;
  decision_id: string | null;
  answers: QuestionAnswer[];
  total: number;
  stale: number;
  mine: MyVote | null;
}

export interface TeamMember {
  user_id: number;
  name: string;
  email: string;
  role: 'voter' | 'curator';
}

export interface ApprovedUser {
  id: number;
  name: string;
  email: string;
}

export interface Project {
  slug: string;
  title: string;
  role?: string | null;
}

export type Answer = { kind: 'decision'; decision: Decision } | { kind: 'vote'; vote: Vote };
