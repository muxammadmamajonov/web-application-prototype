/**
 * The import size cap must be one number, not three (#13).
 *
 * Before this pass there were three and they disagreed: the page TOLD the user
 * "max 5MB", the client enforced nothing at all, and the server accepted 8 MB.
 * So a 6 MB workbook was read, base64'd and uploaded over mobile data, then
 * judged by a rule the UI had already contradicted — and a 400 MB one got the
 * same treatment before anything objected.
 *
 * Same cross-language pin as `deepLinks.contract.test.ts` and
 * `leadContact.test.ts`: two sides in two languages with no shared artifact is
 * the shape that produced the bug, so the test reads the backend constant.
 */
import { describe, expect, it } from 'vitest';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { IMPORT_FILE } from './fileValidation';

const LIMITS_PY = resolve(__dirname, '../../../backend/app/core/limits.py');

/** `MAX_IMPORT_BYTES = 5 * 1024 * 1024` → bytes. */
function backendImportCap(): number {
  const src = readFileSync(LIMITS_PY, 'utf8');
  const m = src.match(/^MAX_IMPORT_BYTES\s*=\s*(\d+)\s*\*\s*(\d+)\s*\*\s*(\d+)/m);
  expect(m, 'MAX_IMPORT_BYTES not found — did the constant move or change shape?')
    .toBeTruthy();
  return Number(m![1]) * Number(m![2]) * Number(m![3]);
}

describe('the client and the server cap imports at the same size', () => {
  it('agrees byte for byte', () => {
    expect(IMPORT_FILE.maxBytes).toBe(backendImportCap());
  });

  it('is the 5 MB the UI promises', () => {
    expect(IMPORT_FILE.maxBytes).toBe(5 * 1024 * 1024);
    expect(IMPORT_FILE.maxLabel).toBe('5 MB');
  });

  it('states the cap in the label the error message uses', () => {
    // The user is told a number; it has to be the number actually enforced.
    const mb = IMPORT_FILE.maxBytes / (1024 * 1024);
    expect(IMPORT_FILE.maxLabel).toContain(String(mb));
  });
});
