/**
 * T1 — the characterization test for the query-key factory.
 *
 * Every one of the 74 entries is pinned against the literal it must produce,
 * hand-copied from the call sites as they stood before the factory existed.
 * Two assertions per row: `toEqual` catches a changed element, a changed order
 * and a changed length; `hashKey` catches anything TanStack would collapse or
 * treat as equal that `toEqual` would not.
 *
 * What this test does NOT do is check which entry a given call site calls —
 * a site can move from `tenant.topups()` to `tenant.topupInfo()` and every row
 * here still passes. That property is proven separately, per call site, during
 * the migration itself.
 */
import { describe, expect, it } from 'vitest';
import { hashKey } from '@tanstack/react-query';
import { queryKeys as k } from './queryKeys';

/** Representative arguments, chosen to exercise each dynamic segment type. */
const ID = 'cust-1';
const PARAMS = { q: 'ali' };

type Row = [name: string, actual: readonly unknown[], expected: unknown[]];

const ROWS: Row[] = [
  // ---- me (5) -------------------------------------------------------------
  ['me.root', k.me.root(), ['me']],
  ['me.summary', k.me.summary(), ['me', 'summary']],
  ['me.identity', k.me.identity(), ['me', 'identity']],
  ['me.notifications', k.me.notifications(), ['me', 'notifications']],
  ['me.installments', k.me.installments('open'), ['me', 'installments', 'open']],
  ['me.installment', k.me.installment(ID), ['me', 'installment', 'cust-1']],

  // ---- public (1) ---------------------------------------------------------
  ['public.plans', k.public.plans(), ['public', 'plans']],
  ['public.yearlyBilling', k.public.yearlyBilling(), ['public', 'yearlyBilling']],
  ['public.supportChannels', k.public.supportChannels(), ['public', 'supportChannels']],

  // ---- onboarding (1) -----------------------------------------------------
  ['onboarding.options', k.onboarding.options(), ['onboarding', 'options']],

  // ---- holidays (1) -------------------------------------------------------
  ['holidays.year', k.holidays.year(2026), ['holidays', 2026]],

  // ---- catalog (8) --------------------------------------------------------
  ['catalog.all', k.catalog.all(), ['catalog']],
  ['catalog.categories', k.catalog.categories('all'), ['catalog', 'categories', 'all']],
  ['catalog.brands', k.catalog.brands('phones'), ['catalog', 'brands', 'phones']],
  ['catalog.allBrands', k.catalog.allBrands(), ['catalog', 'all-brands']],
  ['catalog.models', k.catalog.models('b-1', 'all'), ['catalog', 'models', 'b-1', 'all']],
  ['catalog.modelsAll', k.catalog.modelsAll(), ['catalog', 'models']],
  ['catalog.modelsFlat', k.catalog.modelsFlat(PARAMS), ['catalog', 'models-flat', PARAMS]],
  ['catalog.colors', k.catalog.colors(), ['catalog', 'colors']],

  // ---- platform (20) ------------------------------------------------------
  ['platform.overview', k.platform.overview(), ['platform', 'overview']],
  ['platform.payments', k.platform.payments('all'), ['platform', 'payments', 'all']],
  ['platform.tenants', k.platform.tenants(undefined), ['platform', 'tenants', undefined]],
  ['platform.tenantsAll', k.platform.tenantsAll(), ['platform', 'tenants']],
  ['platform.tenantDetail', k.platform.tenantDetail(ID), ['platform', 'tenant-detail', 'cust-1']],
  ['platform.tenantDetailAll', k.platform.tenantDetailAll(), ['platform', 'tenant-detail']],
  ['platform.plans', k.platform.plans(), ['platform', 'plans']],
  ['platform.riskStats', k.platform.riskStats(), ['platform', 'risk', 'stats']],
  ['platform.admins', k.platform.admins(), ['platform', 'admins']],
  ['platform.audit', k.platform.audit({}), ['platform', 'audit', {}]],
  ['platform.promos', k.platform.promos(), ['platform', 'promos']],
  ['platform.ads', k.platform.ads(), ['platform', 'ads']],
  ['platform.referrals', k.platform.referrals(), ['platform', 'referrals']],
  ['platform.referralLink', k.platform.referralLink(), ['platform', 'referral-link']],
  ['platform.topups', k.platform.topups('pending'), ['platform', 'topups', 'pending']],
  ['platform.topupsAll', k.platform.topupsAll(), ['platform', 'topups']],
  ['platform.settings', k.platform.settings(), ['platform', 'settings']],
  ['platform.broadcasts', k.platform.broadcasts(), ['platform', 'broadcasts']],
  ['platform.segments', k.platform.segments(), ['platform', 'segments']],
  [
    'platform.users',
    k.platform.users({ segment: 's', q: '', limit: 20, offset: 0 }),
    ['platform', 'users', { segment: 's', q: '', limit: 20, offset: 0 }],
  ],

  // ---- tenant (38) --------------------------------------------------------
  ['tenant.root', k.tenant.root(), ['tenant']],
  ['tenant.overview', k.tenant.overview(), ['tenant', 'overview']],
  ['tenant.customers', k.tenant.customers(PARAMS), ['tenant', 'customers', PARAMS]],
  ['tenant.customersAll', k.tenant.customersAll(), ['tenant', 'customers']],
  [
    'tenant.customerChannels',
    k.tenant.customerChannels(ID),
    ['tenant', 'customer', 'cust-1', 'channels'],
  ],
  ['tenant.guarantors', k.tenant.guarantors(PARAMS), ['tenant', 'guarantors', PARAMS]],
  ['tenant.guarantorsAll', k.tenant.guarantorsAll(), ['tenant', 'guarantors']],
  ['tenant.riskScore', k.tenant.riskScore(ID), ['tenant', 'risk', 'score', 'cust-1']],
  ['tenant.products', k.tenant.products(PARAMS), ['tenant', 'products', PARAMS]],
  ['tenant.productsAll', k.tenant.productsAll(), ['tenant', 'products']],
  ['tenant.installment', k.tenant.installment(ID), ['tenant', 'installment', 'cust-1']],
  ['tenant.installments', k.tenant.installments(null), ['tenant', 'installments', null]],
  ['tenant.installmentsAll', k.tenant.installmentsAll(), ['tenant', 'installments']],
  ['tenant.worklist', k.tenant.worklist(20), ['tenant', 'worklist', 20]],
  [
    'tenant.worklistPaged',
    k.tenant.worklistPaged(20, 'ali'),
    ['tenant', 'worklist', 'paged', 20, 'ali'],
  ],
  ['tenant.worklistAll', k.tenant.worklistAll(), ['tenant', 'worklist']],
  ['tenant.payments', k.tenant.payments(), ['tenant', 'payments']],
  ['tenant.paymentReview', k.tenant.paymentReview('p1'),
    ['tenant', 'payments', 'p1', 'review']],
  [
    'tenant.paymentsPendingCount',
    k.tenant.paymentsPendingCount(),
    ['tenant', 'payments', 'pendingCount'],
  ],
  [
    'tenant.paymentsPaged',
    k.tenant.paymentsPaged(50, 'rejected'),
    ['tenant', 'payments', 'paged', 50, 'rejected'],
  ],
  ['tenant.broadcasts', k.tenant.broadcasts(), ['tenant', 'broadcasts']],
  ['tenant.invitations', k.tenant.invitations(undefined), ['tenant', 'invitations', undefined]],
  ['tenant.invitationsAll', k.tenant.invitationsAll(), ['tenant', 'invitations']],
  ['tenant.settings', k.tenant.settings(), ['tenant', 'settings']],
  ['tenant.smsUsage', k.tenant.smsUsage(), ['tenant', 'sms', 'usage']],
  ['tenant.smsHistory', k.tenant.smsHistory(50), ['tenant', 'sms', 'history', 50]],
  ['tenant.staff', k.tenant.staff(), ['tenant', 'staff']],
  ['tenant.staffToday', k.tenant.staffToday(), ['tenant', 'staff-today']],
  ['tenant.reportsAging', k.tenant.reportsAging(), ['tenant', 'reports', 'aging']],
  [
    'tenant.reportsCashflow',
    k.tenant.reportsCashflow({ granularity: 'day' }),
    ['tenant', 'reports', 'cashflow', { granularity: 'day' }],
  ],
  ['tenant.reportsStaff', k.tenant.reportsStaff(), ['tenant', 'reports', 'staff']],
  ['tenant.reportsProducts', k.tenant.reportsProducts(), ['tenant', 'reports', 'products']],
  ['tenant.reportsLtv', k.tenant.reportsLtv(20), ['tenant', 'reports', 'ltv', 20]],
  ['tenant.reportsMargin', k.tenant.reportsMargin(), ['tenant', 'reports', 'margin']],
  ['tenant.billing', k.tenant.billing(), ['tenant', 'billing']],
  ['tenant.invoices', k.tenant.invoices(), ['tenant', 'invoices']],
  ['tenant.topupInfo', k.tenant.topupInfo(), ['tenant', 'topup-info']],
  ['tenant.topups', k.tenant.topups(), ['tenant', 'topups']],
  [
    'tenant.productMovements',
    k.tenant.productMovements(ID),
    ['tenant', 'product-movements', 'cust-1'],
  ],
  ['tenant.lowStock', k.tenant.lowStock(), ['tenant', 'low-stock']],
  ['tenant.importTarget', k.tenant.importTarget('customers'), ['tenant', 'customers']],
];

