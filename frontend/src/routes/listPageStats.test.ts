/**
 * List pages lead with the list, not with a summary (#6).
 *
 * Every list page opened with a `KpiCard` grid and, on phones, a `StatStrip`.
 * They are a preamble the reader scrolls past to reach the thing they came for,
 * and on a 360px screen they cost most of the first paint before a single row
 * appears. The figures are not lost — they live on the Overview dashboard, which
 * is the page whose job they are.
 *
 * DASHBOARDS ARE EXEMPT, deliberately. `platform/OverviewPage` and
 * `tenant/OverviewPage` are made of these cards; stripping them would empty the
 * page and contradict #15, which defers Reports precisely BECAUSE "the Dashboard
 * already provides the required initial overview". Removing both would leave the
 * product with no summary anywhere.
 *
 * As with `createActionPattern.test.ts`, the allowlist only shrinks: it stops new
 * stat cards appearing on list pages today, and tracks what is left to migrate.
 */
import { describe, expect, it } from 'vitest';
import { readdirSync, readFileSync } from 'node:fs';
import { resolve } from 'node:path';

const ROUTES = resolve(__dirname);
const STAT_COMPONENT = /<(KpiCard|StatStrip)\b/;

/** Pages whose entire purpose is the summary. Not a backlog — a decision. */
const DASHBOARDS = new Set([
  'platform/OverviewPage.tsx',
  'tenant/OverviewPage.tsx',
  // Deferred by #15 and no longer routed, so its cards are unreachable. Left
  // untouched so the section returns intact when REPORTS_ENABLED flips.
  'tenant/ReportsPage.tsx',
  // The staff persona has NO Overview route (see router.tsx: staff gets Today,
  // Customers, Sales, Payments and nothing else), so Today IS their dashboard —
  // the exemption's premise, "the figures live on Overview", has nowhere to point.
  // Its three KpiCards are also the desktop half of a pair: the same three
  // figures render on phones as the dark hero + StatTiles. Strip the cards and
  // desktop staff lose a summary mobile still shows.
  'staff/TodayPage.tsx',
]);

/**
 * List pages still opening with a summary. Empty: the sweep is complete, so the
 * first assertion below now rejects a summary on ANY list page outright. Do not
 * add to it — a new entry would re-open the hole this set exists to close.
 */
const NOT_YET_MIGRATED = new Set<string>([]);

function routeFiles(dir = ROUTES, prefix = ''): Array<{ rel: string; text: string }> {
  const out: Array<{ rel: string; text: string }> = [];
  for (const e of readdirSync(dir, { withFileTypes: true })) {
    const rel = prefix ? `${prefix}/${e.name}` : e.name;
    if (e.isDirectory()) { out.push(...routeFiles(resolve(dir, e.name), rel)); continue; }
    if (!e.name.endsWith('.tsx') || e.name.includes('.test.')) continue;
    out.push({ rel, text: readFileSync(resolve(dir, e.name), 'utf8') });
  }
  return out;
}

const FILES = routeFiles();
const withStats = FILES.filter((f) => STAT_COMPONENT.test(f.text)).map((f) => f.rel);
const listPagesWithStats = withStats.filter((r) => !DASHBOARDS.has(r));

describe('the sweep reaches the routes', () => {
  it('scans the whole tree', () => {
    expect(FILES.length).toBeGreaterThan(50);
    expect(FILES.some((f) => f.rel.startsWith('platform/'))).toBe(true);
  });
});

describe('no new summary cards on list pages', () => {
  it('every list page carrying stats is a known, tracked one', () => {
    const fresh = listPagesWithStats.filter((r) => !NOT_YET_MIGRATED.has(r));
    expect(fresh,
      `these list pages open with a summary instead of their content:\n${fresh.join('\n')}\n` +
      'Remove the KpiCard grid and StatStrip; the figures belong on Overview.',
    ).toEqual([]);
  });

  it('the migration list does not outlive the pages on it', () => {
    const stale = [...NOT_YET_MIGRATED].filter((r) => !listPagesWithStats.includes(r));
    expect(stale, `already migrated — remove from NOT_YET_MIGRATED:\n${stale.join('\n')}`)
      .toEqual([]);
  });
});

describe('the dashboards keep theirs', () => {
  it.each(['platform/OverviewPage.tsx', 'tenant/OverviewPage.tsx', 'staff/TodayPage.tsx'])(
    '%s still renders a summary', (rel) => {
      // Guards against a later over-eager sweep: #15 defers Reports on the
      // premise that Overview carries these numbers, so emptying Overview would
      // leave the product with no summary at all.
      const f = FILES.find((x) => x.rel === rel);
      expect(f, `${rel} not found`).toBeTruthy();
      expect(STAT_COMPONENT.test(f!.text), `${rel} lost its summary`).toBe(true);
    },
  );

  it('staff have no Overview page to send Today\'s figures to', () => {
    // The premise of Today's exemption, asserted rather than assumed: if a staff
    // Overview is ever added, this fails and the exemption gets re-decided
    // instead of quietly outliving its reason.
    expect(FILES.filter((f) => f.rel.startsWith('staff/')).map((f) => f.rel).sort())
      .toEqual([
        'staff/CustomersPage.tsx',
        'staff/PaymentsPage.tsx',
        'staff/SalesPage.tsx',
        'staff/TodayPage.tsx',
      ]);
  });
});

describe('the reference page is migrated', () => {
  const tenants = FILES.find((f) => f.rel === 'platform/TenantsPage.tsx');

  it('exists', () => expect(tenants).toBeTruthy());

  it('renders neither a KpiCard nor a StatStrip', () => {
    expect(STAT_COMPONENT.test(tenants!.text)).toBe(false);
  });

  it('keeps the counts the status tabs are built from', () => {
    // Removing the cards must not remove the data behind the filters.
    expect(tenants!.text).toContain('const counts');
  });
});
