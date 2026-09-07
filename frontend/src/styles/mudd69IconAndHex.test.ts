/**
 * MUDD-69 (M-27, M-28) regression guard.
 *
 * `designSystem.test.ts` already enforces icon-size and raw-hex vocabulary,
 * but two gaps let this exact drift recur:
 *
 * 1. UI-006 (icon scale) skips every `V2_SURFACES_ENABLED`-gated page
 *    (`EXCLUDED` in designSystem.test.ts) — and all five M-27 offenders
 *    (OverviewPage, CatalogPage, RoadmapPage, WelcomePage, EmojiPicker) are
 *    gated pages. That is exactly why the count went 41 → 45 unnoticed: the
 *    guard that would have caught it does not run there.
 * 2. UI-010 (raw hex) only looks inside Tailwind utility strings
 *    (`rounded-[#fff]`, `className="...#fff..."`). A hex literal passed as a
 *    plain object value — `color: '#2f8a5b'`, `fill: '#fff'` — is invisible
 *    to it by design (chart ramps/avatar palettes are legitimate DATA hex).
 *    The M-28 offenders were exactly that shape: recharts `color`/`fill`/
 *    `stopColor` props, not classNames.
 *
 * This file re-runs both checks against the specific files named in MUDD-69,
 * independent of the gated-page exclusion and independent of whether the hex
 * sits in a className or a style prop.
 */
import { describe, expect, it } from 'vitest';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';

const SRC = resolve(__dirname, '..');
const read = (rel: string) => readFileSync(resolve(SRC, rel), 'utf8');

const CONTROL_ICON_SIZES = new Set([12, 14, 16, 18, 20, 24]);

/** M-27 sites — flagged in MUDD-69 as off-scale, including two (OverviewPage,
 *  CatalogPage) gated behind V2_SURFACES_ENABLED and so invisible to UI-006. */
const ICON_SCALE_FILES = [
  'components/ui/EmojiPicker.tsx',
  'routes/platform/OverviewPage.tsx',
  'routes/platform/CatalogPage.tsx',
  'routes/marketing/RoadmapPage.tsx',
  'routes/client/WelcomePage.tsx',
];

describe('MUDD-69 M-27 — icon sizes stay on scale even in gated pages', () => {
  it('no size={N} below 28 falls outside the documented control steps', () => {
    const hits: string[] = [];
    for (const rel of ICON_SCALE_FILES) {
      read(rel).split('\n').forEach((line, i) => {
        for (const m of line.matchAll(/size=\{(\d+)\}/g)) {
          const n = Number(m[1]);
          if (n < 28 && !CONTROL_ICON_SIZES.has(n)) hits.push(`${rel}:${i + 1}  size={${n}}`);
        }
      });
    }
    expect(hits, `use 12/14/16/18/20/24, or >=28 for an illustration:\n${hits.join('\n')}`).toEqual([]);
  });
});

describe('MUDD-69 M-28 — chart colours resolve through tokens, not literal hex', () => {
  it('ReportsPage aging-bucket slices use --c-* status tokens', () => {
    const src = read('routes/tenant/ReportsPage.tsx');
    const hits = src.split('\n')
      .map((line, i) => [line, i + 1] as const)
      .filter(([line]) => /\bcolor:\s*['"]#[0-9a-fA-F]{3,8}['"]/.test(line))
      .map(([line, n]) => `ReportsPage.tsx:${n}  ${line.trim()}`);
    expect(hits, `use var(--c-success/--c-warn/--c-accent/--c-danger):\n${hits.join('\n')}`).toEqual([]);
  });

  it('Spark.chart tooltip/dot fills use --c-surface, not a raw white hex', () => {
    const src = read('components/ui/Spark.chart.tsx');
    const hits = src.split('\n')
      .map((line, i) => [line, i + 1] as const)
      .filter(([line]) => /#[0-9a-fA-F]{3,8}/.test(line))
      .map(([line, n]) => `Spark.chart.tsx:${n}  ${line.trim()}`);
    expect(hits, `use var(--c-surface):\n${hits.join('\n')}`).toEqual([]);
  });
});
