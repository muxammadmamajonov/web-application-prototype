/**
 * DEV-ONLY Telegram/auth + data mock — lets us drive the auth-gated operator
 * console in a plain browser (Playwright/preview) to audit every page at every
 * breakpoint, WITHOUT a real Telegram session or backend.
 *
 * SAFETY: only ever imported behind `import.meta.env.DEV && VITE_DEV_MOCK==='1'`
 * (see main.tsx). In a production build `import.meta.env.DEV` is statically false,
 * so the dynamic import is tree-shaken out — none of this ships to prod.
 *
 * It installs a custom axios adapter that short-circuits HTTP: known endpoints
 * return realistic fixtures, everything else returns a safe empty default, so
 * pages render (with data or their empty state) instead of hanging on a request.
 */
import { api, setSessionToken } from './api';
import type { MeResponse } from '@/types';

const ME: MeResponse = {
  id: 'dev-owner', telegramId: 1, telegramUsername: 'devowner',
  firstName: 'Dev', lastName: 'Owner', displayName: 'Dev Owner', photoUrl: null,
  phone: '+998901234567', email: 'dev@muddato.local', birthdate: null, gender: null,
  locale: 'uz', platformRole: 'super_admin', tenants: [],
};

// Tenant + staff personas — selected via `?mock=tenant` / `?mock=staff` so the
// business + staff consoles can be audited in a plain browser too (the persona
// resolver keys off tenants[0].role: owner/manager -> tenant, sales -> staff).
const TENANT_ME: MeResponse = {
  ...ME, id: 'dev-tenant-owner', telegramUsername: 'anvarshop', firstName: 'Anvar', lastName: 'Karimov', displayName: 'Anvar Karimov',
  platformRole: null,
  // currency is deliberately NOT the default: a shop trading in something
  // other than UZS is the case where the screens used to disagree with the
  // generated contracts, and a fixture pinned to the default makes that
  // entire class of bug invisible in the harness.
  tenants: [{ id: 't1', slug: 'demo-electronics', name: 'Demo Electronics', role: 'owner', status: 'active', planCode: 'pro', trialEndsAt: null, currency: 'USD' }],
};
const STAFF_ME: MeResponse = {
  ...ME, id: 'dev-staff', telegramUsername: 'sardorsales', firstName: 'Sardor', lastName: 'Sotuvchi', displayName: 'Sardor Sotuvchi',
  platformRole: null,
  tenants: [{ id: 't1', slug: 'demo-electronics', name: 'Demo Electronics', role: 'sales', status: 'active', planCode: 'pro', trialEndsAt: null, currency: 'USD' }],
};
/** A shop whose account is blocked — exercises the recovery gate. */
const BLOCKED_ME: MeResponse = {
  ...ME, id: 'dev-blocked-owner', telegramUsername: 'blockedshop', firstName: 'Bloklangan', lastName: 'Egasi', displayName: 'Bloklangan Egasi',
  platformRole: null,
  tenants: [{ id: 't-blocked', slug: 'blocked-shop', name: 'Blocked Shop', role: 'owner', status: 'blocked', planCode: 'lite', trialEndsAt: null }],
};
const CLIENT_ME: MeResponse = {
  ...ME, id: 'dev-client', telegramUsername: 'jasurbuyer', firstName: 'Jasur', lastName: 'Toshmatov', displayName: 'Jasur Toshmatov',
  platformRole: null, tenants: [],
};

const ONBOARDING = {
  business_types: [
    { slug: 'electronics', name_uz: 'Elektronika', name_ru: 'Электроника', emoji: '📱' },
    { slug: 'furniture', name_uz: 'Mebel', name_ru: 'Мебель', emoji: '🛋️' },
    { slug: 'appliances', name_uz: 'Maishiy texnika', name_ru: 'Бытовая техника', emoji: '🔌' },
    { slug: 'clothing', name_uz: 'Kiyim-kechak', name_ru: 'Одежда', emoji: '👕' },
    { slug: 'auto', name_uz: 'Avto ehtiyot qismlar', name_ru: 'Автозапчасти', emoji: '🚗' },
    { slug: 'other', name_uz: 'Boshqa', name_ru: 'Другое', emoji: '📦' },
  ],
  how_found_options: [],
};