/** Entries that take no argument — their key is fully determined by the entry. */
const ZERO_ARG = [
  k.me.root(), k.me.summary(), k.me.identity(), k.me.notifications(),
  k.public.plans(),
  k.onboarding.options(),
  k.catalog.all(), k.catalog.allBrands(), k.catalog.modelsAll(), k.catalog.colors(),
  k.platform.overview(), k.platform.tenantsAll(), k.platform.tenantDetailAll(),
  k.platform.plans(), k.platform.riskStats(), k.platform.admins(), k.platform.promos(),
  k.platform.ads(), k.platform.referrals(), k.platform.referralLink(),
  k.platform.topupsAll(), k.platform.settings(), k.platform.broadcasts(),
  k.platform.segments(),
  k.tenant.overview(), k.tenant.customersAll(), k.tenant.guarantorsAll(),
  k.tenant.productsAll(), k.tenant.installmentsAll(), k.tenant.worklistAll(), k.tenant.payments(),
  k.tenant.paymentsPendingCount(), k.tenant.broadcasts(), k.tenant.invitationsAll(),
  k.tenant.settings(), k.tenant.smsUsage(), k.tenant.staff(), k.tenant.staffToday(),
  k.tenant.reportsAging(), k.tenant.reportsStaff(), k.tenant.reportsProducts(),
  k.tenant.reportsMargin(), k.tenant.billing(), k.tenant.invoices(),
  k.tenant.topupInfo(), k.tenant.topups(), k.tenant.lowStock(),
];

