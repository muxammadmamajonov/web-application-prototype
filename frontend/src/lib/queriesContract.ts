/**
 * Compile-time pin for the type half of `@/lib/queries`' public contract.
 *
 * Types are erased before any test runs, so `Object.keys` cannot see them, and
 * `tsconfig.json` excludes `*.test.ts` from `tsc -b` — a pin inside the sibling
 * `queriesContract.test.ts` would be a guard that can never fail. This module
 * is ordinary source, so `npm run typecheck` compiles it: if any type below
 * stops being exported from the barrel, the import fails here by name.
 *
 * Every entry is a type a real consumer imports today. Response shapes with no
 * consumer are deliberately absent — they are private to their own module, and
 * adding one here would re-create the over-exported surface this pin exists to
 * hold steady.
 *
 * Nothing imports this module and nothing should: it emits no runtime code.
 */
import type {
  AdRow, AdminRow, AuditRow, BizType, BroadcastRow, CatalogBrandRow, CatalogCategoryRow,
  CatalogColorRow, CatalogModelRow, ChannelType, CustomFieldDef, CustomerChannel,
  CustomerRow, Granularity, HowFoundOption, ImeiCheckResult, ImportPreview, ImportResult,
  InstallmentDetail, InstallmentRow, InvitationRow, MovementRow, MyInstallmentHeadline,
  MyInstallmentRow, NotificationRow, OverageRule, PaymentRow, PlanRow, PlatformBroadcastRow,
  PlatformPaymentRow, PlatformUserRow, ProductRow, PromoRow, PublicPlan, ReferralRow,
  RiskScore, RiskTier, SegmentRow, StaffRow, StockAdjustBody, TenantDetail, TenantOverview,
  TenantRow, TenantSettingsBundle,
} from './queries';

/** Naming each type keeps `noUnusedLocals` satisfied and the imports load-bearing. */
export type PinnedQueryTypes = [
  AdRow, AdminRow, AuditRow, BizType, BroadcastRow, CatalogBrandRow, CatalogCategoryRow,
  CatalogColorRow, CatalogModelRow, ChannelType, CustomFieldDef, CustomerChannel,
  CustomerRow, Granularity, HowFoundOption, ImeiCheckResult, ImportPreview, ImportResult,
  InstallmentDetail, InstallmentRow, InvitationRow, MovementRow, MyInstallmentHeadline,
  MyInstallmentRow, NotificationRow, OverageRule, PaymentRow, PlanRow, PlatformBroadcastRow,
  PlatformPaymentRow, PlatformUserRow, ProductRow, PromoRow, PublicPlan, ReferralRow,
  RiskScore, RiskTier, SegmentRow, StaffRow, StockAdjustBody, TenantDetail, TenantOverview,
  TenantRow, TenantSettingsBundle,
];