const OVERVIEW = {
  mrr_estimate_uzs: 18_400_000,
  approved_this_month_uzs: 7_250_000,
  active_installments: 142,
  tenants_total: 9,
  tenants_by_status: { active: 5, demo: 2, suspended: 1, blocked: 1 },
  customers_total: 1284,
  pending_receipts: 3,
  pending_tenant_count: 2,
  revenue_series: [
    { month: 1, amount: 4_200_000 }, { month: 2, amount: 5_100_000 },
    { month: 3, amount: 4_800_000 }, { month: 4, amount: 6_300_000 },
    { month: 5, amount: 7_900_000 }, { month: 6, amount: 7_250_000 },
  ],
};

const PAYMENTS = {
  collected_this_month_uzs: 7_250_000,
  pending_amount_uzs: 1_120_000,
  pending_count: 3,
  items: [
    { id: 'p1', tenant_id: 't1', tenant_name: 'Demo Electronics', method: 'card', amount: 1_500_000, status: 'approved', created_at: '2026-06-20T10:00:00Z' },
    { id: 'p2', tenant_id: 't2', tenant_name: 'Anvar Mebel', method: 'click', amount: 850_000, status: 'pending', created_at: '2026-06-22T12:30:00Z' },
    { id: 'p3', tenant_id: 't3', tenant_name: 'Tashkent Auto', method: 'payme', amount: 2_300_000, status: 'approved', created_at: '2026-06-24T09:15:00Z' },
    { id: 'p4', tenant_id: 't4', tenant_name: 'Samarqand Style', method: 'card', amount: 270_000, status: 'pending', created_at: '2026-06-25T15:45:00Z' },
  ],
};

const RISK = {
  total_signals: 4, flagged_customers: 2, opt_in_tenant_count: 3,
  by_status: { published: 2, pending_appeal: 1 },
  published_by_severity: { late_30: 1, fraud_reported: 1 },
};

const mkTenant = (i: number, name: string, slug: string, status: string, plan: string, loc: string) => ({
  id: `t${i}`, name, slug, status, plan_code: plan, locale: loc,
  created_at: `2026-0${(i % 6) + 1}-1${i}T08:00:00Z`, contact_phone: `+99890${1000000 + i}`,
});
const TENANTS = {
  total: 5,
  items: [
    mkTenant(1, 'Demo Electronics', 'demo-electronics', 'active', 'pro', 'uz'),
    mkTenant(2, 'Anvar Mebel', 'anvar-mebel', 'active', 'lite', 'uz'),
    mkTenant(3, 'Tashkent Auto', 'tashkent-auto', 'demo', 'trial', 'ru'),
    mkTenant(4, 'Samarqand Style', 'samarqand-style', 'suspended', 'lite', 'uz'),
    mkTenant(5, 'Bukhara Home', 'bukhara-home', 'blocked', 'pro', 'ru'),
  ],
};

/**
 * Owner-defined plans (R36, free-form), in the shape `GET /platform/plans`
 * serializes — `id` included.
 *
 * `id` is what PlansPage keys its table rows and cards on. A fixture without it
 * gave every row the same (absent) key, so React could not reconcile the list
 * on the one screen whose purpose is adding, reordering and deleting plans.
 */
const mkPlan = (
  i: number, code: string, nameUz: string, nameRu: string,
  price: number, maxActive: number | null, maxStaff: number | null,
  extra: Record<string, unknown> = {},
) => ({
  id: `pl${i}`, code, name_uz: nameUz, name_ru: nameRu,
  description_uz: null, description_ru: null,
  price_monthly: price, max_active_installments: maxActive,
  features: {}, is_active: true,
  bot_mode: 'shared', allow_custom_domain: false, max_custom_fields: null,
  max_staff: maxStaff, broadcasts_per_month: null,
  max_branches: null, overage: {},
  is_popular: code === 'pro', is_public: true, is_contact_sales: false,
  allow_pdf_template_upload: false, allow_api_keys: false, allow_outbound_webhooks: false,
  sms_included_per_month: 0, commission_management: false, multi_branch: false,
  risk_network_access: false, reports_advanced: false,
  badge: null, price_per_installment: null, sort_order: i,
  ...extra,
});
const PLANS = {
  items: [
    mkPlan(1, 'lite', 'Lite', 'Лайт', 299_000, 50, 2),
    mkPlan(2, 'pro', 'Pro', 'Про', 799_000, 500, 10, { badge: 'most_popular', reports_advanced: true }),
    mkPlan(3, 'custom', 'Custom', 'Индивидуальный', 0, null, null, {
      is_contact_sales: true, badge: 'custom', price_per_installment: 5_000,
    }),
  ],
};

