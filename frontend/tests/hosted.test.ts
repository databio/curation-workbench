// hosted.ts against plan 2's routes (hosted/API.md): every method sends
// exactly the documented method, path, headers and body, and unwraps the
// documented response key.
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { CurationError } from '../src/api/backend';
import { canFor, createHostedBackend, type HostedProject, type Me } from '../src/api/hosted';
import type { Action, Decision, DecisionDraft } from '../src/api/types';
import { ACCESS_TOKEN_KEY } from '../src/snapgate';

const ME: Me = { user: { id: 1, name: 'Ann', email: 'a@x' }, site_role: null, memberships: { onga: 'curator' } };
const PROJECT: HostedProject = { slug: 'onga', name: 'ONGA proj', role: 'curator', commit: 'abc', schema_fingerprint: 'fp', synced_at: null };
const BASE = '/api/v1/projects/onga';

interface Call { method: string; path: string; headers: Headers; body: unknown }

function setup(responses: Record<string, { status?: number; body?: unknown }>) {
  const calls: Call[] = [];
  const fetchImpl = vi.fn(async (path: string, init: RequestInit) => {
    const method = init.method ?? 'GET';
    calls.push({ method, path, headers: new Headers(init.headers), body: init.body ? JSON.parse(String(init.body)) : undefined });
    const r = responses[`${method} ${path}`] ?? { status: 404, body: { detail: `no ${method} ${path}`, code: 'not_found' } };
    const status = r.status ?? 200;
    return new Response(status === 204 ? null : JSON.stringify(r.body ?? {}), { status, headers: { 'Content-Type': 'application/json' } });
  });
  const backend = createHostedBackend(ME, PROJECT, fetchImpl as unknown as typeof fetch);
  return { backend, calls };
}

const draft: DecisionDraft = {
  subject: 'term:A', verdict: 'keep', operation: null, rationale: 'r', also_affects: ['term:B'],
  evidence: [{ kind: 'finding', id: 'f:1' }], subject_hashes: { 'term:A': 'h' }, supersedes: null,
};
const action: Action = {
  label: 'Keep', verdict: 'keep', operation: null, rationale: 'r', evidence: 'f:1', also: ['term:B'], tone: 'primary',
  input: null, confirm: null, answer: '0123456789ab',
};

beforeEach(() => localStorage.setItem(ACCESS_TOKEN_KEY, 'tok'));

