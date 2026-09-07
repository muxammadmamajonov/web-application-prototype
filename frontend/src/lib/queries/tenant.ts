/** Tenant back-office reads: customers, contracts, money, settings and reports. */

import { keepPreviousData, useInfiniteQuery, useQuery } from '@tanstack/react-query';
import { downloadGranted } from '@/lib/download';
import { api } from '../api';
import { queryKeys } from '../queryKeys';
import { V2_SURFACES_ENABLED } from '@/routes/_scope';
import type { OverageRule } from './shared';

/** A money total per ISO currency code — `{ UZS: 84000000, USD: 4500 }`.
 *
 *  (MUDD-114) Muddato has no exchange-rate source, so a shop holding both UZS
 *  and USD contracts has no single "total". The server no longer invents one;
 *  it returns the map, and the scalar beside it is the PRIMARY currency's
 *  share — the unit the screen already labels it with. */
export type MoneyByCurrency = Record<string, number>;

export interface TenantOverview {
  tenant: { id: string; name: string; status: string };
  /** The currency the scalar `*_uzs` fields below are denominated in. The
   *  legacy names are kept so nothing breaks; this is what they MEAN. */
  primary_currency?: string;
  debt_total_uzs: number;
  overdue_uzs: number;
  due_today_uzs: number;
  debt_total_by_currency?: MoneyByCurrency;
  overdue_by_currency?: MoneyByCurrency;
  due_today_by_currency?: MoneyByCurrency;
  pending_receipts: number;
  installments_by_status: Record<string, number>;
  /** (MUDD-83) What the shop actually sells, biggest by value first.
   *
   *  A LIST, not a slug-keyed object: the order IS the answer, and JSON object
   *  key order is not something a client should rely on. Optional so a stale
   *  cached response from before this shipped still renders. */
  installments_by_category?: {
    category: string; count: number; amount_uzs: number;
    amount_by_currency?: MoneyByCurrency;
    /** (MUDD-141) `units_sold` = pieces sold (`sum(InstallmentItem.qty)`);
     *  `contract_count` is `count` under its real name. Both optional: the
     *  overview response sits behind a 30s read-through cache with no version
     *  segment, so a request right after deploy can still get the old shape
     *  back with neither key present. */
    units_sold?: number;
    contract_count?: number;
  }[];
}

export function useTenantOverview() {
  return useQuery({
    queryKey: queryKeys.tenant.overview(),
    queryFn: async ({ signal }) => (await api.get<TenantOverview>('/tenant/overview', { signal })).data,
    staleTime: 30_000,
  });
}

export interface CustomerRow {
  id: string;
  full_name: string;
  phone: string;
  secondary_phone: string | null;
  passport_series: string | null;
  passport_number: string | null;
  passport_id: string | null;
  jshshir: string | null;
  birthdate: string | null;
  address: string | null;
  // 'pending' = self-onboarded via invitation and waiting for owner
  // moderation (only when tenant has auto_approve_customers=false).
  status: 'pending' | 'active' | 'blocked';
  telegram_id: number | null;
  /** Whether this person is reachable in Telegram, in three states rather than
   *  two: `joined` (linked to this shop), `known` (already uses the bot from
   *  elsewhere — invitations reach them automatically) or `new` (nobody on the
   *  platform has this number). Optional so an older cached response still
   *  renders; TelegramStatusBadge falls back to `new`. */
  telegram_status?: 'joined' | 'known' | 'new';
  notes: string | null;
  employment_place: string | null;
  monthly_income: number | null;
  mehnat_daftarchasi_photo_urls: string[];
  created_at: string;
}

export function useTenantCustomers(params?: { q?: string; status_filter?: string; limit?: number }) {
  return useQuery({
    queryKey: queryKeys.tenant.customers(params),
    placeholderData: keepPreviousData,  // (#621) keep rows during search/filter refetch
    queryFn: async ({ signal }) => (await api.get<{ items: CustomerRow[]; total: number }>('/tenant/customers', { params, signal })).data,
    staleTime: 15_000,
  });
}

// ── guarantors (kafil) ───────────────────────────────────────────────────────

interface GuarantorRow {
  id: string;
  full_name: string;
  phone: string;
  passport_series: string | null;
  passport_number: string | null;
  jshshir: string | null;
  relationship: string | null;
  telegram_id: number | null;
  address: string | null;
  photo_urls: string[];
  created_at: string;
}

export function useTenantGuarantors(params?: { q?: string }) {
  return useQuery({
    queryKey: queryKeys.tenant.guarantors(params),
    placeholderData: keepPreviousData,  // (#621)
    queryFn: async ({ signal }) => (await api.get<{ items: GuarantorRow[]; total: number }>('/tenant/guarantors', { params, signal })).data,
    staleTime: 15_000,
  });
}

