/**
 * The single source of every TanStack Query cache key in the app.
 *
 * Read this before adding an entry — the rules below are not stylistic, each one
 * prevents a specific class of silent cache bug.
 *
 * 1. **Every entry is a function, called with `()` even when it takes nothing.**
 *    Uniform call syntax everywhere, a fresh array per call (so no caller can
 *    mutate a shared constant), and a single ban on `queryKey: [` is enough to
 *    keep raw literals out of production source.
 *
 * 2. **Return `as const` tuples.** They satisfy TanStack's `readonly unknown[]`
 *    key type, and the literal types turn a mistyped segment into a compile
 *    error here at the definition rather than a cache miss at runtime.
 *
 * 3. **Arity is fixed per entry.** An optional argument becomes a REQUIRED
 *    parameter that accepts `undefined` — never an omitted element. `undefined`
 *    and `null` hash identically, so a nullable value is harmless; a missing
 *    element is not. `['platform','tenants', undefined]` is a specific list,
 *    while `['platform','tenants']` is the prefix that invalidates every list.
 *    Dropping the element silently converts an exact key into a broad one.
 *
 * 4. **No normalisation.** Objects enter the tuple by reference: no spread,
 *    sort, clone or key-strip. Fallbacks (`?? 'all'`, `?? null`, `?? {}`) and
 *    `.trim()` stay at the call site, where they already are, so this module
 *    can never change an identity by "tidying" an argument.
 *
 * 5. **No defaults here.** A hook that declares `limit = 20` resolves the
 *    default itself and passes the resolved value.
 *
 * 6. **Nesting in this object never implies nesting in the key.** Each entry
 *    returns its complete literal: `tenant.staffToday()` is
 *    `['tenant','staff-today']`, NOT `['tenant','staff','today']`. Sibling
 *    names that look hierarchical but are not —
 *    `staff`/`staff-today`, `models`/`models-flat`, `topups`/`topup-info`,
 *    `installment`/`installments`, `tenants`/`tenant-detail`,
 *    `referrals`/`referral-link` — would otherwise start prefix-matching each
 *    other and widen what a mutation invalidates.
 *
 * 7. **Broad prefixes are their own entries with literal bodies**, never
 *    `detail(id).slice(0, -1)`. They are the `…All()` names below.
 *
 * 8. **Two entries are deliberately dual-role**, marked at their definition:
 *    `me.root()` and `tenant.payments()` are each an exact subscription key AND
 *    the prefix that covers their namespace's other keys.
 *
 * Do not re-export this module from `@/lib/queries` or `@/lib/mutations`; both
 * barrels have a contract test that pins their exact export list.
 */

/** Filter bags are declared structurally so this module imports nothing. */
type CatalogModelsFlatParams = { category_id?: string; brand_id?: string; q?: string };
type PlatformTenantsParams = { status?: string; limit?: number };
type PlatformAuditFilters = {
  action?: string; tenant_id?: string; date_from?: string; date_to?: string;
  limit?: number; offset?: number;
};
type PlatformUsersParams = { segment: string; q: string; limit: number; offset: number };
type TenantCustomersParams = { q?: string; status_filter?: string; limit?: number };
type TenantGuarantorsParams = { q?: string };
type TenantProductsParams = { q?: string };
type TenantInstallmentsParams = { q?: string; status_filter?: string; category?: string };
type TenantInvitationsParams = { kind?: 'customer' | 'staff' | 'guarantor'; only_open?: boolean };
type TenantCashflowParams = { from?: string; to?: string; granularity?: 'day' | 'week' | 'month' };

