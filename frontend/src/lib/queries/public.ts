/** Unauthenticated reads: onboarding options and the public pricing table. */

import { useQuery } from '@tanstack/react-query';
import { api } from '../api';
import { queryKeys } from '../queryKeys';
import type { OverageRule } from './shared';

export interface BizType { slug: string; name_uz: string; name_ru: string; emoji?: string }
export interface HowFoundOption { value: string; label_uz: string; label_ru: string }
interface OnboardingOptions { business_types: BizType[]; how_found_options: HowFoundOption[] }

export function useOnboardingOptions() {
  return useQuery({
    queryKey: queryKeys.onboarding.options(),
    queryFn: async ({ signal }) => (await api.get<OnboardingOptions>('/onboarding/options', { signal })).data,
    staleTime: 5 * 60_000,
  });
}

// Public marketing-landing plan (GET /plans/public, no auth)
export interface PublicPlan {
  code: string;
  name_uz: string;
  name_ru: string;
  description_uz: string | null;
  description_ru: string | null;
  price_monthly: number;
  /** Derived server-side from `price_monthly` + the configured yearly discount.
   *  Never stored beside the monthly price — a second stored price is a second
   *  thing to keep in step, and the first reprice that forgets it makes this
   *  page advertise a discount that does not exist. */
  price_yearly: number;
  price_yearly_monthly_equivalent: number;
  yearly_saving: number;
  price_per_installment: number | null;
  max_active_installments: number | null;
  max_staff: number | null;
  max_branches: number | null;
  badge: 'limited' | 'most_popular' | 'full' | 'custom' | null;
  is_popular: boolean;
  is_contact_sales: boolean;
  overage: Record<string, OverageRule>;
  features: Record<string, boolean>;
  caps: {
    allow_custom_domain: boolean;
    allow_pdf_template_upload: boolean;
    allow_api_keys: boolean;
    allow_outbound_webhooks: boolean;
    sms_included_per_month: number;
    commission_management: boolean;
    multi_branch: boolean;
    risk_network_access: boolean;
    reports_advanced: boolean;
    max_custom_fields: number | null;
    broadcasts_per_month: number | null;
  };
}

/** The platform-wide yearly-billing promo, configured in Platform → Sozlamalar. */
export interface YearlyBilling { enabled: boolean; discount_pct: number }

export interface PublicPlansResponse { items: PublicPlan[]; yearly: YearlyBilling }

export function usePublicPlans() {
  return useQuery({
    queryKey: queryKeys.public.plans(),
    queryFn: async ({ signal }) => (await api.get<PublicPlansResponse>('/plans/public', { signal })).data.items,
    staleTime: 5 * 60_000,
    // The global default is retry:false, but this is the public landing's
    // pricing — a single slow/cold-start hiccup must not strand the page on
    // "Tariflarni yuklab bo'lmadi". Retry a few times with backoff.
    retry: 3,
    retryDelay: (attempt) => Math.min(1000 * 2 ** attempt, 8000),
  });
}

export interface SupportChannels {
  support_username: string;
  /** Null when HQ has not configured one — the UI omits the row rather than
   *  advertising an address nobody has been told to watch. */
  company_email: string | null;
}

/**
 * The support contacts the product should show users, from platform settings.
 *
 * Public and unauthenticated on purpose: a user who cannot sign in is exactly
 * who needs the support handle. Cached hard — these change roughly never, and a
 * help screen must not wait on a round trip to render its first row.
 */
export function useSupportChannels() {
  return useQuery({
    queryKey: queryKeys.public.supportChannels(),
    queryFn: async ({ signal }) => (await api.get<SupportChannels>('/contact/channels', { signal })).data,
    staleTime: 30 * 60_000,
  });
}

/** The yearly promo alone — the landing page's billing toggle needs the
 *  percentage to label itself, and shares the same cached response. */
export function useYearlyBilling() {
  return useQuery({
    queryKey: queryKeys.public.yearlyBilling(),
    queryFn: async ({ signal }) => (await api.get<PublicPlansResponse>('/plans/public', { signal })).data.yearly,
    staleTime: 5 * 60_000,
    retry: 3,
  });
}