describe('hosted backend', () => {
  it('reads: same-named routes, bearer token, unwrapped keys', async () => {
    const { backend, calls } = setup({
      [`GET ${BASE}/review`]: { body: { tracks: [] } },
      [`GET ${BASE}/review/vocabulary/links`]: { body: { items: [] } },
      [`GET ${BASE}/subject_index`]: { body: { 'term:A': {} } },
      [`GET ${BASE}/subject?sid=term%3AA%2Fx`]: { body: { subject: {} } },
      [`GET ${BASE}/verdicts`]: { body: { term: {} } },
      [`GET ${BASE}/findings`]: { body: { findings: [{ id: 'f' }] } },
      [`GET ${BASE}/history`]: { body: { history: [{ id: 'H-0001' }] } },
      [`GET ${BASE}/pending`]: { body: { decisions: [] } },
      [`GET ${BASE}/decisions`]: { body: { version: 1, next_id: 'H-0002', decisions: [{ id: 'H-0001' }], token: 't' } },
      [`GET ${BASE}/questions?review_type=vocabulary%2Flinks&sid=term%3AA`]: { body: { questions: [{ sid: 'term:A' }] } },
      [`GET ${BASE}/votes?sid=term%3AA&review_type=vocabulary%2Flinks`]: { body: { votes: [{ id: 3 }] } },
      [`GET ${BASE}/team`]: { body: { members: [{ user_id: 2 }] } },
      ['GET /auth/admin/users?status=approved']: { body: { users: [{ id: 2 }], total: 1 } },
    });
    expect(await backend.review()).toEqual({ tracks: [] });
    expect(await backend.reviewType('vocabulary', 'links')).toEqual({ items: [] });
    expect(await backend.subjectIndex()).toEqual({ 'term:A': {} });
    expect(await backend.subject('term:A/x')).toEqual({ subject: {} });
    expect(await backend.verdicts()).toEqual({ term: {} });
    expect(await backend.findings()).toEqual([{ id: 'f' }]);
    expect(await backend.history()).toEqual([{ id: 'H-0001' }]);
    expect(await backend.pending()).toEqual({ decisions: [] });
    expect(await backend.decisions()).toEqual([{ id: 'H-0001' }]);
    expect(await backend.questions!('vocabulary/links', 'term:A')).toEqual([{ sid: 'term:A' }]);
    expect(await backend.votes!('term:A', 'vocabulary/links')).toEqual([{ id: 3 }]);
    expect(await backend.team!()).toEqual([{ user_id: 2 }]);
    expect(await backend.approvedUsers!()).toEqual([{ id: 2 }]);
    expect(calls.every((c) => c.method === 'GET' && c.headers.get('Authorization') === 'Bearer tok' && c.body === undefined)).toBe(true);
  });

  it('answer: PUT votes with the action answer key, the item hash and the proposal', async () => {
    const { backend, calls } = setup({ [`PUT ${BASE}/votes`]: { body: { vote: { id: 5, sid: 'term:A' } } } });
    const out = await backend.answer({ sid: 'term:A', reviewType: 'vocabulary/links', action, hash: 'h', comment: 'why' }, draft);
    expect(out).toEqual({ kind: 'vote', vote: { id: 5, sid: 'term:A' } });
    expect(calls[0].body).toEqual({
      sid: 'term:A', review_type: 'vocabulary/links', answer: '0123456789ab', subject_hash: 'h',
      proposal: { verdict: 'keep', operation: null, rationale: 'r', also_affects: ['term:B'], evidence: [{ kind: 'finding', id: 'f:1' }] },
      comment: 'why',
    });
    expect(calls[0].headers.get('Content-Type')).toBe('application/json');
    await backend.answer({ sid: 'term:A', reviewType: 'subject', action: { ...action, answer: 'keep' }, hash: 'h' }, draft);
    expect(calls[1].body).toMatchObject({ review_type: 'subject', answer: 'keep' });
    expect(calls[1].body).not.toHaveProperty('comment');
  });

  it('undo: DELETE votes?sid=&review_type= (204)', async () => {
    const { backend, calls } = setup({ [`DELETE ${BASE}/votes?sid=term%3AA&review_type=vocabulary%2Flinks`]: { status: 204 } });
    await backend.undo({ kind: 'vote', vote: { sid: 'term:A', review_type: 'vocabulary/links' } as never });
    expect(calls).toHaveLength(1);
    expect(calls[0].body).toBeUndefined();
  });

  it('decide, resolve, withdraw (If-Match)', async () => {
    const d = { id: 'H-0003', rev: 4 } as Decision;
    const { backend, calls } = setup({
      [`POST ${BASE}/decisions`]: { status: 201, body: { decision: { id: 'H-0003' } } },
      [`POST ${BASE}/questions/resolve`]: { body: { question: {}, decision: { id: 'H-0004' } } },
      [`DELETE ${BASE}/decisions/H-0003`]: { body: { decision: { id: 'H-0003', status: 'withdrawn' } } },
    });
    expect(await backend.decide(draft)).toEqual({ id: 'H-0003' });
    expect(calls[0].body).toEqual(draft);
    expect(await backend.resolve!({ sid: 'term:A', reviewType: 'v/l', outcome: 'decision', fromVote: 7 })).toEqual({ id: 'H-0004' });
    expect(calls[1].body).toEqual({ sid: 'term:A', review_type: 'v/l', outcome: 'decision', from_vote: 7 });
    await backend.resolve!({ sid: 'term:A', reviewType: 'v/l', outcome: 'decision', decision: draft, note: 'n' });
    expect(calls[2].body).toEqual({ sid: 'term:A', review_type: 'v/l', outcome: 'decision', decision: draft, note: 'n' });
    await backend.resolve!({ sid: 'term:A', reviewType: 'v/l', outcome: 'no_action' });
    expect(calls[3].body).toEqual({ sid: 'term:A', review_type: 'v/l', outcome: 'no_action' });
    await backend.withdraw(d);
    expect(calls[4].method).toBe('DELETE');
    expect(calls[4].headers.get('If-Match')).toBe('4');
  });

  it('team admin: PUT/DELETE /api/v1/admin/projects/{slug}/team/{user_id}', async () => {
    const { backend, calls } = setup({
      ['PUT /api/v1/admin/projects/onga/team/2']: { body: { member: {} } },
      ['DELETE /api/v1/admin/projects/onga/team/2']: { status: 204 },
    });
    await backend.setMember!(2, 'curator');
    expect(calls[0].body).toEqual({ role: 'curator' });
    await backend.removeMember!(2);
    expect(calls[1].method).toBe('DELETE');
  });

  it('errors {detail, code, errors?} become CurationError', async () => {
    const { backend } = setup({
      [`PUT ${BASE}/votes`]: { status: 409, body: { detail: 'subject changed', code: 'conflict', errors: ['subject changed', 'x'], current: {} } },
      [`POST ${BASE}/decisions`]: { status: 403, body: { detail: 'curators only', code: 'not_curator' } },
    });
    const e1 = await backend.answer({ sid: 'term:A', reviewType: 'v/l', action, hash: 'h' }, draft).catch((e) => e);
    expect(e1).toBeInstanceOf(CurationError);
    expect(e1).toMatchObject({ status: 409, code: 'conflict', messages: ['subject changed', 'x'] });
    const e2 = await backend.decide(draft).catch((e) => e);
    expect(e2).toMatchObject({ status: 403, code: 'not_curator', messages: ['curators only'] });
  });

  it('can comes from /api/v1/me', () => {
    expect(canFor(ME, 'onga')).toEqual({ vote: true, decide: true, apply: false, export: false, adminTeam: false, adminAccounts: false });
    expect(canFor({ ...ME, memberships: { onga: 'voter' } }, 'onga')).toMatchObject({ vote: true, decide: false });
    expect(canFor({ ...ME, memberships: {}, site_role: 'superadmin' }, 'onga')).toMatchObject({ vote: false, decide: false, adminTeam: true, adminAccounts: true });
  });
});