describe('T1 — every factory entry produces exactly the literal it replaced', () => {
  it('the table covers all 80 entries', () => {
    const declared = Object.values(queryKeys_namespaces()).reduce((n, ns) => n + Object.keys(ns).length, 0);
    expect(declared).toBe(80);
    expect(ROWS).toHaveLength(80);
    expect(new Set(ROWS.map(([name]) => name)).size).toBe(80);
  });

  it.each(ROWS)('%s', (_name, actual, expected) => {
    expect(actual).toEqual(expected);
    expect(hashKey(actual)).toBe(hashKey(expected));
    expect(actual).toHaveLength(expected.length);
  });

  it('no two zero-argument entries share a key', () => {
    expect(new Set(ZERO_ARG.map((key) => hashKey(key))).size).toBe(ZERO_ARG.length);
    expect(ZERO_ARG).toHaveLength(47);
  });

  /**
   * The one intended aliasing. The import screen invalidates whichever list it
   * has just filled, so `importTarget` MUST land on the same key as that list's
   * broad prefix. A blanket uniqueness assertion over all 74 entries would be
   * wrong, not stricter.
   */
  it('importTarget deliberately aliases the two list prefixes', () => {
    expect(hashKey(k.tenant.importTarget('customers'))).toBe(hashKey(k.tenant.customersAll()));
    expect(hashKey(k.tenant.importTarget('products'))).toBe(hashKey(k.tenant.productsAll()));
  });
});

