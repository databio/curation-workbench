// The hosted API (hosted/API.md). The only module that knows its routes. Requests carry snapgate's bearer token and retry once on
// a 401 through snapgate's single-flight `refreshAccessToken` (two concurrent
// refreshes would rotate the refresh token twice and end the session), as
// Pigeon's api/client.ts does. Errors `{detail, code, errors?}` become a
// `CurationError`.
import { ACCESS_TOKEN_KEY, ACCESS_EXPIRES_AT_KEY, REFRESH_TOKEN_KEY, refreshAccessToken } from '../snapgate';
import { CurationError, type Backend, type Can } from './backend';
import type {
  ApprovedUser,
  Decision,
  DecisionDraft,
  Finding,
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

export interface Me {
  user: { id: number; name: string; email: string };
  site_role: 'superadmin' | 'admin' | null;
  memberships: Record<string, 'voter' | 'curator'>;
}

export interface HostedProject {
  slug: string;
  name: string;
  role: 'voter' | 'curator' | null;
  commit: string | null;
  schema_fingerprint: string | null;
  synced_at: string | null;
}

type FetchImpl = typeof fetch;

function token(): string | null {
  try {
    return localStorage.getItem(ACCESS_TOKEN_KEY);
  } catch {
    return null;
  }
}

function clearTokens(): void {
  try {
    localStorage.removeItem(ACCESS_TOKEN_KEY);
    localStorage.removeItem(REFRESH_TOKEN_KEY);
    localStorage.removeItem(ACCESS_EXPIRES_AT_KEY);
  } catch {
    // storage blocked
  }
}

function toLogin(): void {
  const returnTo = encodeURIComponent(window.location.pathname + window.location.search);
  window.location.href = `/login?returnTo=${returnTo}`;
}

/** A same-origin JSON client with the bearer token and one refresh-and-retry on 401. */
export function createHostedClient(fetchImpl: FetchImpl = (...a) => fetch(...a)) {
  async function once(path: string, init: RequestInit): Promise<Response> {
    const headers = new Headers(init.headers);
    const t = token();
    if (t) headers.set('Authorization', `Bearer ${t}`);
    if (typeof init.body === 'string' && !headers.has('Content-Type')) headers.set('Content-Type', 'application/json');
    return fetchImpl(path, { ...init, headers, credentials: 'include' });
  }

  async function request<T>(method: string, path: string, body?: unknown, headers?: Record<string, string>): Promise<T> {
    const init: RequestInit = { method, headers, body: body === undefined ? undefined : JSON.stringify(body) };
    let res = await once(path, init);
    if (res.status === 401) {
      let ok = false;
      try {
        ok = !!(await refreshAccessToken(''));
      } catch {
        ok = false;
      }
      if (!ok) {
        clearTokens();
        toLogin();
        throw new CurationError(401, ['Signed out'], 'unauthorized');
      }
      res = await once(path, init);
    }
    if (res.status === 204) return undefined as T;
    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
      const errors: string[] = Array.isArray(data?.errors) ? data.errors
        : typeof data?.detail === 'string' ? [data.detail] : [res.statusText || `Request failed (${res.status})`];
      throw new CurationError(res.status, errors, data?.code, data);
    }
    return data as T;
  }

  return { request };
}

export function canFor(me: Me, slug: string): Can {
  const role = me.memberships[slug];
  const superadmin = me.site_role === 'superadmin';
  return {
    vote: !!role,
    decide: role === 'curator',
    adminTeam: superadmin,
    adminAccounts: superadmin,
  };
}

const q = (params: Record<string, string | undefined>) => {
  const s = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) if (v !== undefined && v !== '') s.append(k, v);
  const text = s.toString();
  return text ? `?${text}` : '';
};

