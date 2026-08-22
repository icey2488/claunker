/**
 * Thin CLI wrapper around the vendored sync-merge.js core, invoked by
 * spine/merge_bridge.py as a subprocess. NOT part of the vendored file — this is
 * new, spine-owned glue and may be edited freely.
 *
 * Protocol:
 *   stdin  : one JSON object { "a": <blob>, "b": <blob> }
 *   stdout : one JSON object { "merged": <blob>, "conflicts": [ {collection, id, of, source_version}, ... ] }
 *            on success (exit 0), or { "error": <message> } on failure (exit 1).
 *
 * The conflict report is derived from the merged blob's own `_conflict` markers
 * (stamped by sync-merge.js's makeConflictCopy) — no separate merge logic here.
 */
import { mergeBlobs } from './sync-merge.js';

const UNION_COLLECTIONS = ['cards', 'projects', 'tasks', 'artifacts', 'escalations'];

function readStdin() {
  return new Promise((resolve, reject) => {
    const chunks = [];
    process.stdin.on('data', (c) => chunks.push(c));
    process.stdin.on('end', () => resolve(Buffer.concat(chunks).toString('utf8')));
    process.stdin.on('error', reject);
  });
}

function collectConflicts(merged) {
  const conflicts = [];
  for (const name of UNION_COLLECTIONS) {
    const items = merged[name];
    if (!Array.isArray(items)) continue;
    for (const item of items) {
      if (item && item._conflict) {
        conflicts.push({
          collection: name,
          id: item.id,
          of: item._conflict.of,
          source_version: item._conflict.source_version ?? null,
        });
      }
    }
  }
  return conflicts;
}

async function main() {
  let input;
  try {
    const raw = await readStdin();
    input = JSON.parse(raw);
  } catch (exc) {
    process.stdout.write(JSON.stringify({ error: `invalid stdin JSON: ${exc.message}` }));
    process.exit(1);
    return;
  }

  if (!input || typeof input !== 'object' || !('a' in input) || !('b' in input)) {
    process.stdout.write(JSON.stringify({ error: 'stdin must be {"a": <blob>, "b": <blob>}' }));
    process.exit(1);
    return;
  }

  try {
    const merged = mergeBlobs(input.a, input.b);
    const conflicts = collectConflicts(merged);
    process.stdout.write(JSON.stringify({ merged, conflicts }));
    process.exit(0);
  } catch (exc) {
    process.stdout.write(JSON.stringify({ error: `mergeBlobs failed: ${exc.message}` }));
    process.exit(1);
  }
}

main();