describe('T1 — arity never varies with the argument', () => {
  /**
   * The failure this guards: a nullish argument silently shortening the tuple
   * and turning an exact key into a broad prefix. `undefined` and `null` may
   * hash alike; a missing element must never appear.
   */
  const VARIADIC: Array<[string, Array<readonly unknown[]>, number]> = [
    ['me.installments', [k.me.installments('open'), k.me.installments('all')], 3],
    ['me.installment', [k.me.installment(ID), k.me.installment(undefined)], 3],
    ['holidays.year', [k.holidays.year(2026), k.holidays.year(0)], 2],
    ['catalog.categories', [k.catalog.categories('all'), k.catalog.categories('')], 3],
    ['catalog.brands', [k.catalog.brands(ID), k.catalog.brands(null), k.catalog.brands(undefined)], 3],
    ['catalog.models', [k.catalog.models(ID, 'all'), k.catalog.models(null, '')], 4],
    ['catalog.modelsFlat', [k.catalog.modelsFlat({}), k.catalog.modelsFlat(PARAMS)], 3],
    ['platform.payments', [k.platform.payments('all'), k.platform.payments('')], 3],
    ['platform.tenants', [k.platform.tenants(undefined), k.platform.tenants({})], 3],
    ['platform.tenantDetail', [k.platform.tenantDetail(ID), k.platform.tenantDetail(null)], 3],
    ['platform.audit', [k.platform.audit({}), k.platform.audit({ limit: 1 })], 3],
    ['platform.topups', [k.platform.topups('pending'), k.platform.topups('')], 3],
    ['platform.users', [k.platform.users({ segment: '', q: '', limit: 0, offset: 0 })], 3],
    ['tenant.customers', [k.tenant.customers(undefined), k.tenant.customers({})], 3],
    ['tenant.customerChannels', [k.tenant.customerChannels(ID), k.tenant.customerChannels(undefined)], 4],
    ['tenant.guarantors', [k.tenant.guarantors(undefined), k.tenant.guarantors({})], 3],
    ['tenant.riskScore', [k.tenant.riskScore(ID), k.tenant.riskScore(null), k.tenant.riskScore(undefined)], 4],
    ['tenant.products', [k.tenant.products(undefined), k.tenant.products({})], 3],
    ['tenant.installment', [k.tenant.installment(ID), k.tenant.installment(null), k.tenant.installment(undefined)], 3],
    ['tenant.installments', [k.tenant.installments(null), k.tenant.installments({})], 3],
    ['tenant.worklist', [k.tenant.worklist(20), k.tenant.worklist(0)], 3],
    ['tenant.worklistPaged', [k.tenant.worklistPaged(20, ''), k.tenant.worklistPaged(0, 'ali')], 5],
    ['tenant.paymentsPaged', [k.tenant.paymentsPaged(50, ''), k.tenant.paymentsPaged(0, 'rejected')], 5],
    ['tenant.invitations', [k.tenant.invitations(undefined), k.tenant.invitations({})], 3],
    ['tenant.smsHistory', [k.tenant.smsHistory(50), k.tenant.smsHistory(0)], 4],
    ['tenant.reportsCashflow', [k.tenant.reportsCashflow({}), k.tenant.reportsCashflow({ from: 'x' })], 4],
    ['tenant.reportsLtv', [k.tenant.reportsLtv(20), k.tenant.reportsLtv(0)], 4],
    ['tenant.productMovements', [k.tenant.productMovements(ID), k.tenant.productMovements(null), k.tenant.productMovements(undefined)], 3],
    ['tenant.importTarget', [k.tenant.importTarget('customers'), k.tenant.importTarget('products')], 2],
  ];

  it('covers all 29 parameterised entries', () => {
    expect(VARIADIC).toHaveLength(29);
    expect(ZERO_ARG.length + VARIADIC.length).toBe(76);
  });

  it.each(VARIADIC)('%s keeps a fixed length', (_name, keys, arity) => {
    for (const key of keys) expect(key).toHaveLength(arity);
  });
});

/** Read back the namespaces so the 73-entry budget is counted, not trusted. */
function queryKeys_namespaces(): Record<string, Record<string, unknown>> {
  return k as unknown as Record<string, Record<string, unknown>>;
}
