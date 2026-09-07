/**
 * One Create action across the product, and it is the floating `+` (#7).
 *
 * THIS RULE IS A REVERSAL, and it is worth being honest about that. The
 * previous standard — which this file used to enforce — was a compact labelled
 * pill in the page header: `PrimaryAction` on desktop, `MobileHeader`'s
 * `onAdd`/`addLabel` on phones. That standard replaced hand-rolled full-width
 * buttons and was fully adopted. It is not being abandoned because it failed a
 * test; the user asked for the floating `+` instead, so the pill is now the
 * thing being guarded against rather than the thing being guarded for.
 *
 * What the floating button buys is placement: `FloatingCreateAction` is
 * `fixed`, so the create action is in the identical spot on every page at every
 * breakpoint — it never migrates between a desktop header and a mobile row, and
 * it never scrolls away on a long list. What it costs is the visible label,
 * which is why the component makes `label` required and spends it on
 * `aria-label` + `title`.
 *
 * This file does three jobs:
 *   1. stop the OLD header pill (and hand-rolled create buttons) reappearing;
 *   2. pin every page that owns a create action to `FloatingCreateAction` and
 *      to the exact translated label key it uses;
 *   3. keep two floating buttons from ever landing on top of each other.
 *
 * `NOT_YET_MIGRATED` keeps the property the previous version had: it only ever
 * shrinks. A file listed there that no longer violates is *also* a failure, so
 * the list has to describe reality instead of accumulating.
 */
import { describe, expect, it } from 'vitest';
import { readdirSync, readFileSync } from 'node:fs';
import { resolve } from 'node:path';

const ROUTES = resolve(__dirname);

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

/**
 * Modals and drawers are forms, not pages. "Add another line item", "create the
 * customer you just typed" are actions *within* a form and belong beside the
 * field they extend — a floating page-level `+` would be wrong there, and so
 * would the ban on a small primary add button. Only pages are swept.
 */
const PAGES = FILES.filter((f) => !f.rel.includes('_modals/'));

/** Every way the OLD, now-forbidden create control could come back. */
const FORBIDDEN: Array<{ what: string; re: RegExp }> = [
  {
    what: 'the header pill `PrimaryAction`',
    re: /<PrimaryAction\b/,
  },
  {
    // `MobileHeader` no longer accepts these, so typecheck catches it too — but
    // naming the rule here is what makes the failure explain itself.
    what: "`MobileHeader`'s `onAdd` / `addLabel` create pill",
    re: /\b(?:onAdd|addLabel)=/,
  },
  {
    // Deliberately narrow: `size="lg" block` on a *detail* page ("Pay",
    // "Payoff") is a legitimate primary CTA and must not be swept up here.
    what: 'a full-width create button',
    re: /<Button[^>]*variant="primary"[^>]*\bblock\b[^>]*onClick=\{[^}]*\b(?:setOpen|setCreateOpen|setComposeOpen|setLinkOpen|setSaleOpen|openCreate|startInvite|onAdd)\b/,
  },
  {
    what: 'a hand-rolled `＋ Create` primary button',
    re: /<Button\b[\s\S]{0,300}?variant="primary"[\s\S]{0,300}?<Plus\b/,
  },
];

const violations = PAGES.flatMap((f) =>
  FORBIDDEN.filter((p) => p.re.test(f.text)).map((p) => `${f.rel} — ${p.what}`),
);

/**
 * EMPTY, and it must stay that way — the migration is complete.
 *
 * Anything appearing here is a regression, not a backlog item:
 * `FloatingCreateAction` already does this job, so a header pill or a
 * hand-rolled create button means someone rebuilt a control that exists.
 */
const NOT_YET_MIGRATED = new Set<string>([]);

/**
 * Every page that owns a create action, and the translated label key it must
 * pass. The key matters as much as the component: `FloatingCreateAction`
 * renders a bare glyph, so `label` is the ONLY thing a screen-reader user gets.
 * A bare "+" or an untranslated string would leave the control unnamed.
 */
