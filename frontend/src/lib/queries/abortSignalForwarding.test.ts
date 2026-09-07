/**
 * M-17 — TanStack's `AbortSignal` was never forwarded to axios.
 *
 * A previous pass reported this as documented ("Document best practice for
 * forwarding TanStack's signal to axios" — commit 66b173e) but not
 * implemented: 0 of 63 `queryFn`s in `lib/queries/*.ts` destructured
 * `{ signal }`, so navigating away from a screen never cancelled its
 * in-flight requests. This test counts every `queryFn:` in the read layer
 * and asserts each one both accepts and forwards `signal` — a source-scan
 * regression guard rather than an assertion in prose, so a future queryFn
 * added without it fails CI instead of shipping silently.
 *
 * The count below has moved once since: MUDD-84 added `useTenantPaymentsPage`
 * (64th site), also signal-forwarding.
 */
import { describe, expect, it } from 'vitest';
import { readdirSync, readFileSync } from 'node:fs';
import { resolve } from 'node:path';

const QUERIES_DIR = resolve(__dirname, '.');

function queryFiles(): Array<{ file: string; text: string }> {
  return readdirSync(QUERIES_DIR)
    .filter((f) => f.endsWith('.ts') && !f.includes('.test.'))
    .map((f) => ({ file: f, text: readFileSync(resolve(QUERIES_DIR, f), 'utf8') }));
}

/** One `queryFn:` occurrence's full line, for a legible failure message. */
function queryFnLines(text: string): string[] {
  return text.split('\n').filter((l) => /queryFn:/.test(l));
}

describe('every queryFn in lib/queries forwards AbortSignal to axios (M-17)', () => {
  const files = queryFiles();

  it('scans a non-trivial number of files', () => {
    // A scan that silently matched nothing would "pass" forever.
    expect(files.length).toBeGreaterThanOrEqual(7);
  });

  it('finds the known 64 queryFn call sites — this count is the audit\'s own evidence bar', () => {
    const total = files.reduce((n, { text }) => n + queryFnLines(text).length, 0);
    expect(total).toBe(64);
  });

  it.each(queryFiles().filter((f) => queryFnLines(f.text).length > 0).map((f) => f.file))(
    'every queryFn in %s destructures { signal }',
    (file) => {
      const text = readFileSync(resolve(QUERIES_DIR, file), 'utf8');
      const lines = queryFnLines(text);
      const missing = lines.filter((l) => !/signal/.test(l));
      expect(missing, `queryFn missing signal in ${file}:\n${missing.join('\n')}`).toEqual([]);
    },
  );
});