// One tenant-detail fixture (the drawer reads stats/balance from here; identity
// comes from the clicked row). Served for any /platform/tenants/<id>/detail.
const TENANT_DETAIL = {
  id: 't1', name: 'Demo Electronics', slug: 'demo-electronics',
  status: 'active', plan_code: 'pro', locale: 'uz',
  contact_phone: '+998901112233', billing_email: 'billing@demo.uz',
  balance: 2_400_000, created_at: '2026-01-12T08:00:00Z', trial_ends_at: null,
  stats: { customers: 312, active_contracts: 148, financed: 84_000_000, received: 61_500_000, staff: 7 },
};

// ── tenant + staff fixtures (for ?mock=tenant / ?mock=staff) ─────────────────
const TENANT_OVERVIEW = {
  tenant: { id: 't1', name: 'Demo Electronics', status: 'active' },
  debt_total_uzs: 84_000_000, overdue_uzs: 6_200_000, due_today_uzs: 1_450_000,
  pending_receipts: 3,
  installments_by_status: { active: 148, overdue: 12, completed: 64, pending: 5 },
};
// (MUDD-140) A handful of the last 14 local days, so the dashboard's income
// chart can be measured with real bars instead of always falling through to
// the generic empty-array fallback below. Sparse on purpose — the real
// endpoint only emits a `series` entry for a day with at least one approved
// payment, and the chart must fill the gaps itself.
function localDayKey(offsetDays: number): string {
  const d = new Date();
  d.setDate(d.getDate() - offsetDays);
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
}
const TENANT_CASHFLOW = {
  from: localDayKey(13), to: localDayKey(0), granularity: 'day', currency: 'UZS',
  series: [
    { period: localDayKey(9), count: 3, amount: 5_400_000 },
    { period: localDayKey(6), count: 1, amount: 1_200_000 },
    { period: localDayKey(5), count: 4, amount: 8_950_000 },
    { period: localDayKey(2), count: 2, amount: 3_100_000 },
    { period: localDayKey(0), count: 1, amount: 900_000 },
  ],
  total: 19_550_000,
  total_by_currency: { UZS: 19_550_000 },
};
const mkCustomer = (i: number, name: string, phone: string, status: string) => ({
  id: `c${i}`, full_name: name, phone, secondary_phone: null, passport_series: 'AA', passport_number: `${1000000 + i}`,
  passport_id: null, jshshir: null, birthdate: null, address: 'Toshkent', status, telegram_id: null, notes: null,
  employment_place: null, monthly_income: null, mehnat_daftarchasi_photo_urls: [], created_at: `2026-0${(i % 6) + 1}-12T08:00:00Z`,
});
const TENANT_CUSTOMERS = {
  total: 4,
  items: [
    mkCustomer(1, 'Jasur Toshmatov', '+998901112233', 'active'),
    mkCustomer(2, 'Dilnoza Karimova', '+998901112244', 'active'),
    mkCustomer(3, 'Sherzod Aliyev', '+998901112255', 'pending'),
    mkCustomer(4, 'Madina Yusupova', '+998901112266', 'blocked'),
  ],
};
const STAFF_TODAY = { received_today: 3_250_000, pending_count: 4, new_customers_today: 2 };
const TENANT_BILLING = {
  plan_code: 'pro', status: 'active', trial_ends_at: null,
  active_installments: 148, limits: {}, overage: {}, base_price: 450_000,
  projected: null, suggestion: null, last_invoice: null,
};
const TENANT_SETTINGS = {
  profile: {
    business_name: 'Demo Electronics', logo_url: null, address: 'Toshkent, Yunusobod',
    contact_phone: '+998901112233', contact_email: 'info@demo.uz', contact_telegram: '@demo_shop',
    business_hours: { start: '09:00', end: '20:00' }, currency: 'UZS', default_locale: 'uz',
    receipt_footer: null, business_type: 'electronics', business_type_other: null,
  },
  reminders: {
    reminder_target: 'owner', reminder_channel_id: null, reminder_overdue_every_days: 3,
    reminder_offsets_days: [3, 1, 0], reminder_templates: {}, notification_prefs: {},
  },
  calc: {
    default_calc_formula: 'annuity', default_markup_pct: 0, default_max_months: 12,
    default_late_fee_pct_per_day: 0.1, default_min_down_pct: 20, early_payoff_discount_pct: 0,
  },
  payments: {
    payment_methods: { cash: true, card: true, transfer: true, click: false, payme: false, cards: [] },
    click_merchant_id: null, click_service_id: null, click_secret_key: null,
    payme_merchant_id: null, payme_secret_key: null, card_payment_instructions: null,
  },
};

