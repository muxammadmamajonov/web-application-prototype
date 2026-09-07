/**
 * M-23 (MUDD-68) — six catch blocks fell back to the raw English literal
 * `'failed'` when the backend response had no `detail`, bypassing
 * `apiErrorMessage()` (the localized helper every other surface funnels
 * through) and showing an untranslated word inside a uz/ru product.
 */
import { describe, expect, it } from 'vitest';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';

const SRC = resolve(__dirname, '..');
const read = (rel: string) => readFileSync(resolve(SRC, rel), 'utf8');

const TOUCHED = [
  'routes/tenant/_modals/ProductModal.tsx',
  'routes/tenant/_modals/ProductPickerModal.tsx',
  'routes/platform/CatalogPage.tsx',
  'routes/platform/_modals/CategorySchemaModal.tsx',
];

describe('no catch block falls back to the raw "failed" literal', () => {
  it.each(TOUCHED)('%s has no `?? \'failed\'` fallback', (rel) => {
    expect(read(rel)).not.toMatch(/\?\?\s*'failed'/);
  });

  it.each(TOUCHED)('%s routes its catch through apiErrorMessage', (rel) => {
    expect(read(rel)).toMatch(/apiErrorMessage\(/);
  });

  it('CatalogPage has all three former offenders fixed, not just one', () => {
    const matches = read('routes/platform/CatalogPage.tsx').match(/apiErrorMessage\(/g) ?? [];
    expect(matches.length).toBeGreaterThanOrEqual(3);
  });
});
