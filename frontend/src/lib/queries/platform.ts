/** Platform (HQ) console reads: tenants, plans, money oversight, admin surfaces. */

import { keepPreviousData, useQuery } from '@tanstack/react-query';
import { api } from '../api';
import { queryKeys } from '../queryKeys';
import type { OverageRule } from './shared';

export function usePlatformReferralLink() {
  return useQuery({
    queryKey: queryKeys.platform.referralLink(),
    queryFn: async ({ signal }) => (await api.get<{ share_url: string }>('/platform/referrals/my-link', { signal })).data,
    staleTime: 5 * 60_000,
  });
}

interface PlatformOverview {
  tenants_total: number;
  tenants_by_status: { active: number; demo: number; suspended: number; blocked: number };
  active_installments: number;
  pending_receipts: number;
  pending_tenant_count: number;
  mrr_estimate_uzs: number;
  approved_this_month_uzs: number;
  customers_total: number;
  revenue_series: { year: number; month: number; amount: number }[];
}

export function usePlatformOverview() {
  return useQuery({
    queryKey: queryKeys.platform.overview(),
    queryFn: async ({ signal }) => (await api.get<PlatformOverview>('/platform/overview', { signal })).data,
    staleTime: 30_000,
  });
}

export interface PlatformPaymentRow {
  id: string;
  tenant_id: string;
  tenant_name: string;
  amount: number;
  /** (MUDD-114) The payment's OWN currency. This list mixes every tenant on
   *  the platform, so a single ambient label was wrong for any row that did
   *  not happen to be in it. */
  currency?: string;
  method: string;
  status: 'pending' | 'approved' | 'rejected' | 'refunded';
  receipt_image_url: string | null;
  created_at: string;
  paid_at: string | null;
}
interface PlatformPayments {
  collected_this_month_uzs: number;
  pending_amount_uzs: number;
  collected_this_month_by_currency?: Record<string, number>;
  pending_amount_by_currency?: Record<string, number>;
  pending_count: number;
  items: PlatformPaymentRow[];
}
/**
 * H17: previously called with no `limit` at all, which silently rode the
 * backend's own default (50) — a store with more pending payments than that
 * had real rows invisible past page 2 of the (client-side) DataTable, not
 * merely a slow fetch. Requesting the backend's hard cap (`LimitQ`'s
 * `le=200`, `platform/overview.py:141`) is the largest single bounded fetch
 * this endpoint allows; true next-page fetching (mirroring
 * `useWorklist`'s `useInfiniteQuery` in `queries/tenant.ts`) needs the route
 * to drive `offset`, which the backend does not yet accept for this endpoint.
 */
const PLATFORM_PAYMENTS_LIMIT = 200;

export function usePlatformPayments(statusFilter?: string) {
  return useQuery({
    queryKey: queryKeys.platform.payments(statusFilter ?? 'all'),
    placeholderData: keepPreviousData,  // (#621) keep rows visible while refetching on filter change
    queryFn: async ({ signal }) => (await api.get<PlatformPayments>('/platform/payments', {
      params: {
        status_filter: statusFilter || undefined,
        limit: PLATFORM_PAYMENTS_LIMIT,
      },
      signal,
    })).data,
    staleTime: 20_000,
  });
}

export interface TenantRow {
  id: string;
  slug: string;
  name: string;
  status: 'active' | 'demo' | 'suspended' | 'blocked';
  plan_code: string;
  locale: string;
  contact_phone: string | null;
  share_url?: string;
  created_at: string;
}

export interface TenantDetail {
  id: string; name: string; slug: string;
  status: 'active' | 'demo' | 'suspended' | 'blocked';
  plan_code: string;
  locale: string; contact_phone: string | null; billing_email: string | null;
  balance: number; created_at: string; trial_ends_at: string | null;
  plan_overrides?: { price_monthly?: number; limits?: Record<string, number | null>; overage?: Record<string, OverageRule> };
  stats: { customers: number; active_contracts: number; financed: number; received: number; staff: number };
}

export function useTenantDetail(id: string | null) {
  return useQuery({
    queryKey: queryKeys.platform.tenantDetail(id),
    queryFn: async ({ signal }) => (await api.get<TenantDetail>(`/platform/tenants/${id}/detail`, { signal })).data,
    enabled: !!id,
    staleTime: 10_000,
  });
}

export function usePlatformTenants(params?: { status?: string; limit?: number }) {
  return useQuery({
    queryKey: queryKeys.platform.tenants(params),
    placeholderData: keepPreviousData,  // (#621) no skeleton flash on filter/page change
    queryFn: async ({ signal }) => {
      // The backend expects `status_filter`; map it so the status tabs actually
      // filter (otherwise every tab showed all tenants).
      const { data } = await api.get<{ items: TenantRow[]; total: number }>('/platform/tenants', {
        params: { status_filter: params?.status, limit: params?.limit },
        signal,
      });
      return data;
    },
    staleTime: 15_000,
  });
}