// ── client (customer, ?mock=client) fixtures ──────────────────────────────
const MY_SUMMARY = {
  total_outstanding: '4200000', due_this_month: '700000', due_today: '0',
  overdue_count: 0, installments_count: 1, next_due_date: '2026-07-15',
};
const MY_INSTALLMENT_DETAIL = {
  id: 'i1',
  tenant: { id: 't1', name: 'Demo Electronics', logo: null, slug: 'demo-electronics' },
  contract_number: 'BE-2026-0142', reference: null, status: 'active',
  principal: '8400000', paid_amount: '4200000', remaining: '4200000',
  monthly_amount: '700000', total_amount: '8400000', down_payment: '840000',
  months: 12, paid_count: 6, start_date: '2026-01-15', next_due_date: '2026-07-15',
  headline: null, items: [], schedule: [],
  // The real endpoint always returns `payments` as a list. Omitting it made the
  // customer detail page throw on `inst.payments.filter(...)`. Amounts are
  // strings here, matching MyInstallmentDetail, and sum to `paid_amount`.
  payments: Array.from({ length: 6 }, (_, n) => ({
    id: `mp${n + 1}`,
    amount: '700000',
    method: 'cash',
    status: 'approved',
    paid_at: `2026-0${n + 1}-15T10:00:00Z`,
    external_ref: null,
    receipt_image_url: null,
  })),
};
const MY_PAYMENT_METHODS = {
  methods: [
    { method: 'click', label: 'Click', online: true },
    { method: 'payme', label: 'Payme', online: true },
    { method: 'cash', label: "Naqd / o'tkazma", online: false },
  ],
  card_instructions: null,
};

/**
 * Pending balance top-ups — the platform Payments page's only retained content.
 * Without a fixture here that page can only ever be reviewed in its empty state,
 * which is how a short-record layout bug in its detail sheet reached production.
 * Deliberately SHORT (the real records are ~6 key/value rows) so the sheet is
 * exercised at the size that exposed the problem, and one row omits the receipt
 * so the "Chek biriktirilmagan" branch is reachable too.
 */
const TOPUPS = [
  {
    id: 'tp1', tenant_id: 't1', tenant_name: 'Demo Electronics',
    amount: 1_000_000, method: 'card', status: 'pending',
    dest_card_label: '···3478', dest_card_id: 'c1',
    receipt_url: 'https://example.invalid/receipt.jpg', note: null,
    created_at: '2026-08-08T14:38:00Z', created_by_name: 'Anvar Karimov',
    reviewed_by_name: null, reviewed_at: null,
  },
  {
    id: 'tp2', tenant_id: 't2', tenant_name: 'Zamon Mebel',
    amount: 450_000, method: 'transfer', status: 'pending',
    dest_card_label: null, dest_card_id: null,
    receipt_url: null, note: 'Avgust oyi uchun',
    created_at: '2026-08-08T09:12:00Z', created_by_name: 'Dilnoza Rahimova',
    reviewed_by_name: null, reviewed_at: null,
  },
];

/**
 * The global colour palette (`catalog_colors`). Present so the category builder
 * shows a real palette the moment V2_SURFACES_ENABLED is flipped for review —
 * without it that screen renders "palette unavailable", which is exactly the
 * state that would tempt someone to re-add the hardcoded COLOR_POOL this
 * fixture exists to keep deleted. Hexes match the live rows.
 */
