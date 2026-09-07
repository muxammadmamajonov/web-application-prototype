/**
 * The public contract of `@/lib/queries`.
 *
 * 54 modules import from this path. This test pins what they are entitled to
 * find there, so the read layer can be reorganised without silently dropping a
 * symbol — a missing hook would otherwise surface as a runtime crash on one
 * screen, and a missing type as a confusing error in an unrelated file.
 *
 * Two surfaces fail differently, so they are pinned in two places:
 *
 *   - **Runtime exports** (hooks and imperative helpers) are object keys at run
 *     time, so they are asserted here with `Object.keys`.
 *   - **Type exports** are erased before this file executes, so no runtime
 *     assertion can reach them — and `tsconfig.json` excludes `*.test.ts` from
 *     `tsc -b`, so pinning them *here* would be a guard that can never fail.
 *     They are pinned in `src/lib/queriesContract.ts` instead, which the
 *     typecheck does compile.
 *
 * Deliberately not a source-reading regex test — it asserts against the module
 * as consumers actually resolve it, not against the text of a particular file.
 */
import { describe, expect, it } from 'vitest';
import * as queries from '@/lib/queries';
/**
 * Every value consumers may import from `@/lib/queries`.
 *
 * `fetchRiskScore` is deliberately absent: it is the imperative half of
 * `useRiskScore`, no module outside the read layer calls it, and it is now
 * private to the tenant module. Removing a name from this list is the explicit
 * act that records a public-surface change.
 */
const RUNTIME_EXPORTS: readonly string[] = [
  'adjustStock', 'changeTenantPlan', 'checkImei', 'createTopup', 'downloadInvoicePdf',
  'downloadReport', 'importCommit', 'importPreview',
  'sendReportToTelegram', 'useAllCatalogBrands', 'useCatalogBrands',
  'useCatalogCategories', 'useCatalogColors', 'useCatalogModels', 'useCatalogModelsFlat',
  'useCustomerChannels', 'useLowStock', 'useMyIdentity', 'useMyInstallment', 'useMyInstallments',
  'useMyNotifications', 'useMySummary', 'useOnboardingOptions', 'usePendingPaymentCount',
  'usePlatformAdmins', 'usePlatformAds', 'usePlatformAudit', 'usePlatformBroadcasts',
  'usePlatformOverview', 'usePlatformPayments', 'usePlatformPlans', 'usePlatformPromos',
  'usePlatformReferralLink', 'usePlatformReferrals', 'usePlatformRiskStats',
  'usePlatformSegments', 'usePlatformSettings', 'usePlatformTenants', 'usePlatformTopups',
  'usePlatformUsers', 'useProductMovements', 'usePublicPlans', 'useReportAging',
  'useReportCashflow', 'useReportLtv', 'useReportMargin', 'useReportProducts',
  'useReportStaff', 'useRiskScore', 'useSmsHistory', 'useSmsUsage', 'useStaffToday',
  'useSupportChannels',
  'useTenantBilling', 'useTenantBroadcasts', 'useTenantCustomers', 'useTenantDetail',
  'useTenantGuarantors', 'useTenantInstallmentDetail', 'useTenantInstallments',
  'useTenantInvitations', 'useTenantInvoices', 'useTenantOverview', 'useTenantPayments',
  'useTenantPaymentsPage',
  'useTenantProducts', 'useTenantSettings', 'useTenantStaff', 'useTenantTopupInfo',
  'useTenantTopups', 'useTenantWorklist', 'useTenantWorklistPages',
  'useYearlyBilling',
];

describe('@/lib/queries public contract', () => {
  it('exposes exactly the runtime exports consumers import', () => {
    expect(Object.keys(queries).sort()).toEqual([...RUNTIME_EXPORTS].sort());
  });

  it('every runtime export is a callable hook or helper', () => {
    const mod = queries as unknown as Record<string, unknown>;
    const notFunctions = RUNTIME_EXPORTS.filter((name) => typeof mod[name] !== 'function');
    expect(notFunctions).toEqual([]);
  });
});
