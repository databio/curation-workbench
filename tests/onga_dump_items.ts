// Prints ONGA's own review items as JSON: `itemsFor(track, type)` from the ONGA
// site's src/lib/review-types.ts for every track and type. Read-only; run by
// tests/test_onga_review_items.py as
//   npx --yes tsx <this file> <ONGA root>      (cwd: <ONGA root>/site)
import { pathToFileURL } from 'node:url';
import { join } from 'node:path';

(async () => {
  const root = process.argv[2];
  const mod = await import(pathToFileURL(join(root, 'site', 'src', 'lib', 'review-types.ts')).href);
  const out: Record<string, unknown[]> = {};
  for (const track of mod.TRACKS) {
    for (const type of track.types) out[`${track.id}/${type.id}`] = mod.itemsFor(track.id, type.id);
  }
  process.stdout.write(JSON.stringify(out));
})();
