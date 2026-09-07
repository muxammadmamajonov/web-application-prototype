// @vitest-environment jsdom
/**
 * MUDD-147 — the category/brand/model delete buttons in the platform catalog
 * manager (`CategoriesTab`, `BrandsTab`, `ModelsTab`, all internal to this
 * file) fed `Button`'s `loading={del.isPending}` correctly, but each one
 * ALSO rendered a manual `{del.isPending ? <Loader2/> : <Trash2/>}` in its
 * children — a real delete-in-flight showed two spinners (`Button`'s own
 * plus the manual one) instead of one.
 *
 * The full render harness for this page (SettingsShell tab rail + per-tab
 * detail drawer + catalog/onboarding query mocks) is disproportionate to a
 * purely cosmetic duplicate-icon defect with no accessibility or business-
 * logic impact — `Button`'s own `disabled`/`aria-busy` were already correct
 * at all three call sites; only the extra child spinner was wrong. This
 * pins the fix at its source instead, the same way `installmentRowActions
 * .test.tsx` pins the removed duplicate-action menu: by asserting the exact
 * duplicate-spinner shape does not reappear next to any of the three
 * `loading={del.isPending}` danger-zone buttons.
 */
import { describe, expect, it } from 'vitest';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';

const src = readFileSync(resolve(__dirname, 'CatalogPage.tsx'), 'utf8');

describe('catalog delete buttons render exactly one spinner each', () => {
  it('has three `loading={del.isPending}` danger-zone delete buttons (categories/brands/models)', () => {
    expect(src.match(/loading=\{del\.isPending\}/g) ?? []).toHaveLength(3);
  });

  it('none of them also renders a manual isPending-conditioned <Loader2/> in its children', () => {
    // The regression shape: `loading={del.isPending}` immediately followed,
    // within a few lines, by `del.isPending ? <Loader2 ... /> : <Trash2 ... />`
    // — a second spinner stacked on top of Button's own.
    const dup = /loading=\{del\.isPending\}[\s\S]{0,250}del\.isPending\s*\?\s*<Loader2/;
    expect(dup.test(src)).toBe(false);
  });

  it('each danger-zone button renders Trash2 unconditionally as its only icon', () => {
    const blocks = src.split('loading={del.isPending}').slice(1);
    expect(blocks).toHaveLength(3);
    for (const block of blocks) {
      const nextLines = block.slice(0, 250);
      expect(nextLines).toMatch(/<Trash2 size=\{14\} \/>/);
      expect(nextLines).not.toMatch(/Loader2/);
    }
  });
});