export interface PlanRow {
  id: string;
  code: string;   // free-form (R36): owner-chosen slug, no longer a fixed enum
  name_uz: string;
  name_ru: string;
  description_uz: string | null;
  description_ru: string | null;
  price_monthly: number;
  max_active_installments: number | null;
  features: Record<string, boolean>;
  is_active: boolean;
  // Capability flags — admin can rebalance per plan
  bot_mode: 'shared' | 'tenant';
  allow_custom_domain: boolean;
  max_custom_fields: number | null;
  max_staff: number | null;
  broadcasts_per_month: number | null;
  // Round 39 — subscription + overage model
  max_branches: number | null;
  overage: Record<string, OverageRule>;
  is_popular: boolean;
  is_public: boolean;
  is_contact_sales: boolean;
  // Round 26 capability flags + marketing badge + custom-plan unit price
  allow_pdf_template_upload: boolean;
  allow_api_keys: boolean;
  allow_outbound_webhooks: boolean;
  sms_included_per_month: number;
  commission_management: boolean;
  multi_branch: boolean;
  risk_network_access: boolean;
  reports_advanced: boolean;
  badge: 'limited' | 'most_popular' | 'full' | 'custom' | null;
  price_per_installment: number | null;
  sort_order: number;
}

export function usePlatformPlans() {
  return useQuery({
    queryKey: queryKeys.platform.plans(),
    queryFn: async ({ signal }) => (await api.get<{ items: PlanRow[] }>('/platform/plans', { signal })).data.items,
    staleTime: 60_000,
  });
}

interface PlatformRiskStats {
  total_signals: number;
  by_status: Record<string, number>;
  published_by_severity: Record<string, number>;
  opt_in_tenant_count: number;
  flagged_customers: number;
}

export function usePlatformRiskStats() {
  return useQuery({
    queryKey: queryKeys.platform.riskStats(),
    queryFn: async ({ signal }) => (await api.get<PlatformRiskStats>('/platform/risk/stats', { signal })).data,
    staleTime: 60_000,
  });
}

// ── platform: admins / audit / promos / ads / referrals / settings ─────────

export interface AdminRow {
  id: string; telegram_id: number; telegram_username: string | null;
  first_name: string | null; last_name: string | null;
  photo_url: string | null;
  platform_role: string; is_active: boolean;
  last_seen_at: string | null; created_at: string;
}
export function usePlatformAdmins() {
  return useQuery({
    queryKey: queryKeys.platform.admins(),
    queryFn: async ({ signal }) => (await api.get<{ items: AdminRow[] }>('/platform/admins', { signal })).data.items,
    staleTime: 60_000,
  });
}

export interface AuditRow {
  id: string; tenant_id: string | null; actor_user_id: string | null;
  /** Resolved server-side from actor_user_id. Null when the actor no longer
   *  exists — the UI then falls back to the id rather than naming the wrong
   *  person. The log used to ship only the id, so it read as truncated UUIDs. */
  actor_name: string | null;
  action: string; entity_type: string | null; entity_id: string | null;
  ip: string | null; created_at: string;
}
interface AuditFilters {
  action?: string; tenant_id?: string; date_from?: string; date_to?: string;
  limit?: number; offset?: number;
}
export function usePlatformAudit(filters?: AuditFilters) {
  return useQuery({
    queryKey: queryKeys.platform.audit(filters ?? {}),
    placeholderData: keepPreviousData,  // (#621) keep rows during filter refetch
    queryFn: async ({ signal }) => (await api.get<{ items: AuditRow[]; total: number; actions: string[] }>(
      '/platform/audit',
      { params: {
        action: filters?.action || undefined,
        tenant_id: filters?.tenant_id || undefined,
        date_from: filters?.date_from || undefined,
        date_to: filters?.date_to || undefined,
        limit: filters?.limit ?? undefined,
        offset: filters?.offset ?? undefined,
      }, signal },
    )).data,
    staleTime: 30_000,
  });
}

export interface PromoRow {
  id: string; code: string; description: string | null;
  kind: 'percent' | 'fixed'; value: number;
  max_uses: number | null; used_count: number;
  valid_from: string | null; valid_until: string | null; is_active: boolean;
}
export function usePlatformPromos() {
  return useQuery({
    queryKey: queryKeys.platform.promos(),
    queryFn: async ({ signal }) => (await api.get<{ items: PromoRow[] }>('/platform/promos', { signal })).data.items,
    staleTime: 30_000,
  });
}

