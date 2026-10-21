import { describe, expect, it } from 'vitest';
import { readApplyStream } from '../src/api/local';
import { lineClass } from '../src/pages/Apply';

function stream(chunks: string[]): ReadableStream<Uint8Array> {
  const enc = new TextEncoder();
  return new ReadableStream({
    start(c) {
      for (const ch of chunks) c.enqueue(enc.encode(ch));
      c.close();
    },
  });
}

describe('apply stream', () => {
  it('passes lines through, split across chunks, and reads the closing exit line', async () => {
    const lines: string[] = [];
    const code = await readApplyStream(stream(['--- a/x\n+++ b', '/x\n+new\n', '\nexit 3\n']), (l) => lines.push(l));
    expect(code).toBe(3);
    expect(lines).toEqual(['--- a/x', '+++ b/x', '+new', '', '']);
  });
  it('is -1 when the stream ends without an exit line; only a whole `exit N` line counts', async () => {
    const lines: string[] = [];
    expect(await readApplyStream(stream(['exit code was 1\nexit -2x\n']), (l) => lines.push(l))).toBe(-1);
    expect(lines.slice(0, 2)).toEqual(['exit code was 1', 'exit -2x']);
    expect(await readApplyStream(stream(['exit -1\n']), () => {})).toBe(-1);
    expect(await readApplyStream(stream(['exit 0']), () => {})).toBe(0);
  });
  it('colors diff lines by prefix', () => {
    expect(lineClass('+++ b/x')).toContain('--file');
    expect(lineClass('+a')).toContain('--add');
    expect(lineClass('-a')).toContain('--del');
    expect(lineClass('@@ -1 +1 @@')).toContain('--hunk');
  });
});
