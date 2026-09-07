/**
 * T3 — no raw cache-key literal survives in production source.
 *
 * The factory only helps while it is the ONLY way a key gets built. One
 * hand-written array at a call site is enough to drift from the entry it was
 * meant to mirror, and nothing else in the suite would notice: the parity table
 * only reads the factory, and the intersection test only reads the registries.
 *
 * Two spellings are banned — the object form (`queryKey: [...]`) and the
 * positional form (`setQueryData([...], v)`), because the second is invisible
 * to a search for the first. Test files are exempt: this file and the top-up
 * guard both have to contain the strings they hunt for.
 */
import { describe, expect, it } from 'vitest';
import { readdirSync, readFileSync } from 'node:fs';
import { resolve, relative } from 'node:path';

const SRC = resolve(__dirname, '..');

/** The one file where key literals are the point. */
const EXEMPT = 'lib/queryKeys.ts';

const OBJECT_FORM = /queryKey:\s*\[/;
const POSITIONAL_FORM =
  /\.(setQueryData|getQueryData|invalidateQueries|refetchQueries|removeQueries|resetQueries|cancelQueries|prefetchQuery|ensureQueryData|fetchQuery)\s*\(\s*\[/;

function productionFiles(): Array<{ rel: string; text: string }> {
  const out: Array<{ rel: string; text: string }> = [];
  const walk = (dir: string) => {
    for (const entry of readdirSync(dir, { withFileTypes: true })) {
      const abs = resolve(dir, entry.name);
      if (entry.isDirectory()) {
        if (entry.name === 'node_modules') continue;
        walk(abs);
        continue;
      }
      if (!/\.(ts|tsx)$/.test(entry.name)) continue;
      if (entry.name.includes('.test.')) continue;
      const rel = relative(SRC, abs);
      if (rel === EXEMPT) continue;
      out.push({ rel, text: readFileSync(abs, 'utf8') });
    }
  };
  walk(SRC);
  return out;
}

/** Offenders as `path:line`, so a failure names the site instead of a count. */
function offenders(pattern: RegExp): string[] {
  return productionFiles().flatMap(({ rel, text }) =>
    text
      .split('\n')
      .map((line, i) => (pattern.test(line) ? `${rel}:${i + 1}` : null))
      .filter((hit): hit is string => hit !== null),
  );
}

describe('T3 — every production key comes from the factory', () => {
  it('the sweep actually reaches the code', () => {
    // A walk that silently matched nothing would pass forever.
    const files = productionFiles();
    expect(files.length).toBeGreaterThan(100);
    expect(files.some(({ rel }) => rel === 'lib/mutations/tenant.ts')).toBe(true);
    expect(files.every(({ rel }) => rel !== EXEMPT)).toBe(true);
  });

  it('no production source builds a key with an array literal', () => {
    expect(offenders(OBJECT_FORM)).toEqual([]);
  });

  it('no production source passes an array literal positionally to a cache call', () => {
    expect(offenders(POSITIONAL_FORM)).toEqual([]);
  });
});

describe('T3 — the detectors bite', () => {
  it('the object form matches a synthetic literal', () => {
    expect(OBJECT_FORM.test("useQuery({ queryKey: ['tenant', 'topups'] })")).toBe(true);
    expect(OBJECT_FORM.test('useQuery({ queryKey:  [x] })')).toBe(true);
    expect(OBJECT_FORM.test('useQuery({ queryKey: queryKeys.tenant.topups() })')).toBe(false);
  });

  it('the positional form matches every banned cache call', () => {
    for (const op of [
      'setQueryData', 'getQueryData', 'invalidateQueries', 'refetchQueries',
      'removeQueries', 'resetQueries', 'cancelQueries', 'prefetchQuery',
      'ensureQueryData', 'fetchQuery',
    ]) {
      expect(POSITIONAL_FORM.test(`qc.${op}(['me'], data)`)).toBe(true);
    }
    expect(POSITIONAL_FORM.test('qc.setQueryData(queryKeys.me.root(), data)')).toBe(false);
  });
});