const CATALOG_COLORS = [
  { id: 'c1', slug: 'black',      name_uz: 'Qora',           name_ru: 'Чёрный',          hex: '#000000', sort_order: 1,  is_active: true },
  { id: 'c2', slug: 'white',      name_uz: 'Oq',             name_ru: 'Белый',           hex: '#FFFFFF', sort_order: 2,  is_active: true },
  { id: 'c3', slug: 'gray',       name_uz: 'Kulrang',        name_ru: 'Серый',           hex: '#808080', sort_order: 3,  is_active: true },
  { id: 'c4', slug: 'silver',     name_uz: 'Kumush',         name_ru: 'Серебристый',     hex: '#C0C0C0', sort_order: 4,  is_active: true },
  { id: 'c5', slug: 'space-gray', name_uz: 'Kosmik kulrang', name_ru: 'Космический серый', hex: '#4A4A4A', sort_order: 5, is_active: true },
  { id: 'c6', slug: 'graphite',   name_uz: 'Grafit',         name_ru: 'Графитовый',      hex: '#383838', sort_order: 6,  is_active: true },
];

/**
 * The platform top-up sheet. The wallet currency is so'm — deliberately NOT
 * this fixture shop's USD, so the harness shows a shop currency and a platform
 * currency that differ. Both cards declare a `network`; one that did not would
 * be withheld.
 */
const TENANT_TOPUP_INFO = {
  balance: 200_000,
  currency: 'UZS',
  terms: { uz: '', ru: '' },
  cards: [
    { id: 'c-uz', number: '5614000000002802', bank: 'Kapital Bank', holder: 'EXAMPLE LLC',
      scheme: 'uzcard', network: 'UZCARD', settlement_currency: 'UZS' },
    { id: 'c-humo', number: '9860160101234567', bank: 'Ipoteka Bank', holder: 'EXAMPLE LLC',
      scheme: 'humo', network: 'HUMO', settlement_currency: 'UZS' },
  ],
  cards_withheld: false,
  providers: { payme: false, click: false },
  can_topup: true,
};

