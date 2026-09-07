/**
 * Centralised TanStack Query hooks for the Muddato API.
 *
 * The hooks live in per-persona modules beside this file; this barrel is the
 * single import path for all of them, so `@/lib/queries` resolves the same way
 * it always has. Re-exports are explicit rather than wildcards: adding a hook
 * is a deliberate two-line act, and a response shape stays private to its own
 * module unless a consumer genuinely needs to name it.
 */

// ── shared types ──

export type {
  OverageRule
} from './shared';

// ── public / onboarding ──

export {
  useOnboardingOptions, usePublicPlans, useSupportChannels, useYearlyBilling
} from './public';

export type {
  BizType, HowFoundOption, PublicPlan, SupportChannels, YearlyBilling
} from './public';

// ── platform (HQ) console ──

export {
  usePlatformAdmins, usePlatformAds, usePlatformAudit, usePlatformBroadcasts,
  usePlatformOverview, usePlatformPayments, usePlatformPlans, usePlatformPromos,
  usePlatformReferralLink, usePlatformReferrals, usePlatformRiskStats, usePlatformSegments,
  usePlatformSettings, usePlatformTenants, usePlatformTopups, usePlatformUsers,
  useTenantDetail
} from './platform';

export type {
  AdRow, AdminRow, AuditRow, PlanRow, PlatformBroadcastRow, PlatformPaymentRow,
  PlatformUserRow, PromoRow, ReferralRow, SegmentRow, TenantDetail, TenantRow
} from './platform';

// ── global catalog ──

export {
  useAllCatalogBrands, useCatalogBrands, useCatalogCategories, useCatalogColors,
  useCatalogModels, useCatalogModelsFlat
} from './catalog';

export type {
  CatalogBrandRow, CatalogCategoryRow, CatalogColorRow, CatalogModelRow
} from './catalog';

// ── tenant back-office ──

export {
  adjustStock, changeTenantPlan, checkImei, createTopup, downloadInvoicePdf, downloadReport,
  importCommit, importPreview, sendReportToTelegram, useCustomerChannels, useLowStock,
  usePendingPaymentCount, useProductMovements, useReportAging, useReportCashflow,
  useReportLtv, useReportMargin, useReportProducts, useReportStaff, useRiskScore,
  useSmsHistory, useSmsUsage, useTenantBilling, useTenantBroadcasts, useTenantCustomers,
  useTenantGuarantors, useTenantInstallmentDetail, useTenantInstallments,
  useTenantInvitations, useTenantInvoices, useTenantOverview, useTenantPayments,
  useTenantPaymentsPage,
  useTenantProducts, useTenantSettings, useTenantStaff, useTenantTopupInfo, useTenantTopups,
  useTenantWorklist, useTenantWorklistPages
} from './tenant';

export type {
  BroadcastRow, ChannelType, CustomFieldDef, CustomerChannel, CustomerRow, Granularity,
  ImeiCheckResult, ImportPreview, ImportResult, InstallmentDetail, InstallmentRow,
  InvitationRow, MovementRow, PaymentRow, ProductRow, RiskScore, RiskTier, StaffRow,
  StockAdjustBody, TenantOverview, TenantSettingsBundle
} from './tenant';

// ── sales staff ──

export {
  useStaffToday
} from './staff';

// ── customer Mini-App ──

export {
  useMyIdentity, useMyInstallment, useMyInstallments, useMyNotifications, useMySummary
} from './client';

export type {
  MyIdentity, MyInstallmentHeadline, MyInstallmentRow, NotificationRow
} from './client';