export const queryKeys = {
  me: {
    /** Dual-role: the exact key `useMe` subscribes to, and the prefix over every `me.*` key. */
    root: () => ['me'] as const,
    summary: () => ['me', 'summary'] as const,
    identity: () => ['me', 'identity'] as const,
    notifications: () => ['me', 'notifications'] as const,
    installments: (filter: 'open' | 'completed' | 'all') => ['me', 'installments', filter] as const,
    installment: (id: string | undefined) => ['me', 'installment', id] as const,
  },

  public: {
    plans: () => ['public', 'plans'] as const,
    /** The yearly-billing promo. Its own key, not a suffix on `plans()`: the
     *  landing toggle needs the percentage before any plan renders, and an
     *  ad-hoc array literal here is exactly what `queryKeys.guard` forbids. */
    yearlyBilling: () => ['public', 'yearlyBilling'] as const,
    supportChannels: () => ['public', 'supportChannels'] as const,
  },

  onboarding: {
    options: () => ['onboarding', 'options'] as const,
  },

  holidays: {
    year: (year: number) => ['holidays', year] as const,
  },

  catalog: {
    /** Broad prefix over the whole catalog namespace. */
    all: () => ['catalog'] as const,
    categories: (businessType: string) => ['catalog', 'categories', businessType] as const,
    brands: (categorySlug: string | null | undefined) => ['catalog', 'brands', categorySlug] as const,
    allBrands: () => ['catalog', 'all-brands'] as const,
    models: (brandId: string | null | undefined, categoryId: string) =>
      ['catalog', 'models', brandId, categoryId] as const,
    /** Broad prefix over `models` only. Does NOT reach `models-flat` (rule 6). */
    modelsAll: () => ['catalog', 'models'] as const,
    modelsFlat: (params: CatalogModelsFlatParams) => ['catalog', 'models-flat', params] as const,
    colors: () => ['catalog', 'colors'] as const,
  },

  platform: {
    overview: () => ['platform', 'overview'] as const,
    payments: (statusFilter: string) => ['platform', 'payments', statusFilter] as const,
    tenants: (params: PlatformTenantsParams | undefined) => ['platform', 'tenants', params] as const,
    /** Broad prefix over every tenants list. */
    tenantsAll: () => ['platform', 'tenants'] as const,
    tenantDetail: (id: string | null | undefined) => ['platform', 'tenant-detail', id] as const,
    /** Broad prefix over every tenant detail, whatever the id. */
    tenantDetailAll: () => ['platform', 'tenant-detail'] as const,
    plans: () => ['platform', 'plans'] as const,
    riskStats: () => ['platform', 'risk', 'stats'] as const,
    admins: () => ['platform', 'admins'] as const,
    audit: (filters: PlatformAuditFilters) => ['platform', 'audit', filters] as const,
    promos: () => ['platform', 'promos'] as const,
    ads: () => ['platform', 'ads'] as const,
    referrals: () => ['platform', 'referrals'] as const,
    referralLink: () => ['platform', 'referral-link'] as const,
    topups: (status: string) => ['platform', 'topups', status] as const,
    /** Broad prefix over every top-up status. */
    topupsAll: () => ['platform', 'topups'] as const,
    settings: () => ['platform', 'settings'] as const,
    broadcasts: () => ['platform', 'broadcasts'] as const,
    segments: () => ['platform', 'segments'] as const,
    users: (params: PlatformUsersParams) => ['platform', 'users', params] as const,
  },

  tenant: {
    /** Broad prefix over EVERY tenant-scoped query — staff surfaces included,
     *  since those key under `['tenant','staff-today']` and friends. Used to
     *  drop one shop's cache before another becomes active: no key in this
     *  namespace carries a tenant id (scoping is the X-Tenant-Id header), so
     *  switching shops without this showed one tenant's money under another's
     *  name. See store/session.ts. */
    root: () => ['tenant'] as const,
    overview: () => ['tenant', 'overview'] as const,
    customers: (params: TenantCustomersParams | undefined) => ['tenant', 'customers', params] as const,
    /** Broad prefix over every customers list. */
    customersAll: () => ['tenant', 'customers'] as const,
    customerChannels: (customerId: string | undefined) =>
      ['tenant', 'customer', customerId, 'channels'] as const,
    guarantors: (params: TenantGuarantorsParams | undefined) => ['tenant', 'guarantors', params] as const,
    /** Broad prefix over every guarantors list. */
    guarantorsAll: () => ['tenant', 'guarantors'] as const,
    riskScore: (customerId: string | null | undefined) =>
      ['tenant', 'risk', 'score', customerId] as const,
    products: (params: TenantProductsParams | undefined) => ['tenant', 'products', params] as const,
    /** Broad prefix over every products list. */
    productsAll: () => ['tenant', 'products'] as const,
    installment: (id: string | null | undefined) => ['tenant', 'installment', id] as const,
    installments: (params: TenantInstallmentsParams | null) =>
      ['tenant', 'installments', params] as const,
    /** Broad prefix over every installments list. Does NOT reach `installment` (rule 6). */
    installmentsAll: () => ['tenant', 'installments'] as const,
    worklist: (limit: number) => ['tenant', 'worklist', limit] as const,
    worklistPaged: (pageSize: number, search: string) =>
      ['tenant', 'worklist', 'paged', pageSize, search] as const,
    /** Broad prefix over the whole collections queue — both the Overview preview
     *  and every `worklistPaged(pageSize, search)` variant. A write that changes
     *  queue membership cannot know which (pageSize, search) pairs are cached,
     *  so it must invalidate through this rather than name one pair. */
    worklistAll: () => ['tenant', 'worklist'] as const,
    /** Dual-role: the exact key `useTenantPayments` subscribes to, and the prefix
     *  over `paymentsPendingCount` AND every `paymentsPaged(pageSize, statusFilter)`
     *  variant — so approve/reject invalidating THIS key also refreshes whichever
     *  status tab is currently paged in, without naming each pair. */
    payments: () => ['tenant', 'payments'] as const,
    /** One payment's review screen — the Telegram notification's destination. */
    paymentReview: (paymentId: string) =>
      ['tenant', 'payments', paymentId, 'review'] as const,
    paymentsPendingCount: () => ['tenant', 'payments', 'pendingCount'] as const,
    paymentsPaged: (pageSize: number, statusFilter: string) =>
      ['tenant', 'payments', 'paged', pageSize, statusFilter] as const,
    broadcasts: () => ['tenant', 'broadcasts'] as const,
    invitations: (params: TenantInvitationsParams | undefined) =>
      ['tenant', 'invitations', params] as const,
    /** Broad prefix over every invitations list. */
    invitationsAll: () => ['tenant', 'invitations'] as const,
    settings: () => ['tenant', 'settings'] as const,
    smsUsage: () => ['tenant', 'sms', 'usage'] as const,
    smsHistory: (limit: number) => ['tenant', 'sms', 'history', limit] as const,
    staff: () => ['tenant', 'staff'] as const,
    /** A different endpoint for a different persona — NOT below `staff` (rule 6). */
    staffToday: () => ['tenant', 'staff-today'] as const,
    reportsAging: () => ['tenant', 'reports', 'aging'] as const,
    reportsCashflow: (p: TenantCashflowParams) => ['tenant', 'reports', 'cashflow', p] as const,
    reportsStaff: () => ['tenant', 'reports', 'staff'] as const,
    reportsProducts: () => ['tenant', 'reports', 'products'] as const,
    reportsLtv: (limit: number) => ['tenant', 'reports', 'ltv', limit] as const,
    reportsMargin: () => ['tenant', 'reports', 'margin'] as const,
    billing: () => ['tenant', 'billing'] as const,
    invoices: () => ['tenant', 'invoices'] as const,
    topupInfo: () => ['tenant', 'topup-info'] as const,
    topups: () => ['tenant', 'topups'] as const,
    productMovements: (productId: string | null | undefined) =>
      ['tenant', 'product-movements', productId] as const,
    lowStock: () => ['tenant', 'low-stock'] as const,
    /** The import screen writes into whichever list it just filled. */
    importTarget: (target: 'customers' | 'products') => ['tenant', target] as const,
  },
} as const;