export interface ImeiCheckResult {
  serial: string;
  in_tenant: boolean;
  in_tenant_count: number;
  cross_tenant_count: number;
}

/** Imperative IMEI/serial duplicate lookup (used by the installment wizard). */
export async function checkImei(serial: string): Promise<ImeiCheckResult> {
  return (await api.get<ImeiCheckResult>('/tenant/imei-check', { params: { serial } })).data;
}

// ── cross-tenant risk (Round 23) ──────────────────────────────────────────

export type RiskTier = 'low' | 'medium' | 'high';

export interface RiskScore {
  tier: RiskTier;
  has_jshshir: boolean;
  network_opted_in: boolean;
  internal: {
    installments_total: number;
    completed: number;
    active_overdue: number;
    approved_payments: number;
    late_fee_count: number;
    on_time_rate: number | null;
  };
  external: {
    signal_count: number;
    by_severity: Record<string, number>;
    worst_severity: string | null;
    distinct_shops: number;
  };
}

/** Risk profile for one of this tenant's customers. */
async function fetchRiskScore(customerId: string, signal?: AbortSignal): Promise<RiskScore> {
  return (await api.get<RiskScore>('/tenant/risk/score', { params: { customer_id: customerId }, signal })).data;
}

export function useRiskScore(customerId: string | null | undefined) {
  return useQuery({
    queryKey: queryKeys.tenant.riskScore(customerId),
    queryFn: ({ signal }) => fetchRiskScore(customerId as string, signal),
    enabled: !!customerId,
    staleTime: 30_000,
  });
}

export interface ProductRow {
  id: string;
  sku: string | null;
  name_uz: string;
  name_ru: string | null;
  category: string | null;
  brand: string | null;
  catalog_model_id: string | null;
  base_price: number;
  cost_price: number | null;
  stock: number | null;
  min_stock_level: number | null;
  attributes: Record<string, unknown>;
  photo_urls: string[];
  is_active: boolean;
  created_at: string;
}

export function useTenantProducts(params?: { q?: string }) {
  return useQuery({
    queryKey: queryKeys.tenant.products(params),
    placeholderData: keepPreviousData,  // (#621)
    queryFn: async ({ signal }) => (await api.get<{ items: ProductRow[]; total: number }>('/tenant/products', { params, signal })).data,
    staleTime: 15_000,
  });
}

export interface InstallmentRow {
  id: string;
  reference: string | null;
  contract_number: string | null;
  customer_id: string;
  customer_name: string | null;
  customer_phone: string | null;
  status: 'draft' | 'active' | 'overdue' | 'completed' | 'cancelled';
  /** The currency this contract was WRITTEN in — snapshot at creation
   *  and immutable. Every money label on a contract-scoped surface reads this,
   *  NOT `useCurrency()`: the shop changing its setting must not relabel a debt
   *  that was already agreed. Optional so a stale cached response still renders. */
  currency?: string;
  total_amount: number;
  down_payment: number;
  principal: number;
  months: number;
  monthly_amount: number;
  paid_count: number;
  paid_amount: number;
  start_date: string | null;
  next_due_date: string | null;
  // Financing model the contract was written with. The backend already emits
  // these (see _installment_dict); surfacing them lets the edit modal rehydrate
  // the real terms instead of silently reverting to the tenant defaults.
  calc_formula: 'none' | 'markup' | 'annuity' | 'differentiated' | null;
  markup_pct: number | null;
  interest_rate: number | null;       // stored as a fraction (0.03 = 3%/mo)
}

export interface InstallmentDetail extends InstallmentRow {
  guarantors: { id: string; sequence: number; full_name: string; phone: string; relationship: string | null }[];
  items: {
    id: string; product_id: string | null; catalog_model_id: string | null; product_name_snapshot: string;
    category: string | null; variant_label: string | null; serial_number: string | null;
    /** (MUDD-85/98) Category-schema-declared fields (IMEI 1/2, VIN, …), keyed
     *  by `catalog_categories.identifier_schema[].key`. Always present as an
     *  object (possibly empty) — optional here only for older mocked fixtures. */
    identifiers?: Record<string, string>;
    qty: number; unit_price: number; line_total: number;
  }[];
  payments: { id: string; amount: number; currency?: string; method: string; status: string; paid_at: string | null }[];
}
export function useTenantInstallmentDetail(id: string | null) {
  return useQuery({
    queryKey: queryKeys.tenant.installment(id),
    enabled: !!id,
    queryFn: async ({ signal }) => (await api.get<InstallmentDetail>(`/tenant/installments/${id}`, { signal })).data,
    staleTime: 10_000,
  });
}

