import { describe, expect, it, vi } from 'vitest';
import { CurationError } from '../src/api/backend';
import { createLocalBackend } from '../src/api/local';
import { decision } from './fakeBackend';

describe('local backend', () => {
  it('sends the last X-Store-Mtime on writes and maps routes', async () => {
    const calls: [string, RequestInit][] = [];
    const fetchImpl = vi.fn(async (path: string, init: RequestInit) => {
      calls.push([path, init]);
      const body = path === '/api/decisions' && init.method === 'GET' ? { decisions: [decision()] }
        : init.method === 'POST' ? { decision: decision({ id: 'DEC-0011' }) } : init.method === 'DELETE' ? { errors: ['changed'] } : {};
      return new Response(JSON.stringify(body), {
        status: init.method === 'DELETE' ? 409 : 200,
        headers: { 'Content-Type': 'application/json', 'X-Store-Mtime': '123' },
      });
    });
    const b = createLocalBackend({ mode: 'local', projects: [{ slug: 'p', title: 'P' }], user: { name: 'me' } }, fetchImpl as never);
    expect(b.project).toEqual({ slug: 'p', title: 'P' });
    expect(await b.decisions()).toHaveLength(1);
    const out = await b.answer({ sid: 's', reviewType: 't/u', action: {} as never, hash: 'h' }, decision());
    expect(out).toEqual({ kind: 'decision', decision: expect.objectContaining({ id: 'DEC-0011' }) });
    expect((calls[1][1].headers as Record<string, string>)['X-Store-Mtime']).toBe('123');
    const err = await b.withdraw(decision({ id: 'DEC-0011' })).catch((e) => e);
    expect(err).toBeInstanceOf(CurationError);
    expect(err.status).toBe(409);
    expect(calls[2][0]).toBe('/api/decisions/DEC-0011');
    await b.subject('term:A/b').catch(() => null);
    expect(calls[3][0]).toBe('/api/subject?sid=term%3AA%2Fb');
  });
});
