/**
 * T2 — do the keys mutations invalidate and the keys hooks subscribe to actually meet?
 *
 * Both registries are written in terms of factory entries, never literals, so a
 * renamed segment moves both sides at once and this test stays silent about it.
 * That is deliberate: identity is pinned by the parity table, not here. What
 * this test catches is a key that no longer *reaches* anything — an invalidation
 * firing into empty space, or a list that nothing refreshes.
 *
 * Prefix matching is modelled with `slice` + `hashKey` rather than TanStack's
 * own matcher, because `partialMatchKey` is not public API. For array-prefix
 * comparison the two agree exactly.
 */
import { describe, expect, it } from 'vitest';
import { hashKey } from '@tanstack/react-query';
import { queryKeys as k } from './queryKeys';

/** Representative arguments. The same values are used on both sides so a prefix
 *  comparison is meaningful. */
const ID = 'id-1';
const PARAMS = { q: 'ali' };

type Entry = { key: readonly unknown[]; from: string };
const e = (key: readonly unknown[], from: string): Entry => ({ key, from });

// ---------------------------------------------------------------------------
// Every key a hook subscribes to — 62.
// ---------------------------------------------------------------------------
const Q = 'lib/queries';
const SUBSCRIPTIONS: Entry[] = [
  // lib/queries/tenant.ts — 30
  e(k.tenant.overview(), `${Q}/tenant.ts`),
  e(k.tenant.customers(PARAMS), `${Q}/tenant.ts`),
  e(k.tenant.guarantors(PARAMS), `${Q}/tenant.ts`),
  e(k.tenant.riskScore(ID), `${Q}/tenant.ts`),
  e(k.tenant.products(PARAMS), `${Q}/tenant.ts`),
  e(k.tenant.installment(ID), `${Q}/tenant.ts`),
  e(k.tenant.installments(null), `${Q}/tenant.ts`),
  e(k.tenant.worklist(20), `${Q}/tenant.ts`),
  e(k.tenant.worklistPaged(20, ''), `${Q}/tenant.ts`),
  e(k.tenant.paymentsPendingCount(), `${Q}/tenant.ts`),
  e(k.tenant.payments(), `${Q}/tenant.ts`),
  e(k.tenant.broadcasts(), `${Q}/tenant.ts`),
  e(k.tenant.invitations(undefined), `${Q}/tenant.ts`),
  e(k.tenant.settings(), `${Q}/tenant.ts`),
  e(k.tenant.smsUsage(), `${Q}/tenant.ts`),
  e(k.tenant.smsHistory(50), `${Q}/tenant.ts`),
  e(k.tenant.customerChannels(ID), `${Q}/tenant.ts`),
  e(k.tenant.staff(), `${Q}/tenant.ts`),
  e(k.tenant.reportsAging(), `${Q}/tenant.ts`),
  e(k.tenant.reportsCashflow({}), `${Q}/tenant.ts`),
  e(k.tenant.reportsStaff(), `${Q}/tenant.ts`),
  e(k.tenant.reportsProducts(), `${Q}/tenant.ts`),
  e(k.tenant.reportsLtv(20), `${Q}/tenant.ts`),
  e(k.tenant.reportsMargin(), `${Q}/tenant.ts`),
  e(k.tenant.billing(), `${Q}/tenant.ts`),
  e(k.tenant.invoices(), `${Q}/tenant.ts`),
  e(k.tenant.topupInfo(), `${Q}/tenant.ts`),
  e(k.tenant.topups(), `${Q}/tenant.ts`),
  e(k.tenant.productMovements(ID), `${Q}/tenant.ts`),
  e(k.tenant.lowStock(), `${Q}/tenant.ts`),
  // lib/queries/platform.ts — 17
  e(k.platform.referralLink(), `${Q}/platform.ts`),
  e(k.platform.overview(), `${Q}/platform.ts`),
  e(k.platform.payments('all'), `${Q}/platform.ts`),
  e(k.platform.tenantDetail(ID), `${Q}/platform.ts`),
  e(k.platform.tenants(undefined), `${Q}/platform.ts`),
  e(k.platform.plans(), `${Q}/platform.ts`),
  e(k.platform.riskStats(), `${Q}/platform.ts`),
  e(k.platform.admins(), `${Q}/platform.ts`),
  e(k.platform.audit({}), `${Q}/platform.ts`),
  e(k.platform.promos(), `${Q}/platform.ts`),
  e(k.platform.ads(), `${Q}/platform.ts`),
  e(k.platform.referrals(), `${Q}/platform.ts`),
  e(k.platform.topups('pending'), `${Q}/platform.ts`),
  e(k.platform.settings(), `${Q}/platform.ts`),
  e(k.platform.broadcasts(), `${Q}/platform.ts`),
  e(k.platform.segments(), `${Q}/platform.ts`),
  e(k.platform.users({ segment: 's', q: '', limit: 20, offset: 0 }), `${Q}/platform.ts`),
  // lib/queries/catalog.ts — 6
  e(k.catalog.categories('all'), `${Q}/catalog.ts`),
  e(k.catalog.brands(ID), `${Q}/catalog.ts`),
  e(k.catalog.models(ID, 'all'), `${Q}/catalog.ts`),
  e(k.catalog.modelsFlat({}), `${Q}/catalog.ts`),
  e(k.catalog.colors(), `${Q}/catalog.ts`),
  e(k.catalog.allBrands(), `${Q}/catalog.ts`),
  // lib/queries/client.ts — 4
  e(k.me.notifications(), `${Q}/client.ts`),
  e(k.me.summary(), `${Q}/client.ts`),
  e(k.me.installments('open'), `${Q}/client.ts`),
  e(k.me.installment(ID), `${Q}/client.ts`),
  // lib/queries/public.ts — 2
  e(k.onboarding.options(), `${Q}/public.ts`),
  e(k.public.plans(), `${Q}/public.ts`),
  // lib/queries/staff.ts — 1 (a tenant key, emitted from the staff module)
  e(k.tenant.staffToday(), `${Q}/staff.ts`),
  // hooks/useMe.ts — 1
  e(k.me.root(), 'hooks/useMe.ts'),
  // routes/tenant/SettingsPage.tsx — 1 (the only holidays key)
  e(k.holidays.year(2026), 'routes/tenant/SettingsPage.tsx'),
];