/** `enabled` lets a screen that is showing the collections worklist instead keep
 *  this request off the wire rather than fetching a list it does not render. */
export function useTenantInstallments(
  params?: { q?: string; status_filter?: string; category?: string },
  enabled = true,
) {
  return useQuery({
    queryKey: queryKeys.tenant.installments(params ?? null),
    placeholderData: keepPreviousData,  // (#621) keep rows during status/search filter refetch
    // `category_counts` is tenant-wide and INDEPENDENT of the params above, so
    // a chip keeps showing how many rows it would reveal while a different
    // filter is on — the moment a user most needs to know where else to look.
    queryFn: async ({ signal }) => (await api.get<{ items: InstallmentRow[]; total: number; category_counts?: Record<string, number> }>('/tenant/installments', { params, signal })).data,
    staleTime: 15_000,
    enabled,
  });
}

export interface PaymentRow {
  id: string;
  installment_id: string;
  customer_id: string;
  customer_name: string | null;
  /** Names the contract the money lands on, so an approval confirmation can
   *  restate it — a customer may hold several. */
  contract_number: string | null;
  amount: number;
  /** Equal to the parent contract's currency — held so by a database
   *  foreign key, not a convention. Read this, never the shop's setting. */
  currency?: string;
  method: 'cash' | 'card' | 'click' | 'payme' | 'transfer';
  status: 'pending' | 'approved' | 'rejected' | 'refunded';
  receipt_image_url: string | null;
  paid_at: string | null;
  created_at: string;
}

/**
 * The collections worklist — contracts due today or overdue — computed by the
 * server over the WHOLE tenant. Never re-derive it from a page of
 * `/tenant/installments`: that describes one page as the whole book. `total` is
 * the real count and `as_of` the moment it was computed, so a screen can say
 * exactly what it is showing.
 */
interface WorklistRow extends InstallmentRow {
  /** Server-derived: true when the due date has passed, whatever `status` says
   *  (the overdue cron is nightly, so the column lags reality by up to a day). */
  is_overdue: boolean;
}

interface WorklistResponse {
  items: WorklistRow[];
  total: number;
  overdue_count: number;
  due_today_count: number;
  as_of: string;
  /** The tenant-local date the classification used. */
  as_of_date: string;
  /** The timezone that date was computed in. */
  timezone: string;
}

export function useTenantWorklist(limit = 20) {
  return useQuery({
    queryKey: queryKeys.tenant.worklist(limit),
    queryFn: async ({ signal }) => (await api.get<WorklistResponse>('/tenant/worklist', { params: { limit }, signal })).data,
    staleTime: 30_000,
  });
}

/**
 * The worklist, page by page.
 *
 * `useTenantWorklist` fetches one bounded page — right for the Overview, which
 * is an explicit preview. Any screen whose heading counts the true total must
 * use THIS instead, or rows past the first page are unreachable while the count
 * insists they exist.
 *
 * Server ordering ends in a stable key (`id`), so pages are a true partition:
 * appending cannot duplicate or skip a contract.
 *
 * `q` searches the WHOLE due/overdue set server-side and is part of the query
 * key, so changing it refetches from offset 0 rather than filtering the pages
 * already in hand — which would search a fraction of the queue and present it
 * as the whole answer. `enabled` keeps the request off screens that are not
 * showing the collections view.
 */
export function useTenantWorklistPages(pageSize = 20, q = '', enabled = true) {
  const search = q.trim();
  return useInfiniteQuery({
    queryKey: queryKeys.tenant.worklistPaged(pageSize, search),
    initialPageParam: 0,
    queryFn: async ({ pageParam, signal }) =>
      (await api.get<WorklistResponse>('/tenant/worklist', {
        params: { limit: pageSize, offset: pageParam, ...(search ? { q: search } : {}) },
        signal,
      })).data,
    getNextPageParam: (last, all) => {
      const loaded = all.reduce((n, p) => n + p.items.length, 0);
      return loaded < last.total ? loaded : undefined;
    },
    staleTime: 30_000,
    enabled,
  });
}

/**
 * The true tenant-wide count of payments awaiting approval.
 * Asks for a single row and reads the server's filtered `total`, so the figure
 * is right without pulling the queue itself.
 */
export function usePendingPaymentCount() {
  return useQuery({
    queryKey: queryKeys.tenant.paymentsPendingCount(),
    queryFn: async ({ signal }) => (await api.get<{ items: PaymentRow[]; total: number }>(
      '/tenant/payments', { params: { status_filter: 'pending', limit: 1 }, signal },
    )).data.total,
    staleTime: 30_000,
  });
}