/** Exact-path (query-stripped) fixtures. Anything unmatched → safe default. */
const ROUTES: Record<string, unknown> = {
  'GET /tenant/billing/topup-info': TENANT_TOPUP_INFO,
  'GET /platform/topups': { items: TOPUPS },
  'GET /catalog/colors': CATALOG_COLORS,
  'GET /auth/me': ME,
  'GET /onboarding/options': ONBOARDING,
  'GET /platform/overview': OVERVIEW,
  'GET /platform/payments': PAYMENTS,
  'GET /platform/risk/stats': RISK,
  'GET /platform/tenants': TENANTS,
  'GET /platform/plans': PLANS,
  'GET /tenant/overview': TENANT_OVERVIEW,
  'GET /tenant/reports/cashflow': TENANT_CASHFLOW,
  'GET /tenant/customers': TENANT_CUSTOMERS,
  'GET /tenant/billing': TENANT_BILLING,
  'GET /tenant/settings': TENANT_SETTINGS,
  'GET /staff/today': STAFF_TODAY,
  'GET /staff/stats': STAFF_TODAY,
  'GET /me/summary': MY_SUMMARY,
  // Unverified by default so the verification surface is reachable in the dev
  // preview; ?identity=verified flips it.
  // JoinPage preflight. ?identity=verified|mismatch|foreign picks the state.
  'POST /inv/identity': (() => {
    const q = typeof location !== 'undefined'
      ? new URLSearchParams(location.search).get('identity') : null;
    if (q === 'verified') return { state: 'VERIFIED', verified: true,
      phoneMasked: '+998•••••••33', matchesInvitation: true,
      knownName: 'Example User', telegramName: 'Muxammad' };
    if (q === 'mismatch') return { state: 'PHONE_DOES_NOT_MATCH_INVITATION', verified: true,
      phoneMasked: '+998•••••••44', matchesInvitation: false,
      knownName: null, telegramName: 'Muxammad' };
    if (q === 'foreign') return { state: 'UNSUPPORTED_REGION', verified: true,
      phoneMasked: '+1••••••71', matchesInvitation: null,
      knownName: null, telegramName: 'Muxammad' };
    return { state: 'VERIFICATION_REQUIRED', verified: false, phoneMasked: null,
      matchesInvitation: null, knownName: null, telegramName: 'Muxammad' };
  })(),
  // The receipt-review screen a Telegram notification opens. `?review=done`
  // renders the already-processed state, which is the one that must NOT offer
  // an actionable button.
  'GET /tenant/payment-review': (() => {
    const processed = typeof location !== 'undefined'
      && new URLSearchParams(location.search).get('review') === 'done';
    const ru = typeof location !== 'undefined' && location.pathname.startsWith('/ru');
    return {
      id: 'pay-1', customer_name: 'Abdurahmonov Abdulaziz Abdurahmon oʻgʻli',
      contract_number: 'MDD-2026-000001', installment_id: 'i1',
      amount_formatted: '4 800 000', currency: 'UZS',
      method: 'cash',
      // The SERVER localizes these from the caller's `user.locale`. The fixture
      // mirrors that rather than pinning Uzbek, so the Russian preview shows
      // what a Russian shop owner actually receives.
      method_label: ru ? 'Наличные' : 'Naqd',
      status: processed ? 'approved' : 'pending',
      status_label: processed
        ? (ru ? 'Подтверждён' : 'Tasdiqlangan')
        : (ru ? 'Ожидает' : 'Kutilmoqda'),
      // (LX-02 harness) Null by default — unchanged. `?receipt=ok` renders a
      // decodable image so the reserved frame and the Approve/Reject rectangles
      // can be measured in a real browser; `?receipt=bad` points at a path that
      // 404s, which is exactly the state a PDF receipt produces inside an <img>.
      receipt_image_url: (() => {
        const r = typeof location !== 'undefined'
          && new URLSearchParams(location.search).get('receipt');
        if (r === 'ok') return 'data:image/gif;base64,R0lGODlhAQABAIAAAAAAAP///yH5BAEAAAAALAAAAAABAAEAAAIBRAA7';
        if (r === 'bad') return '/api/receipts/missing-on-purpose';
        return null;
      })(),
      submitted_at: '2026-09-01T09:23:00Z',
      submitted_at_label: ru ? '1 сентября 2026 г., 14:23' : '1-sentabr, 2026, 14:23',
      actionable: !processed,
    };
  })(),
  'GET /me/identity': (typeof location !== 'undefined'
    && new URLSearchParams(location.search).get('identity') === 'verified')
    ? { state: 'VERIFIED', verified: true, phoneMasked: '+998•••••••33',
        eligibility: 'ELIGIBLE', source: 'telegram_contact',
        verifiedAt: '2026-08-10T00:00:00Z', displayName: 'Jasur Toshmatov', linkedShops: 1 }
    : { state: 'VERIFICATION_REQUIRED', verified: false, phoneMasked: null,
        eligibility: null, source: null, verifiedAt: null,
        displayName: 'Jasur Toshmatov', linkedShops: 0 },
};

/** Contracts for the tenant list and the collections queue. */
const CONTRACT_NAMES = [
  'Aziz Karimov', 'Bekzod Tursunov', 'Dilnoza Rahimova', 'Sardor Alimov',
  'Nodira Yusupova', 'Jasur Toshmatov', 'Kamola Sobirova', 'Rustam Ergashev',
  'Malika Nazarova', 'Otabek Sharipov', 'Zuhra Qodirova', 'Sanjar Umarov',
  'Feruza Ibragimova', 'Bobur Xolmatov', 'Sevara Mirzayeva', 'Aziza Rasulova',
  'Timur Yoqubov', 'Gulnora Saidova', 'Doniyor Ochilov', 'Shahnoza Tashpulatova',
  'Ulugbek Norov', 'Madina Xasanova', 'Farrux Berdiyev', 'Nilufar Zokirova',
  'Akmal Jo‘rayev',
];
const CONTRACTS = CONTRACT_NAMES.map((name, i) => ({
  id: `c${i + 1}`, reference: `R${1000 + i}`, contract_number: `MDD-2026-${String(i + 1).padStart(4, '0')}`,
  customer_id: `cu${i + 1}`, customer_name: name, customer_phone: `+99890${String(1000000 + i * 7919).slice(0, 7)}`,
  status: i % 3 === 0 ? 'overdue' : 'active',
  is_overdue: i % 3 === 0,
  total_amount: 6_000_000 + i * 250_000, down_payment: i % 4 === 0 ? 1_000_000 : 0,
  principal: 6_000_000 + i * 250_000, markup_amount: 0,
  calc_formula: 'none', markup_pct: null, interest_rate: null,
  months: 6 + (i % 7), monthly_amount: 900_000 + i * 25_000,
  paid_count: i % 5, paid_amount: (i % 5) * 900_000,
  start_date: '2026-01-05', next_due_date: i % 3 === 0 ? '2026-03-01' : '2026-07-28',
  created_at: `2026-0${1 + (i % 6)}-1${i % 9}T09:00:00Z`,
}));