export interface AdRow {
  id: string; name: string; channel: string; status: string;
  budget: number; spent: number; leads_count: number; converted_tenants: number;
  started_at: string | null; ended_at: string | null; notes?: string | null;
}
export function usePlatformAds() {
  return useQuery({
    queryKey: queryKeys.platform.ads(),
    queryFn: async ({ signal }) => (await api.get<{ items: AdRow[] }>('/platform/ads', { signal })).data.items,
    staleTime: 30_000,
  });
}

export interface ReferralRow {
  id: string; referrer_user_id: string; referrer_name: string | null;
  referee_tenant_id: string | null; referee_name: string | null;
  status: string; credit_amount: number; notes: string | null; created_at: string;
}
export function usePlatformReferrals() {
  return useQuery({
    queryKey: queryKeys.platform.referrals(),
    queryFn: async ({ signal }) => (await api.get<{ items: ReferralRow[] }>('/platform/referrals', { signal })).data.items,
    staleTime: 60_000,
  });
}

interface TopupReviewRow {
  id: string; tenant_id: string; tenant_name: string;
  amount: number; method: 'card' | 'payme' | 'click';
  status: 'pending' | 'paid' | 'rejected';
  receipt_url: string | null; created_at: string;
  /** Which platform receiving card a manual transfer was sent to — the label is
   *  a "Bank · ···1234" snapshot, the id its fallback when no label was stored. */
  dest_card_label: string | null; dest_card_id: string | null;
  note: string | null;
  /** Audit trail. Null when the actor is unknown or was deleted — never a
   *  placeholder name, so the UI omits the row instead of asserting a person. */
  created_by_name: string | null;
  reviewed_by_name: string | null; reviewed_at: string | null;
}
export function usePlatformTopups(status = 'pending') {
  return useQuery({
    queryKey: queryKeys.platform.topups(status),
    queryFn: async ({ signal }) => (await api.get<{ items: TopupReviewRow[] }>(
      '/platform/topups', { params: { status_filter: status }, signal },
    )).data.items,
    staleTime: 15_000,
  });
}

interface SettingRow {
  key: string; value: unknown; description: string | null;
  /** Present on `billing_cards` only: per-card resolution state from
   *  the server's compatibility matrix. Deliberately BESIDE `value`, so a save
   *  round-trip cannot persist the annotation into the setting itself. */
  validation?: import('@/components/ui/CardManager').CardValidation[];
}
export function usePlatformSettings() {
  return useQuery({
    queryKey: queryKeys.platform.settings(),
    queryFn: async ({ signal }) => (await api.get<{ items: SettingRow[] }>('/platform/settings', { signal })).data.items,
    staleTime: 60_000,
  });
}

export interface PlatformBroadcastRow {
  id: string;
  title: string;
  body_uz: string;
  body_ru?: string | null;
  media_url?: string | null;
  button_label?: string | null;
  button_url?: string | null;
  segment?: string;
  status: string;
  sent_count: number;
  failed_count: number;
  created_at: string | null;
}

/** (#203) Recent platform-wide (HQ) broadcasts. */
export function usePlatformBroadcasts() {
  return useQuery({
    queryKey: queryKeys.platform.broadcasts(),
    queryFn: async ({ signal }) => (await api.get<{ items: PlatformBroadcastRow[]; total: number }>('/platform/broadcasts', { signal })).data,
  });
}

export interface SegmentRow { key: string; label_uz: string; label_ru: string; count: number }

/** Recipient counts per user segment — powers the broadcast reach preview. */
export function usePlatformSegments() {
  return useQuery({
    queryKey: queryKeys.platform.segments(),
    queryFn: async ({ signal }) => (await api.get<{ segments: SegmentRow[] }>('/platform/users/segments', { signal })).data.segments,
  });
}

export interface PlatformUserRow {
  id: string;
  telegram_id: number | null;
  telegram_username: string | null;
  first_name: string | null;
  last_name: string | null;
  phone: string | null;
  locale: string | null;
  platform_role: string | null;
  is_client: boolean;
  memberships: { tenant: string; role: string; status: string }[];
  created_at: string | null;
  last_seen_at: string | null;
}

/** All Muddato users (Telegram recipients), filterable by segment + search. */
export function usePlatformUsers(params: { segment: string; q: string; limit: number; offset: number }) {
  return useQuery({
    queryKey: queryKeys.platform.users(params),
    queryFn: async ({ signal }) => (await api.get<{ items: PlatformUserRow[]; total: number; limit: number; offset: number }>(
      '/platform/users',
      { params: { segment: params.segment, q: params.q || undefined, limit: params.limit, offset: params.offset }, signal },
    )).data,
    placeholderData: keepPreviousData,  // (#621) normalized to the shared helper
  });
}
