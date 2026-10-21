// The review UI names no project: no project name, ontology, predicate or
// rule id in frontend/src (snapgate, vendored, excluded). Project wording
// comes from review_types.yaml and plugins, through the API.
import { existsSync, readdirSync, readFileSync, statSync } from 'node:fs';
import { join, relative } from 'node:path';
import { describe, expect, it } from 'vitest';

const SRC = join(__dirname, '..', 'src');
// ONGA's review types, in ONGA's checkout (ONGA_ROOT, default ../onga next to this repo).
const ONGA_ROOT = process.env.ONGA_ROOT ?? join(__dirname, '..', '..', '..', 'onga');
const ADAPTER = join(ONGA_ROOT, 'curation', 'review_types.yaml');

function files(dir: string): string[] {
  return readdirSync(dir).flatMap((name) => {
    const p = join(dir, name);
    if (relative(SRC, p).startsWith('snapgate')) return [];
    return statSync(p).isDirectory() ? files(p) : [p];
  });
}

/** Rule ids named in ONGA's review types (skipped when they are not reachable). */
function ruleIds(): string[] {
  if (!existsSync(ADAPTER)) return [];
  const text = readFileSync(ADAPTER, 'utf8');
  const ids = new Set<string>();
  for (const m of text.matchAll(/rules: \[([^\]]*)\]/g)) for (const r of m[1].split(',')) if (r.trim()) ids.add(r.trim());
  const reasons = /\nreasons:\n((?: {2}.*\n)+)/.exec(text);
  for (const m of reasons?.[1].matchAll(/^ {2}([a-z0-9-]+):/gm) ?? []) ids.add(m[1]);
  return [...ids];
}

describe('no project names in the UI', () => {
  it('finds none', () => {
    const words = [/\bonga\b/i, /\bencode\b/i, /\bedam\b/i, /\bpato\b/i, / SO /, /has_element_type/];
    const rules = ruleIds();
    expect(rules.length === 0 || rules.includes('mapping-suggestion')).toBe(true);
    const hits: string[] = [];
    for (const f of files(SRC)) {
      const text = readFileSync(f, 'utf8');
      for (const w of words) if (w.test(text)) hits.push(`${relative(SRC, f)}: ${w}`);
      for (const r of rules) if (text.includes(r)) hits.push(`${relative(SRC, f)}: rule ${r}`);
    }
    expect(hits).toEqual([]);
  });
});