/** One contract in the shape `GET /tenant/installments/{id}` returns:
 *  the list row plus `guarantors`, `items` and `payments`. Without this the
 *  detail modal fell through to the empty-array default and could not render.
 *  Numbers stay numbers here (InstallmentDetail), unlike the customer contract
 *  above where money is serialized as strings. */
function contractDetail(id: string) {
  const base = CONTRACTS.find((c) => c.id === id) ?? CONTRACTS[0];
  const i = CONTRACTS.indexOf(base);
  return {
    ...base,
    id,
    guarantors: i % 2 === 0
      ? [{
        id: `g${i + 1}`, sequence: 1, full_name: 'Shuhrat Qodirov',
        phone: '+998901112233', relationship: 'aka',
      }]
      : [],
    items: [{
      id: `it${i + 1}`, product_id: `p${i + 1}`, catalog_model_id: null,
      product_name_snapshot: 'Samsung Galaxy A55', variant_label: '8/256 GB',
      serial_number: `SN${100000 + i}`, qty: 1,
      unit_price: base.total_amount, line_total: base.total_amount,
    }],
    // Consistent with paid_count/paid_amount on the row.
    payments: Array.from({ length: base.paid_count }, (_, n) => ({
      id: `pay${i + 1}-${n + 1}`,
      amount: base.monthly_amount,
      method: 'cash',
      status: 'approved',
      paid_at: `2026-0${(n % 6) + 1}-12T11:00:00Z`,
    })),
  };
}

/** Filter/paginate exactly as the server does, so search states are real. */
function contractPage(params: Record<string, unknown>, onlyDue: boolean) {
  // axios carries these in `config.params`, not in the URL string.
  const q = String(params.q ?? '').trim().toLowerCase();
  const limit = Number(params.limit ?? (onlyDue ? 20 : 50));
  const offset = Number(params.offset ?? 0);
  let rows = onlyDue ? CONTRACTS.filter((c) => c.next_due_date <= '2026-07-28') : CONTRACTS;
  if (q) {
    rows = rows.filter((c) => c.customer_name.toLowerCase().includes(q)
      || (c.contract_number ?? '').toLowerCase().includes(q)
      || (c.customer_phone ?? '').includes(q));
  }
  if (onlyDue) rows = [...rows].sort((a, b) => Number(b.is_overdue) - Number(a.is_overdue));
  const page = rows.slice(offset, offset + limit);
  const overdue = rows.filter((c) => c.is_overdue).length;
  return onlyDue
    ? {
      items: page, total: rows.length, overdue_count: overdue,
      due_today_count: rows.length - overdue,
      as_of: '2026-07-28T09:00:00Z', as_of_date: '2026-07-28', timezone: 'Asia/Tashkent',
    }
    : { items: page, total: rows.length };
}