/**
 * H17: called with NO params at all, so the fetch rode the backend's own
 * default (`limit: LimitQ = 50`, `tenant/payments.py:87`) — every payment
 * past the 50th was invisible, not merely slow to load, since the DataTable
 * below paginates client-side over whatever this hook returned. Requesting
 * the backend's hard cap (`le=200`) is the largest single bounded fetch this
 * endpoint allows. True next-page fetching — mirroring `useTenantWorklistPages`
 * above, which already does real server pagination with a stable `id`
 * tiebreaker — needs the route to drive `offset` from DataTable's page state,
 * which is a route-level change tracked separately from this fix.
 */
const TENANT_PAYMENTS_LIMIT = 200;

export function useTenantPayments() {
  return useQuery({
    queryKey: queryKeys.tenant.payments(),
    queryFn: async ({ signal }) => (await api.get<{ items: PaymentRow[]; total: number }>(
      '/tenant/payments', { params: { limit: TENANT_PAYMENTS_LIMIT }, signal },
    )).data,
    staleTime: 15_000,
  });
}

/** Per-status totals for the whole tenant — independent of which status page
 *  is currently loaded, so a "Rad etilgan (N)" tab shows the real figure even
 *  while a different status is on screen. */
export type PaymentStatusCounts = Record<'pending' | 'approved' | 'rejected' | 'refunded', number>;

interface PaymentsPageResponse {
  items: PaymentRow[];
  total: number;
  counts: PaymentStatusCounts;
}

/**
 * (MUDD-84) The payments list, page by page, filtered by status ON THE
 * SERVER — the true fix for the bug `useTenantPayments` above cannot fix on
 * its own. That hook fetches the newest `TENANT_PAYMENTS_LIMIT` payments
 * UNFILTERED and lets the page filter by status client-side; on a tenant with
 * more than that many payments, any rejected/approved/refunded row older
 * than the cutoff was invisible under every status view, not merely slow to
 * load.
 *
 * Mirrors `useTenantWorklistPages`'s pattern exactly: `statusFilter` is part
 * of the query key, so switching tabs refetches from offset 0 instead of
 * filtering pages fetched for a different tab; the backend orders by
 * `created_at` with an `id` tiebreaker, so pages are a true partition and
 * `getNextPageParam` can safely resume from the running item count.
 */
export function useTenantPaymentsPage(pageSize: number, statusFilter: string) {
  return useInfiniteQuery({
    queryKey: queryKeys.tenant.paymentsPaged(pageSize, statusFilter),
    initialPageParam: 0,
    queryFn: async ({ pageParam, signal }) =>
      (await api.get<PaymentsPageResponse>('/tenant/payments', {
        params: { limit: pageSize, offset: pageParam, ...(statusFilter ? { status_filter: statusFilter } : {}) },
        signal,
      })).data,
    getNextPageParam: (last, all) => {
      const loaded = all.reduce((n, p) => n + p.items.length, 0);
      return loaded < last.total ? loaded : undefined;
    },
    staleTime: 15_000,
  });
}

export interface BroadcastRow {
  id: string;
  title: string;
  body_uz: string;
  body_ru: string | null;
  media_url: string | null;
  button_label: string | null;
  button_url: string | null;
  target: 'all' | 'active' | 'overdue' | 'custom';
  status: 'draft' | 'scheduled' | 'sending' | 'sent' | 'failed';
  sent_count: number;
  failed_count: number;
  created_at: string | null;
  finished_at: string | null;
}

export function useTenantBroadcasts() {
  return useQuery({
    queryKey: queryKeys.tenant.broadcasts(),
    queryFn: async ({ signal }) => (await api.get<{ items: BroadcastRow[]; total: number }>('/tenant/broadcasts', { signal })).data,
    staleTime: 15_000,
  });
}

// ── tenant settings + staff ───────────────────────────────────────────

export interface CustomFieldDef {
  key:      string;
  label:    string;
  type:     'text' | 'phone' | 'date' | 'file' | 'select' | 'number' | 'imei';
  required: boolean;
  options?:    string[];     // for `select`
  max_length?: number;       // cap input length (e.g. IMEI = 14)
  integer?:    boolean;      // digits only
  order?:      number;
}