const CREATE_PAGES: Record<string, string> = {
  'tenant/CustomersPage.tsx':      'customers.create',
  'tenant/OverviewPage.tsx':       'common.newInstallment',
  'tenant/InstallmentsPage.tsx':   'common.newInstallment',
  'tenant/ProductsPage.tsx':       'products.create',
  'tenant/StaffPage.tsx':          'tenantStaff.inviteLink',
  'tenant/BroadcastsPage.tsx':     'broadcasts.compose',
  'platform/TenantsPage.tsx':      'platformTenants.newTenant',
  'platform/AdsPage.tsx':          'platformAds.newCampaign',
  'platform/PromosPage.tsx':       'platformPromos.newPromo',
  'platform/AdminsPage.tsx':       'platformAdmins.inviteLink',
  'platform/BroadcastsPage.tsx':   'platformBroadcasts.composeTitle',
  'platform/PlansPage.tsx':        'platformPlans.add',
  'platform/CatalogPage.tsx':      'platformCatalog.newCategory',
  'staff/SalesPage.tsx':           'common.newSale',
  'staff/PaymentsPage.tsx':        'staffPayments.record',
  'staff/TodayPage.tsx':           'common.newSale',
};

describe('the sweep reaches the routes', () => {
  it('scans the whole route tree', () => {
    // A sweep that silently matched nothing would pass forever.
    expect(FILES.length).toBeGreaterThan(50);
    expect(FILES.some((f) => f.rel.startsWith('tenant/'))).toBe(true);
    expect(FILES.some((f) => f.rel.startsWith('platform/'))).toBe(true);
    // And the page/modal split must not silently swallow everything.
    expect(PAGES.length).toBeGreaterThan(50);
  });
});

describe('the header create pill does not come back', () => {
  it('every violation is a known, tracked one', () => {
    const fresh = violations.filter((v) => !NOT_YET_MIGRATED.has(v));
    expect(fresh,
      `these rebuild a create control that already exists:\n${fresh.join('\n')}\n` +
      'Render `<FloatingCreateAction onClick={…} label={t("…")} />` once per page instead.',
    ).toEqual([]);
  });

  it('the migration list does not outlive the pages on it', () => {
    const stale = [...NOT_YET_MIGRATED].filter((v) => !violations.includes(v));
    expect(stale,
      `already migrated — remove from NOT_YET_MIGRATED:\n${stale.join('\n')}`,
    ).toEqual([]);
  });
});

describe('every create action is the floating `+`', () => {
  it.each(Object.entries(CREATE_PAGES))('%s renders FloatingCreateAction', (rel, labelKey) => {
    const file = PAGES.find((f) => f.rel === rel);
    expect(file, `${rel} is listed in CREATE_PAGES but no longer exists`).toBeTruthy();
    expect(file!.text).toContain('<FloatingCreateAction');
    expect(file!.text,
      `${rel} must label its create action with t('${labelKey}') — the glyph alone names nothing.`,
    ).toContain(labelKey);
  });

  it('never renders two floating buttons on one page', () => {
    // Two `fixed` buttons share the same corner and would sit on top of each
    // other. Pages with several create surfaces (Catalog's tabs) funnel through
    // a single call site whose wrapper guarantees one tab body at a time.
    const doubled = PAGES
      .map((f) => ({ rel: f.rel, n: f.text.split('<FloatingCreateAction').length - 1 }))
      .filter((f) => f.n > 1);
    expect(doubled.map((f) => `${f.rel} (${f.n})`),
      'two fixed buttons would overlap in the same corner',
    ).toEqual([]);
  });
});

describe('the reference page is migrated', () => {
  const customers = PAGES.find((f) => f.rel === 'tenant/CustomersPage.tsx');

  it('exists', () => expect(customers).toBeTruthy());

  it('keeps the create action out of both headers', () => {
    // `PageHeader` and `MobileHeader` carry the title only — no `actions` pill,
    // no `onAdd`. The one create control is the floating button.
    expect(customers!.text).toMatch(/<PageHeader title=\{t\('nav\.customers'\)\} \/>/);
    expect(customers!.text).toMatch(/<MobileHeader title=\{t\('nav\.customers'\)\} \/>/);
    expect(customers!.text).toContain("<FloatingCreateAction onClick={openCreate} label={t('customers.create')} />");
  });

  it('does not repeat the action inside its empty state', () => {
    // The floating button is on screen behind the empty card, so an in-card
    // CTA would be the same action twice.
    expect(customers!.text).not.toMatch(/EmptyState[\s\S]{0,400}?tone="accent"[\s\S]{0,400}?onCreate/);
  });
});