function resolve(method: string, rawUrl: string, params: Record<string, unknown> = {}): unknown {
  const url = rawUrl.replace(/^\/api/, '').split('?')[0];
  const key = `${method.toUpperCase()} ${url}`;
  // Query-aware endpoints must be checked BEFORE the static table so `q`,
  // `limit` and `offset` actually take effect.
  if (method === 'get' && url === '/tenant/worklist') return contractPage(params, true);
  if (method === 'get' && url === '/tenant/installments') return contractPage(params, false);
  if (key in ROUTES) return ROUTES[key];
  // Path-param endpoints exercised by the tenant detail drawer + create flow.
  if (method === 'get' && /^\/platform\/tenants\/[^/]+\/detail$/.test(url)) return TENANT_DETAIL;
  if (method === 'get' && /^\/me\/installments\/[^/]+\/payment-methods$/.test(url)) return MY_PAYMENT_METHODS;
  if (method === 'get' && /^\/me\/installments\/[^/]+$/.test(url)) return MY_INSTALLMENT_DETAIL;
  if (method === 'get' && /^\/tenant\/installments\/[^/]+$/.test(url)) {
    return contractDetail(url.slice(url.lastIndexOf('/') + 1));
  }
  if (method === 'post' && /^\/platform\/tenants\/[^/]+\/owner-invite$/.test(url)) return { share_url: 'https://t.me/MuddatoBot?start=owner_abc123def456' };
  if (method === 'post' && url === '/platform/tenants') return { id: 't-new', share_url: 'https://t.me/MuddatoBot?start=owner_new789xyz' };
  // A freshly created customer hands back a join link. The URL is deliberately
  // long and unbreakable — that is what pushed the success sheet sideways.
  if (method === 'post' && url === '/tenant/customers') {
    return {
      id: 'cu-new', full_name: 'Abdurahmonov Shohruhmirzo Bahodirjon oʻgʻli',
      phone: '+998901234567', status: 'active',
      invite: { url: 'https://t.me/MuddatoBot?startapp=join_9f2b7c41d8e34a6fb05c1e77a2d93be6' },
    };
  }
  if (method === 'get' && /^\/tenant\/payments\/[^/]+\/review$/.test(url)) return ROUTES['GET /tenant/payment-review'];
  // (LX-07 harness) The tenant + staff payment queues, so their detail drawers —
  // and the receipt inside them — can be opened and measured in a browser. The
  // receipt URL follows the same `?receipt=` switch as the review screen, so the
  // default (no param) is an empty-receipt drawer, exactly as before.
  if (method === 'get' && url === '/tenant/payments') {
    const r = typeof location !== 'undefined'
      && new URLSearchParams(location.search).get('receipt');
    const receipt = r === 'ok'
      ? 'data:image/gif;base64,R0lGODlhAQABAIAAAAAAAP///yH5BAEAAAAALAAAAAABAAEAAAIBRAA7'
      : r === 'bad' ? '/api/receipts/missing-on-purpose' : null;
    return {
      items: [
        { id: 'pay-1', installment_id: 'i1', customer_id: 'cu1',
          customer_name: 'Abdurahmonov Abdulaziz', contract_number: 'MDD-2026-000001',
          amount: 4800000, currency: 'UZS', method: 'cash', status: 'pending',
          receipt_image_url: receipt, paid_at: null, created_at: '2026-09-01T09:23:00Z' },
      ],
      total: 1,
    };
  }
  if (method === 'post' && /^\/inv\/[^/]+\/identity$/.test(url)) return ROUTES['POST /inv/identity'];
  if (method === 'get' && /^\/inv\/[^/]+$/.test(url)) {
    return { kind: 'customer', tenant_name: 'Muddato Demo', tenant_slug: 'demo',
             tenant_logo: null, role: null, preset: {}, custom_fields: [] };
  }
  if (method === 'post' && url.startsWith('/auth/telegram')) return { token: 'dev-mock-token' };
  // Writes succeed as no-ops; reads fall back to a HYBRID empty default: an
  // array that ALSO carries .items/.total. So endpoints that return a bare array
  // (catalog categories/brands/models — `cats.data.find(...)`) AND those that
  // return `{ items, total }` (list pages) both get a valid empty shape and never
  // crash the host page in the dev mock. Detail-object endpoints read keys via
  // `?.`, so the extra keys are harmless.
  if (method === 'get') return Object.assign([] as unknown[], { items: [] as unknown[], total: 0 });
  return { ok: true };
}

export function installDevMock(): void {
  // Persona select via `?mock=tenant|staff|client` (default platform). Only read
  // at boot; navigate with the param to switch (full reload re-installs the role).
  let role = '';
  try { role = new URLSearchParams(window.location.search).get('mock') ?? ''; } catch { /* noop */ }
  ROUTES['GET /auth/me'] =
    role === 'tenant' ? TENANT_ME : role === 'staff' ? STAFF_ME
      : role === 'client' ? CLIENT_ME : role === 'blocked' ? BLOCKED_ME : ME;
  // Seed the auth breadcrumb so RequireAuth/useMe run and hit the mock /auth/me.
  setSessionToken('dev-mock-token');
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  api.defaults.adapter = async (config: any) => {
    const method = (config.method ?? 'get').toLowerCase();
    const data = resolve(method, config.url ?? '', config.params ?? {});
    return {
      data, status: 200, statusText: 'OK', headers: {}, config,
    };
  };
  // eslint-disable-next-line no-console
  console.info('[devMock] installed — operator console served from in-memory fixtures');
}