export function createHostedBackend(me: Me, project: HostedProject, fetchImpl?: FetchImpl): Backend {
  const { request } = createHostedClient(fetchImpl);
  const slug = project.slug;
  const base = `/api/v1/projects/${encodeURIComponent(slug)}`;
  const admin = `/api/v1/admin/projects/${encodeURIComponent(slug)}/team`;
  const get = <T,>(path: string) => request<T>('GET', `${base}/${path}`);

  return {
    project: { slug, title: project.name },
    me: { name: me.user.name, role: me.site_role === 'superadmin' ? 'superadmin' : me.memberships[slug] ?? null },
    can: canFor(me, slug),
    review: () => get<ReviewIndex>('review'),
    reviewType: (track, type) => get<ReviewTypePage>(`review/${encodeURIComponent(track)}/${encodeURIComponent(type)}`),
    subjectIndex: () => get<SubjectIndex>('subject_index'),
    subject: (sid) => get<SubjectView>(`subject${q({ sid })}`),
    verdicts: () => get<Verdicts>('verdicts'),
    findings: async () => (await get<{ findings: Finding[] }>('findings')).findings,
    history: async () => (await get<{ history: HistoryRow[] }>('history')).history,
    pending: () => get<Pending>('pending'),
    decisions: async () => (await get<{ decisions: Decision[] }>('decisions')).decisions,
    answer: async ({ sid, reviewType, action, hash, comment }, draft) => {
      const body: Record<string, unknown> = {
        sid,
        review_type: reviewType,
        answer: action.answer,
        subject_hash: hash,
        proposal: {
          verdict: draft.verdict,
          operation: draft.operation,
          rationale: draft.rationale,
          also_affects: draft.also_affects,
          evidence: draft.evidence,
        },
      };
      if (comment) body.comment = comment;
      const out = await request<{ vote: Vote }>('PUT', `${base}/votes`, body);
      return { kind: 'vote', vote: out.vote };
    },
    undo: async (a) => {
      await request<void>('DELETE', `${base}/votes${q({ sid: a.vote.sid, review_type: a.vote.review_type })}`);
    },
    questions: async (reviewType, sid) =>
      (await get<{ questions: Question[] }>(`questions${q({ review_type: reviewType, sid })}`)).questions,
    votes: async (sid, reviewType) => (await get<{ votes: Vote[] }>(`votes${q({ sid, review_type: reviewType })}`)).votes,
    decide: async (draft: DecisionDraft) => (await request<{ decision: Decision }>('POST', `${base}/decisions`, draft)).decision,
    resolve: async ({ sid, reviewType, outcome, fromVote, decision, note }) => {
      const body: Record<string, unknown> = { sid, review_type: reviewType, outcome };
      if (fromVote !== undefined) body.from_vote = fromVote;
      if (decision !== undefined) body.decision = decision;
      if (note) body.note = note;
      return (await request<{ decision: Decision | null }>('POST', `${base}/questions/resolve`, body)).decision;
    },
    withdraw: async (d) => {
      await request<{ decision: Decision }>('DELETE', `${base}/decisions/${encodeURIComponent(d.id)}`, undefined, {
        'If-Match': String(d.rev ?? ''),
      });
    },
    makeRepoKey: me.memberships[slug] === 'curator'
      ? async () => {
        const out = await request<{ api_key: string }>('POST', '/auth/api-keys', {
          name: `${slug} repository (curate Action)`,
          description: `Read-only key for ${slug}'s curate GitHub Action (repository secret CURATE_API_KEY).`,
          scopes: ['curation:read'],
        });
        return out.api_key;
      }
      : undefined,
    team: async () => (await get<{ members: TeamMember[] }>('team')).members,
    setMember: async (userId, role) => {
      await request('PUT', `${admin}/${userId}`, { role });
    },
    removeMember: async (userId) => {
      await request('DELETE', `${admin}/${userId}`);
    },
    approvedUsers: async () => {
      const out = await request<{ users?: ApprovedUser[] } | ApprovedUser[]>('GET', '/auth/admin/users?status=approved');
      return Array.isArray(out) ? out : out.users ?? [];
    },
  };
}

/** Load `/api/v1/me` and the project, then build the backend. */
export async function loadHostedBackend(slug: string, fetchImpl?: FetchImpl): Promise<Backend> {
  const { request } = createHostedClient(fetchImpl);
  const [me, project] = await Promise.all([
    request<Me>('GET', '/api/v1/me'),
    request<HostedProject>('GET', `/api/v1/projects/${encodeURIComponent(slug)}`),
  ]);
  return createHostedBackend(me, project, fetchImpl);
}

export async function listHostedProjects(fetchImpl?: FetchImpl): Promise<HostedProject[]> {
  const { request } = createHostedClient(fetchImpl);
  return (await request<{ projects: HostedProject[] }>('GET', '/api/v1/projects')).projects;
}
