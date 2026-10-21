// Local mode: the loopback daemon (`cwb serve`) at the same origin. Ported
// from the first project's Astro curation.ts: `call()` tracks the store's
// `X-Store-Mtime` so a write made against an older read gets 409, and the
// apply stream is parsed line by line with the daemon's closing `exit N`.
import { CurationError, type Backend, type Can } from './backend';
import type {
  ApplyRun,
  Decision,
  DecisionDraft,
  Finding,
  Health,
  HistoryRow,
  Pending,
  ReviewIndex,
  ReviewTypePage,
  SubjectIndex,
  SubjectView,
  Verdicts,
} from './types';

export const LOCAL_CAN: Can = { decide: true, vote: false, apply: true, export: false, adminTeam: false, adminAccounts: false };

export interface LocalConfig {
  mode: 'local';
  projects: { slug: string; title: string }[];
  user: { name: string };
}

export function createLocalBackend(config: LocalConfig, fetchImpl: typeof fetch = (...a) => fetch(...a)): Backend {
  let mtime: string | null = null;

  async function call<T>(method: string, path: string, body?: unknown): Promise<T> {
    const headers: Record<string, string> = { Accept: 'application/json' };
    if (body !== undefined) headers['Content-Type'] = 'application/json';
    if (method !== 'GET' && mtime) headers['X-Store-Mtime'] = mtime;
    const res = await fetchImpl(path, {
      method,
      headers,
      body: body === undefined ? undefined : JSON.stringify(body),
    });
    const m = res.headers.get('X-Store-Mtime');
    if (m) mtime = m;
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new CurationError(res.status, data.errors || [res.statusText], undefined, data);
    return data as T;
  }

  const save = async (draft: DecisionDraft) => (await call<{ decision: Decision }>('POST', '/api/decisions', draft)).decision;
  const withdraw = async (id: string) => {
    await call<{ decision: Decision }>('DELETE', `/api/decisions/${encodeURIComponent(id)}`);
  };
  const project = config.projects[0];

  return {
    mode: 'local',
    project: { slug: project.slug, title: project.title },
    me: { name: config.user.name },
    can: LOCAL_CAN,
    review: () => call<ReviewIndex>('GET', '/api/review'),
    reviewType: (track, type) =>
      call<ReviewTypePage>('GET', `/api/review/${encodeURIComponent(track)}/${encodeURIComponent(type)}`),
    subjectIndex: () => call<SubjectIndex>('GET', '/api/subject_index'),
    subject: (sid) => call<SubjectView>('GET', `/api/subject?sid=${encodeURIComponent(sid)}`),
    verdicts: () => call<Verdicts>('GET', '/api/verdicts'),
    findings: async () => (await call<{ findings: Finding[] }>('GET', '/api/findings')).findings,
    history: async () => (await call<{ history: HistoryRow[] }>('GET', '/api/history')).history,
    pending: () => call<Pending>('GET', '/api/pending'),
    decisions: async () => (await call<{ decisions: Decision[] }>('GET', '/api/decisions')).decisions,
    answer: async (_q, draft) => ({ kind: 'decision', decision: await save(draft) }),
    undo: async (a) => {
      if (a.kind === 'decision') await withdraw(a.decision.id);
    },
    decide: save,
    withdraw: (d) => withdraw(d.id),
    health: () => call<Health>('GET', '/api/health'),
    applyStatus: () => call<ApplyRun>('GET', '/api/apply'),
    apply: async ({ dryRun, onLine }) => {
      const res = await fetchImpl('/api/apply', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ dry_run: dryRun }),
      });
      if (!res.ok || !res.body) {
        const data = await res.json().catch(() => ({}));
        throw new CurationError(res.status, data.errors || [res.statusText]);
      }
      return readApplyStream(res.body, onLine);
    },
  };
}

/**
 * Feed an apply stream to `onLine` line by line; resolves to the exit code
 * from the daemon's closing `exit N` line (-1 when it never came).
 */
export async function readApplyStream(body: ReadableStream<Uint8Array>, onLine: (line: string) => void): Promise<number> {
  const reader = body.getReader();
  const decoder = new TextDecoder();
  let buf = '';
  let code = -1;
  for (;;) {
    const { value, done } = await reader.read();
    if (value) buf += decoder.decode(value, { stream: !done });
    const lines = done ? buf.split('\n') : buf.split('\n').slice(0, -1);
    buf = done ? '' : buf.slice(buf.lastIndexOf('\n') + 1);
    for (const line of lines) {
      const m = /^exit (-?\d+)$/.exec(line);
      if (m) code = Number(m[1]);
      else onLine(line);
    }
    if (done) break;
  }
  return code;
}
