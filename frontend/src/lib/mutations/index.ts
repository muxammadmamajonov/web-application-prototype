/**
 * Mutations + query invalidation helpers.
 *
 * The hooks live in per-persona modules beside this file; this barrel is the
 * single import path for all of them, so `@/lib/mutations` resolves the same way
 * it always has. Re-exports are explicit rather than wildcards: adding a hook is
 * a deliberate two-line act, and a request shape stays private to its own module
 * unless a consumer genuinely needs to name it.
 */

// ── profile (self) ──

export {
  useUpdateProfile
} from './profile';

// ── platform (HQ) console ──

export {
  useCreateAd, useCreateAdminInvitation, useCreatePlan, useCreatePromo, useCreateReferral,
  useCreateTenant, useDeleteAd, useDeletePlan, useDeletePromo, useDeleteReferral,
  useDeleteTenant, usePutSetting, useRemoveAdmin, useReviewTopup,
  useSendPlatformBroadcast, useSetPlanOverrides, useSetTenantPlan, useSetTenantStatus,
  useTenantOwnerInvite, useTopupBalance, useUpdateAd, useUpdateAdmin, useUpdatePlan,
  useUpdatePromo
} from './platform';

// ── global catalog ──

export {
  useCreateBrand, useCreateCatalogModel, useCreateCategory, useDeleteCatalogBrand,
  useDeleteCatalogCategory, useDeleteCatalogModel, useProposeModel, useUpdateBrand,
  useUpdateCatalogModel, useUpdateCategory
} from './catalog';

// ── tenant back-office ──

export {
  useActivateTenantBot, useApproveCustomer, useApprovePayment, useCancelInstallment,
  useCreateBroadcast, useCreateCustomer, useCreateGuarantor, useCreateInstallment,
  useCreateInvitation, useCreateProduct, useDeactivateTenantBot, useDeleteBroadcast,
  useDeleteCustomer, useDeleteGuarantor, useDeleteProduct, useMarkNotificationsRead,
  usePatchTenantSettings, useRecordTenantPayment, useRejectCustomer, useRejectPayment,
  useRemindInstallment, useReplaceInstallment, useReportRisk, useRescheduleInstallment,
  useResendCustomerInvite, useRevokeInvitation, useRevokeStaff, useUpdateCustomer,
  useUpdateGuarantor, useUpdateProduct, useUpdateStaff
} from './tenant';

export type {
  CustomerInvite, CustomerWrite, GuarantorWrite, InstallmentItemWrite, ProductWrite
} from './tenant';