export interface TenantSettingsBundle {
  profile: {
    business_name: string | null;
    logo_url: string | null;
    address: string | null;
    contact_phone: string | null;
    contact_email: string | null;
    contact_telegram: string | null;
    business_hours: { start?: string; end?: string };
    currency: string;
    default_locale: string;
    receipt_footer: string | null;
    business_type: string | null;
    business_type_other: string | null;
  };
  reminders: {
    reminder_target: 'owner' | 'channel' | 'both';
    reminder_channel_id: string | null;
    reminder_overdue_every_days: number;
    reminder_offsets_days: number[];
    reminder_templates: Record<string, string>;
    notification_prefs: Record<string, unknown>;
  };
  calc: {
    default_calc_formula: 'none' | 'annuity' | 'differentiated' | 'markup';
    default_markup_pct: number;
    default_max_months: number;
    default_late_fee_pct_per_day: number;
    default_min_down_pct: number;
    early_payoff_discount_pct: number;
  };
  payments: {
    payment_methods: {
      cash?: boolean; card?: boolean; click?: boolean; payme?: boolean; transfer?: boolean;
      // Receiving cards customers transfer to (R36). Stored in the same JSON blob.
      cards?: import('@/components/ui/CardManager').PayoutCard[];
    };
    click_merchant_id: string | null;
    click_service_id: string | null;
    click_secret_key: string | null;
    payme_merchant_id: string | null;
    payme_secret_key: string | null;
    card_payment_instructions: string | null;
  };
  bot?: {
    bot_mode: 'shared' | 'tenant';
    bot_username: string | null;
    bot_activated_at: string | null;
    plan_allows_bot: boolean;
  };
  customer_form?: {
    fields: CustomFieldDef[];
    auto_approve: boolean;
  };
  product_form?: {
    fields: CustomFieldDef[];
  };
  risk?: {
    risk_network_opt_in: boolean;
  };
  sms?: {
    sms_budget_uzs_per_month: number;
    sms_sender_alpha: string | null;
  };
}

export interface InvitationRow {
  id: string;
  token: string;
  kind: 'customer' | 'staff' | 'guarantor';
  role: string | null;
  web_url: string;
  telegram_url: string;
  expires_at: string;
  used_at: string | null;
  created_at: string;
}

export function useTenantInvitations(params?: { kind?: 'customer' | 'staff' | 'guarantor'; only_open?: boolean }) {
  return useQuery({
    queryKey: queryKeys.tenant.invitations(params),
    placeholderData: keepPreviousData,  // (#621)
    queryFn: async ({ signal }) => (await api.get<InvitationRow[]>('/tenant/invitations', { params, signal })).data,
    staleTime: 30_000,
  });
}

export function useTenantSettings() {
  return useQuery({
    queryKey: queryKeys.tenant.settings(),
    queryFn: async ({ signal }) => (await api.get<TenantSettingsBundle>('/tenant/settings', { signal })).data,
    staleTime: 30_000,
  });
}

// ── SMS fallback (Round 25) ───────────────────────────────────────────────
interface SmsUsage {
  configured: boolean;
  budget_uzs_per_month: number;
  spent_this_month: number;
  remaining: number;
  sender: string | null;
  sent_count: number;
  failed_count: number;
}

interface SmsHistoryRow {
  id: string;
  customer_id: string | null;
  phone: string;
  text: string;
  parts: number;
  cost_uzs: number;
  status: 'pending' | 'sent' | 'failed';
  kind: string;
  error: string | null;
  created_at: string | null;
}

export type ChannelType = 'telegram' | 'sms' | 'email';

export interface CustomerChannel {
  channel: ChannelType;
  is_primary: boolean;
  opted_in: boolean;
  opted_in_at: string | null;
}

export function useSmsUsage() {
  return useQuery({
    queryKey: queryKeys.tenant.smsUsage(),
    queryFn: async ({ signal }) => (await api.get<SmsUsage>('/tenant/sms/usage', { signal })).data,
    staleTime: 30_000,
  });
}

export function useSmsHistory(limit = 50) {
  return useQuery({
    queryKey: queryKeys.tenant.smsHistory(limit),
    queryFn: async ({ signal }) => (await api.get<SmsHistoryRow[]>('/tenant/sms/history', { params: { limit }, signal })).data,
    staleTime: 30_000,
  });
}

export function useCustomerChannels(customerId: string | undefined) {
  return useQuery({
    queryKey: queryKeys.tenant.customerChannels(customerId),
    queryFn: async ({ signal }) =>
      (await api.get<CustomerChannel[]>(`/tenant/customers/${customerId}/channels`, { signal })).data,
    enabled: !!customerId,
    staleTime: 30_000,
  });
}

export interface StaffRow {
  id: string;
  telegram_id: number;
  telegram_username: string | null;
  first_name: string | null;
  last_name: string | null;
  role: 'owner' | 'manager' | 'sales';
  is_active: boolean;
  last_seen_at: string | null;
  invitation_status: string;
}

