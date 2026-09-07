/**
 * Empty and loading states come from the shared primitives (§3, §7).
 *
 * `EmptyState` was imported by 25 files and hand-rolled in eight more, always
 * as the same centred `text-body text-muted` div. The existing guard —
 * `components/ui/dataTableEmpty.test.tsx` — is titled "there is only one
 * empty-state implementation" but asserts only that `DataTable` imports it, so
 * every route-level copy drifted underneath a test that appeared to cover them.
 *
 * The copies were not merely inconsistent. §7 requires "no data yet" and "no
 * result for this filter" to be visually distinct, and a bare centred sentence
 * cannot express the difference — nor carry the icon, the body line or the
 * recovery action that make an empty screen actionable. The loading variants
 * were worse: a centred word "Yuklanmoqda" where §3 mandates `SkeletonList`,
 * so the layout jumped when the rows arrived.
 *
 * This guard bans the shape itself, so a ninth copy fails rather than joining
 * them. As with the other sweeps here, the allowlist only shrinks.
 */
import { describe, expect, it } from 'vitest';
import { readdirSync, readFileSync } from 'node:fs';
import { resolve } from 'node:path';

const SRC = resolve(__dirname, '..');

/**
 * The hand-rolled shape: a centred, muted, body-sized block standing in for a
 * state primitive. Matched on the class trio rather than on the copy, because
 * the copy is translated and the classes are what actually get pasted around.
 */
const HAND_ROLLED = /text-center\s+text-body\s+text-muted/;

/**
 * Not an empty state — a NOTICE that sits above a populated list to say the
 * view is truncated ("showing 50 of 214"). It uses the same muted centred
 * treatment but means the opposite: there IS data, and there is more of it.
 * `EmptyState` would be actively wrong here.
 */
const NOT_AN_EMPTY_STATE = new Set([
  'routes/platform/TenantsPage.tsx',
]);

/** Still hand-rolling. Each should move to `EmptyState` / `SkeletonList`. */
const NOT_YET_MIGRATED = new Set<string>([]);

function sourceFiles(dir = SRC, prefix = ''): Array<{ rel: string; text: string }> {
  const out: Array<{ rel: string; text: string }> = [];
  for (const e of readdirSync(dir, { withFileTypes: true })) {
    const rel = prefix ? `${prefix}/${e.name}` : e.name;
    if (e.isDirectory()) { out.push(...sourceFiles(resolve(dir, e.name), rel)); continue; }
    if (!e.name.endsWith('.tsx') || e.name.includes('.test.')) continue;
    out.push({ rel, text: readFileSync(resolve(dir, e.name), 'utf8') });
  }
  return out;
}

const FILES = sourceFiles();
const handRolled = FILES.filter((f) => HAND_ROLLED.test(f.text)).map((f) => f.rel);

describe('the sweep reaches the source', () => {
  it('scans a meaningful tree', () => {
    expect(FILES.length).toBeGreaterThan(100);
    expect(FILES.some((f) => f.rel.startsWith('routes/'))).toBe(true);
  });
});

describe('no hand-rolled empty or loading states', () => {
  it('every centred-muted block is a tracked exception', () => {
    const fresh = handRolled
      .filter((r) => !NOT_AN_EMPTY_STATE.has(r))
      .filter((r) => !NOT_YET_MIGRATED.has(r));
    expect(fresh,
      `these hand-roll a state primitive:\n${fresh.join('\n')}\n` +
      'Use <EmptyState> for "nothing here" and <SkeletonList> for loading — a ' +
      'centred sentence cannot carry an icon, a recovery action, or the §7 ' +
      'distinction between "no data yet" and "no result".',
    ).toEqual([]);
  });

  it('the migration list does not outlive the files on it', () => {
    const stale = [...NOT_YET_MIGRATED].filter((r) => !handRolled.includes(r));
    expect(stale, `already migrated — remove from NOT_YET_MIGRATED:\n${stale.join('\n')}`)
      .toEqual([]);
  });

  it('the truncation notice is still there to be excepted', () => {
    // Guards the exception: if that notice is ever removed or reworded, this
    // fails and the allowlist entry gets deleted rather than lingering.
    for (const rel of NOT_AN_EMPTY_STATE) {
      expect(handRolled, `${rel} no longer matches — drop it from the allowlist`)
        .toContain(rel);
    }
  });
});

describe('the pages that were migrated stay migrated', () => {
  const file = (rel: string) => FILES.find((f) => f.rel === rel)?.text ?? '';

  it.each([
    'routes/tenant/OverviewPage.tsx',
    'routes/tenant/BillingPage.tsx',
    'routes/tenant/ReportsPage.tsx',
  ])('%s uses the shared primitives', (rel) => {
    expect(file(rel)).toContain('EmptyState');
  });

  it.each([
    'routes/tenant/OverviewPage.tsx',
    'routes/tenant/InstallmentsPage.tsx',
    'routes/tenant/ReportsPage.tsx',
  ])('%s renders a skeleton while loading, not a word', (rel) => {
    expect(file(rel)).toContain('SkeletonList');
  });
});

describe('the icon registry is respected', () => {
  it('SlidersHorizontal appears only in FilterDrawer', () => {
    // §4 reserves it for Filter, "only via FilterButton". It had drifted onto a
    // "custom plan terms" action in a tenant drawer, where the glyph promised a
    // filter and opened an editor.
    const users = FILES.filter((f) => /\bSlidersHorizontal\b/.test(f.text)).map((f) => f.rel);
    expect(users.sort()).toEqual(['components/ui/FilterDrawer.tsx']);
  });
});
