/**
 * The public contract of `@/lib/mutations`.
 *
 * 33 modules import from this path. This test pins what they are entitled to
 * find there, so the write layer can be reorganised without silently dropping a
 * hook — which would surface as a runtime crash on one screen rather than as a
 * build failure.
 *
 * Two surfaces fail differently, so they are pinned in two places:
 *
 *   - **Runtime exports** (mutation hooks) are object keys at run time, so they
 *     are asserted here with `Object.keys`.
 *   - **Type exports** are erased before this file executes, and `tsconfig.json`
 *     excludes `*.test.ts` from `tsc -b`, so pinning them here would be a guard
 *     that can never fail. They are pinned in `src/lib/mutationsContract.ts`
 *     instead, which the typecheck does compile.
 *
 * Deliberately not a source-reading regex test — it asserts against the module
 * as consumers actually resolve it, not against the text of a particular file.
 */
import { describe, expect, it } from 'vitest';
import * as mutations from '@/lib/mutations';

/**
 * Every value consumers may import from `@/lib/mutations`.
 *
 * `useInviteAdmin` is deliberately absent: the by-Telegram-ID admin invite it
 * backed was removed with its modal, and the retained, safer flow is
 * `useCreateAdminInvitation` (single-use role-scoped link). Removing a name from
 * this list is the explicit act that records a public-surface change.
 */
const RUNTIME_EXPORTS: readonly string[] = [
  'useActivateTenantBot', 'useApproveCustomer', 'useApprovePayment',
  'useCancelInstallment', 'useCreateAd', 'useCreateAdminInvitation', 'useCreateBrand',
  'useCreateBroadcast', 'useCreateCatalogModel', 'useCreateCategory', 'useCreateCustomer',
  'useCreateGuarantor', 'useCreateInstallment', 'useCreateInvitation', 'useCreatePlan',
  'useCreateProduct', 'useCreatePromo', 'useCreateReferral', 'useCreateTenant',
  'useDeactivateTenantBot', 'useDeleteAd', 'useDeleteBroadcast', 'useDeleteCatalogBrand',
  'useDeleteCatalogCategory', 'useDeleteCatalogModel', 'useDeleteCustomer',
  'useDeleteGuarantor', 'useDeletePlan', 'useDeleteProduct', 'useDeletePromo',
  'useDeleteReferral', 'useDeleteTenant', 'useMarkNotificationsRead',
  'usePatchTenantSettings', 'useProposeModel', 'usePutSetting', 'useRecordTenantPayment',
  'useRejectCustomer', 'useRejectPayment', 'useRemindInstallment', 'useRemoveAdmin',
  'useReplaceInstallment', 'useReportRisk', 'useRescheduleInstallment',
  'useResendCustomerInvite', 'useReviewTopup', 'useRevokeInvitation', 'useRevokeStaff',
  'useSendPlatformBroadcast', 'useSetPlanOverrides', 'useSetTenantPlan',
  'useSetTenantStatus', 'useTenantOwnerInvite', 'useTopupBalance', 'useUpdateAd',
  'useUpdateAdmin', 'useUpdateBrand', 'useUpdateCatalogModel', 'useUpdateCategory',
  'useUpdateCustomer', 'useUpdateGuarantor', 'useUpdatePlan', 'useUpdateProduct',
  'useUpdateProfile', 'useUpdatePromo', 'useUpdateStaff',
];

describe('@/lib/mutations public contract', () => {
  it('exposes exactly the runtime exports consumers import', () => {
    expect(Object.keys(mutations).sort()).toEqual([...RUNTIME_EXPORTS].sort());
  });

  it('every runtime export is a callable hook', () => {
    const mod = mutations as unknown as Record<string, unknown>;
    expect(RUNTIME_EXPORTS.filter((n) => typeof mod[n] !== 'function')).toEqual([]);
  });
});
