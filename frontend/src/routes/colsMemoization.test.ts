/**
 * H17 — 19 list pages built their `DataTable` `cols` array as a plain array
 * literal, so every render (a debounced keystroke, an unrelated filter
 * toggle) allocated a fresh array and defeated `DataTable`'s own
 * `React.memo`. This scans `src/routes/**` for every `const cols:`
 * declaration and asserts it is wrapped in `useMemo`, so a page added later
 * without the wrap fails here instead of shipping a silent perf regression.
 */
import { describe, expect, it } from 'vitest';
import { readdirSync, readFileSync, statSync } from 'node:fs';
import { resolve } from 'node:path';

const ROUTES_DIR = resolve(__dirname, '.');

function walk(dir: string, out: string[] = []): string[] {
  for (const name of readdirSync(dir)) {
    const p = resolve(dir, name);
    if (statSync(p).isDirectory()) { walk(p, out); continue; }
    if (/\.tsx?$/.test(name) && !name.includes('.test.')) out.push(p);
  }
  return out;
}

const COLS_DECL = /const cols:\s*Column</;

describe('every `const cols:` DataTable column array in src/routes is memoized (H17)', () => {
  const files = walk(ROUTES_DIR);

  it('scans a non-trivial number of route files', () => {
    expect(files.length).toBeGreaterThan(50);
  });

  it('finds the known 19 pages with a `cols` DataTable column array', () => {
    const withCols = files.filter((f) => COLS_DECL.test(readFileSync(f, 'utf8')));
    expect(withCols.length).toBe(19);
  });

  it('every one of them wraps `cols` in useMemo', () => {
    const offenders: string[] = [];
    for (const f of files) {
      const text = readFileSync(f, 'utf8');
      const line = text.split('\n').find((l) => COLS_DECL.test(l));
      if (!line) continue;
      if (!/useMemo\(/.test(line)) offenders.push(`${f.split('/src/').pop()}: ${line.trim()}`);
    }
    expect(offenders, `cols not wrapped in useMemo:\n${offenders.join('\n')}`).toEqual([]);
  });
});