// ---------------------------------------------------------------------------
// Every structurally distinct key a mutation invalidates — 36 shapes.
// ---------------------------------------------------------------------------
const M = 'lib/mutations';
const INVALIDATIONS: Entry[] = [
  e(k.catalog.all(), `${M}/catalog.ts`),
  e(k.catalog.modelsAll(), `${M}/catalog.ts`),
  e(k.tenant.customersAll(), `${M}/tenant.ts`),
  e(k.tenant.guarantorsAll(), `${M}/tenant.ts`),
  e(k.tenant.productsAll(), `${M}/tenant.ts`),
  e(k.tenant.installmentsAll(), `${M}/tenant.ts`),
  e(k.tenant.installment(ID), `${M}/tenant.ts`),
  // The collections queue and the shift tiles are derived from what the payment
  // and lifecycle writes change, and are mounted on the same screens. The
  // worklist prefix is deliberately broad: the paged key carries
  // (pageSize, search), so naming one pair would leave the others stale.
  e(k.tenant.worklistAll(), `${M}/tenant.ts`),
  e(k.tenant.staffToday(), `${M}/tenant.ts`),
  e(k.tenant.payments(), `${M}/tenant.ts`),
  e(k.tenant.overview(), `${M}/tenant.ts`),
  e(k.tenant.broadcasts(), `${M}/tenant.ts`),
  e(k.tenant.settings(), `${M}/tenant.ts`),
  e(k.tenant.topupInfo(), `${M}/tenant.ts`),
  e(k.tenant.riskScore(ID), `${M}/tenant.ts`),
  e(k.tenant.staff(), `${M}/tenant.ts`),
  e(k.tenant.invitationsAll(), `${M}/tenant.ts`),
  e(k.me.notifications(), `${M}/tenant.ts`),
  e(k.me.root(), 'hooks/useMe.ts'),
  e(k.platform.tenantsAll(), `${M}/platform.ts`),
  e(k.platform.tenantDetail(ID), `${M}/platform.ts`),
  e(k.platform.tenantDetailAll(), `${M}/platform.ts`),
  e(k.platform.plans(), `${M}/platform.ts`),
  e(k.platform.promos(), `${M}/platform.ts`),
  e(k.platform.ads(), `${M}/platform.ts`),
  e(k.platform.admins(), `${M}/platform.ts`),
  e(k.platform.referrals(), `${M}/platform.ts`),
  e(k.platform.topupsAll(), `${M}/platform.ts`),
  e(k.platform.settings(), `${M}/platform.ts`),
  e(k.platform.broadcasts(), `${M}/platform.ts`),
  e(k.tenant.billing(), 'routes/tenant/BillingPage.tsx'),
  e(k.tenant.productMovements(ID), 'routes/tenant/_modals/StockModal.tsx'),
  e(k.tenant.lowStock(), 'routes/tenant/_modals/StockModal.tsx'),
  e(k.tenant.topups(), 'routes/tenant/_modals/TopUpModal.tsx'),
  e(k.tenant.customerChannels(ID), 'routes/tenant/_modals/CustomerModal.tsx'),
  // A computed segment over a closed union; both branches are live list prefixes.
  e(k.tenant.importTarget('customers'), 'routes/tenant/ImportPage.tsx'),
  e(k.tenant.importTarget('products'), 'routes/tenant/ImportPage.tsx'),
];