export function useTenantStaff() {
  return useQuery({
    queryKey: queryKeys.tenant.staff(),
    queryFn: async ({ signal }) => (await api.get<StaffRow[]>('/tenant/staff', { signal })).data,
    staleTime: 30_000,
  });
}

// ── Reports (Round 24) ───────────────────────────────────────────────────────

interface AgingBucket { count: number; outstanding: number; by_currency?: MoneyByCurrency }
interface AgingReport {
  as_of: string;
  currency?: string;
  buckets: { b0_30: AgingBucket; b31_60: AgingBucket; b61_90: AgingBucket; b90_plus: AgingBucket };
  total_outstanding: number;
  total_outstanding_by_currency?: MoneyByCurrency;
  total_count: number;
}
interface CashflowReport {
  from: string; to: string; granularity: string;
  currency?: string;
  series: { period: string; count: number; amount: number; by_currency?: MoneyByCurrency }[];
  total: number;
  total_by_currency?: MoneyByCurrency;
}
/** (MUDD-121) `volume`/`commission` are the PRIMARY currency's share only
 *  (MUDD-114) — a seller whose sales are entirely in another currency shows
 *  real activity next to a zero payout unless the `*_by_currency` maps beside
 *  them are read too. */
interface StaffPerfRow {
  user_id: string | null; name: string; sales_count: number;
  volume: number; commission_rate_pct: number; commission: number;
  volume_by_currency?: MoneyByCurrency; commission_by_currency?: MoneyByCurrency;
}
interface StaffReport {
  currency?: string;
  default_rate_pct: number; staff: StaffPerfRow[];
  total_volume: number; total_commission: number;
  total_volume_by_currency?: MoneyByCurrency; total_commission_by_currency?: MoneyByCurrency;
}
interface ProductsReport {
  currency?: string;
  rows: { brand: string; category: string; qty: number; revenue: number;
    revenue_by_currency?: MoneyByCurrency }[];
  total_revenue: number; total_revenue_by_currency?: MoneyByCurrency; total_qty: number;
}
interface LtvReport {
  limit: number;
  currency?: string;
  customers: { customer_id: string; name: string; phone: string | null;
    contracts: number; lifetime_value: number; paid: number;
    lifetime_value_by_currency?: MoneyByCurrency; paid_by_currency?: MoneyByCurrency }[];
}
interface MarginReport {
  currency?: string;
  revenue: number; cost_known: number; gross_profit: number;
  margin_pct: number; qty_with_cost: number; qty_total: number;
  revenue_by_currency?: MoneyByCurrency; cost_known_by_currency?: MoneyByCurrency;
  gross_profit_by_currency?: MoneyByCurrency; margin_pct_by_currency?: MoneyByCurrency;
}

export type Granularity = 'day' | 'week' | 'month';

export function useReportAging() {
  return useQuery({
    queryKey: queryKeys.tenant.reportsAging(),
    queryFn: async ({ signal }) => (await api.get<AgingReport>('/tenant/reports/aging', { signal })).data,
    staleTime: 60_000,
  });
}
/**
 * `enabled` defaults to `true` and is stripped before it reaches the query
 * key — for a caller that can conditionally skip the fetch (e.g. while its
 * own inputs are not yet ready) rather than surface a doomed request as an
 * error card.
 */
export function useReportCashflow(p: { from?: string; to?: string; granularity?: Granularity; enabled?: boolean }) {
  const { enabled = true, ...params } = p;
  return useQuery({
    queryKey: queryKeys.tenant.reportsCashflow(params),
    queryFn: async ({ signal }) => (await api.get<CashflowReport>('/tenant/reports/cashflow', {
      params: { from: params.from, to: params.to, granularity: params.granularity },
      signal,
    })).data,
    staleTime: 60_000,
    enabled,
  });
}
  // Deferred report — its card is not rendered in the MVP,
  // so the request is not issued either. Retained for V2.
export function useReportStaff() {
  return useQuery({
    queryKey: queryKeys.tenant.reportsStaff(),
    queryFn: async ({ signal }) => (await api.get<StaffReport>('/tenant/reports/staff-performance', { signal })).data,
    staleTime: 60_000,
    enabled: V2_SURFACES_ENABLED,
  });
}
  // Deferred report — its card is not rendered in the MVP,
  // so the request is not issued either. Retained for V2.
export function useReportProducts() {
  return useQuery({
    queryKey: queryKeys.tenant.reportsProducts(),
    queryFn: async ({ signal }) => (await api.get<ProductsReport>('/tenant/reports/products', { signal })).data,
    staleTime: 60_000,
    enabled: V2_SURFACES_ENABLED,
  });
}
  // Deferred report — its card is not rendered in the MVP,
  // so the request is not issued either. Retained for V2.