// ---------------------------------------------------------------------------
// Subscriptions no invalidation reaches. Every row states why, so that adding a
// hook without an invalidation forces a decision rather than passing silently.
// ---------------------------------------------------------------------------
type Tag = 'KNOWN-DEFECT' | 'NO-WRITER' | 'NEVER-SUBSCRIBED' | 'CROSS-SCREEN';
const UNINVALIDATED: Array<{ key: readonly unknown[]; tag: Tag; why: string }> = [
  { key: k.platform.referralLink(), tag: 'NO-WRITER', why: "The admin's own share link is constant; creating a referral does not alter it." },
  { key: k.platform.riskStats(), tag: 'NO-WRITER', why: 'Derived tenant-side; no mutation in the app writes it.' },
  { key: k.platform.segments(), tag: 'NO-WRITER', why: 'No segment mutation exists.' },
  { key: k.platform.audit({}), tag: 'NO-WRITER', why: 'Its only writer and the endpoint behind it were both removed; no write exists.' },
  { key: k.onboarding.options(), tag: 'NO-WRITER', why: 'Reference data (business types, how-found). Settings writes target a disjoint surface.' },
  { key: k.holidays.year(2026), tag: 'NO-WRITER', why: 'Read-only list; no holiday write endpoint is called anywhere.' },
  { key: k.tenant.smsUsage(), tag: 'NEVER-SUBSCRIBED', why: 'Hook has no consumers; never mounted at runtime.' },
  { key: k.tenant.smsHistory(50), tag: 'NEVER-SUBSCRIBED', why: 'Hook has no consumers; never mounted at runtime.' },
  { key: k.tenant.reportsStaff(), tag: 'NEVER-SUBSCRIBED', why: 'Hard-disabled by the V2 surface flag; never fetches.' },
  { key: k.tenant.reportsProducts(), tag: 'NEVER-SUBSCRIBED', why: 'Hard-disabled by the V2 surface flag; never fetches.' },
  { key: k.tenant.reportsLtv(20), tag: 'NEVER-SUBSCRIBED', why: 'Hard-disabled by the V2 surface flag; never fetches.' },
  { key: k.tenant.reportsMargin(), tag: 'NEVER-SUBSCRIBED', why: 'Hard-disabled by the V2 surface flag; never fetches.' },
  { key: k.tenant.reportsAging(), tag: 'CROSS-SCREEN', why: 'Reports page mounts no mutation; aggregate staleness there is a policy choice.' },
  { key: k.tenant.reportsCashflow({}), tag: 'CROSS-SCREEN', why: 'Reports page mounts no mutation; aggregate staleness there is a policy choice.' },
  { key: k.tenant.invoices(), tag: 'CROSS-SCREEN', why: 'Plan change is server-side and the invoice may not exist yet when the response lands.' },
  { key: k.platform.overview(), tag: 'CROSS-SCREEN', why: 'Overview page mounts no mutation.' },
  { key: k.platform.payments('all'), tag: 'CROSS-SCREEN', why: 'Subscription payments are a different endpoint; a top-up review does not affect them.' },
  { key: k.platform.users({ segment: 's', q: '', limit: 20, offset: 0 }), tag: 'CROSS-SCREEN', why: 'Users page mounts no mutation.' },
  { key: k.public.plans(), tag: 'CROSS-SCREEN', why: 'Marketing/signup screens; an admin editing a plan then visiting the landing page is a different screen. Merging the two plan namespaces would change four invalidations blast radius.' },
];

// ---------------------------------------------------------------------------
const isPrefixOf = (inv: readonly unknown[], sub: readonly unknown[]) =>
  inv.length <= sub.length && hashKey(sub.slice(0, inv.length)) === hashKey(inv);

/** Throws on the first violation. Exported shape lets the self-test seed a typo. */
function check(
  subscriptions: Entry[],
  invalidations: Entry[],
  uninvalidated: Array<{ key: readonly unknown[] }>,
) {
  const orphanInvalidations = invalidations.filter(
    (i) => !subscriptions.some((s) => isPrefixOf(i.key, s.key)),
  );
  if (orphanInvalidations.length) {
    throw new Error(
      `invalidation reaches no subscription: ${orphanInvalidations
        .map((i) => `${hashKey(i.key)} (${i.from})`)
        .join(', ')}`,
    );
  }
  const allowed = new Set(uninvalidated.map((u) => hashKey(u.key)));
  const unreached = subscriptions.filter(
    (s) => !invalidations.some((i) => isPrefixOf(i.key, s.key)) && !allowed.has(hashKey(s.key)),
  );
  if (unreached.length) {
    throw new Error(
      `subscription nothing invalidates and nothing explains: ${unreached
        .map((s) => `${hashKey(s.key)} (${s.from})`)
        .join(', ')}`,
    );
  }
}

describe('T2 — invalidations and subscriptions intersect', () => {
  it('the registries are complete', () => {
    expect(SUBSCRIPTIONS).toHaveLength(62);
    // 36 shapes as written in source; the computed segment is one spelling that
    // covers two branches, so it occupies two rows here.
    expect(INVALIDATIONS).toHaveLength(37);
    // …but only 35 distinct keys, because those two branches ARE the two list
    // prefixes: importTarget('customers') is customersAll(), and
    // importTarget('products') is productsAll(). That coincidence is the point
    // of the entry — the import screen refreshes the list it has just filled.
    expect(new Set(INVALIDATIONS.map((i) => hashKey(i.key))).size).toBe(35);
    expect(hashKey(k.tenant.importTarget('customers'))).toBe(hashKey(k.tenant.customersAll()));
    expect(hashKey(k.tenant.importTarget('products'))).toBe(hashKey(k.tenant.productsAll()));
    expect(SUBSCRIPTIONS.every((s) => s.from.length > 0)).toBe(true);
  });

  it('every invalidation prefix-matches at least one subscription', () => {
    expect(() => check(SUBSCRIPTIONS, INVALIDATIONS, UNINVALIDATED)).not.toThrow();
  });

  it('every subscription is either invalidated or explicitly accounted for', () => {
    const unreached = SUBSCRIPTIONS.filter(
      (s) => !INVALIDATIONS.some((i) => isPrefixOf(i.key, s.key)),
    );
    expect(unreached).toHaveLength(19);
    expect(UNINVALIDATED).toHaveLength(19);
    // the allowlist and the measured set are the same set, not merely the same size
    expect(new Set(UNINVALIDATED.map((u) => hashKey(u.key)))).toEqual(
      new Set(unreached.map((s) => hashKey(s.key))),
    );
  });

  it('no allowlist row has quietly become covered', () => {
    // A row that starts being invalidated must be deleted from the list, not left
    // to rot into a silent pass.
    for (const u of UNINVALIDATED) {
      expect(INVALIDATIONS.some((i) => isPrefixOf(i.key, u.key))).toBe(false);
      expect(u.why.length).toBeGreaterThan(20);
    }
  });

  it('the allowlist keeps the reasoned breakdown', () => {
    const count = (t: Tag) => UNINVALIDATED.filter((u) => u.tag === t).length;
    expect(count('KNOWN-DEFECT')).toBe(0);
    expect(count('NO-WRITER')).toBe(6);
    expect(count('NEVER-SUBSCRIBED')).toBe(6);
    expect(count('CROSS-SCREEN')).toBe(7);
  });

  it('the profile write targets the me root exactly, not as a prefix', () => {
    // `setQueryData(queryKeys.me.root(), data)` is an exact write, not an invalidation.
    expect(hashKey(k.me.root())).toBe(hashKey(['me']));
    expect(k.me.root()).toHaveLength(1);
  });
});

describe('T2 — the detector bites', () => {
  it('rejects an invalidation that reaches nothing', () => {
    const typo = [...INVALIDATIONS, e(['tenant', 'customerss'] as const, 'seeded')];
    expect(() => check(SUBSCRIPTIONS, typo, UNINVALIDATED)).toThrow(/reaches no subscription/);
  });

  it('rejects a new subscription that is neither invalidated nor explained', () => {
    const typo = [...SUBSCRIPTIONS, e(['tenant', 'brand-new-list'] as const, 'seeded')];
    expect(() => check(typo, INVALIDATIONS, UNINVALIDATED)).toThrow(/nothing invalidates/);
  });
});