export function useReportLtv(limit = 20) {
  return useQuery({
    queryKey: queryKeys.tenant.reportsLtv(limit),
    queryFn: async ({ signal }) => (await api.get<LtvReport>('/tenant/reports/ltv', { params: { limit }, signal })).data,
    staleTime: 60_000,
    enabled: V2_SURFACES_ENABLED,
  });
}
  // Deferred report — its card is not rendered in the MVP,
  // so the request is not issued either. Retained for V2.
export function useReportMargin() {
  return useQuery({
    queryKey: queryKeys.tenant.reportsMargin(),
    queryFn: async ({ signal }) => (await api.get<MarginReport>('/tenant/reports/margin', { signal })).data,
    staleTime: 60_000,
    enabled: V2_SURFACES_ENABLED,
  });
}

/** Download a report export (csv|xlsx|pdf) via the authed axios client and
 *  trigger a browser save. The HttpOnly session cookie is sent via
 *  withCredentials over the same-origin proxied /api. */
export async function downloadReport(
  path: string, format: 'csv' | 'xlsx' | 'pdf', params?: Record<string, unknown>,
): Promise<void> {
  const res = await api.get(path, { params: { ...params, format }, responseType: 'blob' });
  const cd = String(res.headers['content-disposition'] ?? '');
  const m = cd.match(/filename="([^"]+)"/);
  const name = m?.[1] ?? `report.${format}`;
  const url = URL.createObjectURL(res.data as Blob);
  const a = document.createElement('a');
  a.href = url; a.download = name;
  document.body.appendChild(a); a.click();
  a.remove(); URL.revokeObjectURL(url);
}

/** (#206) Deliver a report export to the tenant owner's Telegram via the bot
 *  instead of a browser download. Returns how many owner chats it reached. */
export async function sendReportToTelegram(
  path: string, format: 'csv' | 'xlsx' | 'pdf', params?: Record<string, unknown>,
): Promise<number> {
  const res = await api.get<{ sent: number; filename: string }>(
    path, { params: { ...params, format, deliver: 'telegram' } },
  );
  return res.data.sent;
}

// ── billing (Round 27) ───────────────────────────────────────────────────────

interface InvoiceRow {
  id: string;
  plan_code: string;
  period_start: string;
  period_end: string;
  active_installments_count: number | null;
  unit_price: string | null;
  amount: string;
  status: 'open' | 'paid' | 'overdue' | 'void';
  line_items: { lines?: Array<{ label: string; qty: number; unit_price: string; total: string }> } | null;
  issued_at: string | null;
  due_at: string | null;
  paid_at: string | null;
  payment_method: string | null;
}

interface UpgradeSuggestion {
  to_plan: string;
  to_plan_name: string;
  current_projected: string;
  plan_price: string;
  message: string;
}

interface TenantBilling {
  plan_code: string;
  status: string;
  trial_ends_at: string | null;
  active_installments: number;
  limits: Record<string, number | null>;
  overage: Record<string, OverageRule>;
  base_price: number;
  projected: {
    amount: string;
    active_count: number;
    included_contracts: number | null;
    contract_overage_price: number | null;
    line_items: InvoiceRow['line_items'];
  } | null;
  suggestion: UpgradeSuggestion | null;
  last_invoice: InvoiceRow | null;
}

export function useTenantBilling() {
  return useQuery({
    queryKey: queryKeys.tenant.billing(),
    queryFn: async ({ signal }) => (await api.get<TenantBilling>('/tenant/billing', { signal })).data,
  });
}

export function useTenantInvoices() {
  return useQuery({
    queryKey: queryKeys.tenant.invoices(),
    queryFn: async ({ signal }) => (await api.get<InvoiceRow[]>('/tenant/invoices', { signal })).data,
  });
}

export async function changeTenantPlan(planCode: string) {
  return (await api.post<{ plan_code: string; status: string }>(
    '/tenant/plan-change', { plan_code: planCode },
  )).data;
}

// ── balance top-up / online checkout (R37) ───────────────────────────────────

interface TopupInfo {
  balance: number;
  /** The PLATFORM wallet's currency, stated by the server. Not the
   *  shop's — those are different pots of money and were being labelled alike. */
  currency: string;
  terms: { uz: string; ru: string };
  /** Already filtered by the server to cards that can settle `currency`. */
  cards: import('@/components/ui/CardManager').PayoutCard[];
  /** True when cards ARE configured but none can take this currency —
   *  a different message from "no cards yet", and a different person's problem. */
  cards_withheld?: boolean;
  providers: { payme: boolean; click: boolean };
  /** Server-decided: may THIS member top up? See the endpoint's comment. */
  can_topup: boolean;
}
export function useTenantTopupInfo() {
  return useQuery({
    queryKey: queryKeys.tenant.topupInfo(),
    queryFn: async ({ signal }) => (await api.get<TopupInfo>('/tenant/billing/topup-info', { signal })).data,
    staleTime: 30_000,
  });
}

interface TopupRow {
  id: string; amount: number; method: 'card' | 'payme' | 'click';
  status: 'pending' | 'paid' | 'rejected'; created_at: string;
  dest_card_label?: string | null;
}
export function useTenantTopups() {
  return useQuery({
    queryKey: queryKeys.tenant.topups(),
    queryFn: async ({ signal }) => (await api.get<TopupRow[]>('/tenant/billing/topups', { signal })).data,
    staleTime: 15_000,
    // Status changes externally (an operator approves or rejects), so a cached
    // row goes wrong without the client ever knowing — these override the
    // client-wide `false`. `true`, never `'always'`: staleTime bounds the
    // mount/focus refetch to at most one request per 15s.
    refetchOnMount: true,
    refetchOnWindowFocus: true,
  });
}

export async function createTopup(
  args: { amount: number; method: 'card' | 'payme' | 'click'; receipt?: File; card_id?: string },
): Promise<{ id: string; status: string; checkout_url: string | null }> {
  const fd = new FormData();
  fd.append('amount', String(args.amount));
  fd.append('method', args.method);
  if (args.receipt) fd.append('receipt', args.receipt);
  if (args.card_id) fd.append('card_id', args.card_id);
  return (await api.post('/tenant/billing/topup', fd)).data;
}

export async function downloadInvoicePdf(invoiceId: string): Promise<void> {
  // (#10) Signed URL first so this works inside Telegram; the blob + anchor
  // dance survives only as the desktop fallback inside the shared helper.
  await downloadGranted('invoice', invoiceId, {
    path: `/tenant/invoices/${invoiceId}/invoice.pdf`,
    filename: `invoice-${invoiceId}.pdf`,
  });
}

// ── inventory / stock movements (Round 28) ──────────────────────────────────

export interface MovementRow {
  id: string;
  product_id: string;
  qty: number;
  kind: 'in' | 'out' | 'sale' | 'return' | 'writeoff' | 'inventory';
  reason: string | null;
  by_user_id: string | null;
  related_installment_id: string | null;
  created_at: string | null;
}

export function useProductMovements(productId: string | null | undefined) {
  return useQuery({
    queryKey: queryKeys.tenant.productMovements(productId),
    queryFn: async ({ signal }) =>
      (await api.get<MovementRow[]>(`/tenant/products/${productId}/movements`, { signal })).data,
    enabled: !!productId,
  });
}

interface LowStockRow {
  id: string;
  name_uz: string;
  sku: string | null;
  stock: number | null;
  min_stock_level: number | null;
}

export function useLowStock() {
  return useQuery({
    queryKey: queryKeys.tenant.lowStock(),
    queryFn: async ({ signal }) => (await api.get<LowStockRow[]>('/tenant/inventory/low-stock', { signal })).data,
    staleTime: 30_000,
  });
}

export interface StockAdjustBody {
  qty: number;
  kind: 'in' | 'out' | 'return' | 'writeoff' | 'inventory';
  reason?: string | null;
}

export async function adjustStock(productId: string, body: StockAdjustBody) {
  return (await api.post<{ movement: MovementRow; stock: number | null }>(
    `/tenant/products/${productId}/stock-adjust`, body,
  )).data;
}

// ── Excel / CSV import wizard (Round 28) ────────────────────────────────────

export interface ImportPreview {
  columns: string[];
  sample: Record<string, string>[];
  total_rows: number;
}

export interface ImportResult {
  /** false → nothing was imported (the file had errors); fix and retry. */
  ok?: boolean;
  total?: number;
  created: number;
  skipped: number;
  errors: { row: number; error: string; fields?: string[] }[];
}

export async function importPreview(file: File): Promise<ImportPreview> {
  const fd = new FormData();
  fd.append('file', file);
  return (await api.post<ImportPreview>('/tenant/import/preview', fd)).data;
}

export async function importCommit(
  target: 'products' | 'customers',
  file: File,
  mapping: Record<string, string>,
): Promise<ImportResult> {
  const fd = new FormData();
  fd.append('file', file);
  fd.append('mapping', JSON.stringify(mapping));
  return (await api.post<ImportResult>(`/tenant/${target}/import`, fd)).data;
}
