/** Tenant back-office writes: customers, contracts, money, settings, staff, invitations. */

import { useMutation, useQueryClient } from '@tanstack/react-query';
import { api } from '../api';
import { queryKeys } from '../queryKeys';

// ── tenant: customers ──────────────────────────────────────────────────────

export interface CustomerWrite {
  full_name: string;
  phone: string;
  secondary_phone?: string | null;
  passport_series?: string | null;
  passport_number?: string | null;
  jshshir?: string | null;
  birthdate?: string | null;
  address?: string | null;
  notes?: string | null;
  telegram_id?: number | null;
  /** joined | known | new — see TelegramStatusBadge. `known` means the
   *  person already uses the bot and is messaged automatically. */
  telegram_status?: 'joined' | 'known' | 'new';
  employment_place?: string | null;
  monthly_income?: number | null;
  mehnat_daftarchasi_photo_urls?: string[];
}

export interface CustomerInvite { token: string; url: string }

/** What `POST /customers/{id}/invite` answers.
 *
 *  A customer who is ALREADY connected gets no link at all — minting one would
 *  put a live bearer credential on screen for a journey nobody has to take.
 *  The two shapes are disjoint, so the caller must branch rather than read
 *  `url` off whatever came back. */
export type ResendInviteResult =
  | (CustomerInvite & { already_linked?: undefined })
  | { already_linked: true; telegram_status: 'joined'; invite: null };

export function useCreateCustomer() {
  const qc = useQueryClient();
  return useMutation({
    // POST returns the customer dict PLUS a one-time `invite` link minted on create.
    mutationFn: (body: CustomerWrite) =>
      api.post('/tenant/customers', body).then((r) => r.data as { id: string; invite?: CustomerInvite } & Record<string, unknown>),
    onSuccess: () => qc.invalidateQueries({ queryKey: queryKeys.tenant.customersAll() }),
  });
}

/** Re-issue a one-time join invite for an existing customer. */
export function useResendCustomerInvite() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: string) =>
      api.post(`/tenant/customers/${id}/invite`).then((r) => r.data as ResendInviteResult),
    onSuccess: () => qc.invalidateQueries({ queryKey: queryKeys.tenant.customersAll() }),
  });
}

export function useUpdateCustomer() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, body }: { id: string; body: Partial<CustomerWrite> & { status?: string } }) =>
      api.patch(`/tenant/customers/${id}`, body).then((r) => r.data),
    onSuccess: () => qc.invalidateQueries({ queryKey: queryKeys.tenant.customersAll() }),
  });
}

export function useDeleteCustomer() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => api.delete(`/tenant/customers/${id}`).then((r) => r.data),
    // Refresh on settle (success OR error) so a row that's already gone on the
    // server (404) can't linger in the list and trigger repeat "not found"s.
    onSettled: () => qc.invalidateQueries({ queryKey: queryKeys.tenant.customersAll() }),
  });
}

/** Flip a self-onboarded customer from `pending` to `active`. Owner / manager only. */
export function useApproveCustomer() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => api.post(`/tenant/customers/${id}/approve`).then((r) => r.data),
    onSuccess: () => qc.invalidateQueries({ queryKey: queryKeys.tenant.customersAll() }),
  });
}

/** Reject a self-onboarded customer — flips them to `blocked`. */
export function useRejectCustomer() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => api.post(`/tenant/customers/${id}/reject`).then((r) => r.data),
    onSuccess: () => qc.invalidateQueries({ queryKey: queryKeys.tenant.customersAll() }),
  });
}

// ── tenant: guarantors (kafil) ───────────────────────────────────────────────

export interface GuarantorWrite {
  full_name: string;
  phone: string;
  passport_series?: string | null;
  passport_number?: string | null;
  jshshir?: string | null;
  relationship?: string | null;
  telegram_id?: number | null;
  /** joined | known | new — see TelegramStatusBadge. `known` means the
   *  person already uses the bot and is messaged automatically. */
  telegram_status?: 'joined' | 'known' | 'new';
  address?: string | null;
  photo_urls?: string[];
}

export function useCreateGuarantor() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: GuarantorWrite) => api.post('/tenant/guarantors', body).then((r) => r.data),
    onSuccess: () => qc.invalidateQueries({ queryKey: queryKeys.tenant.guarantorsAll() }),
  });
}

export function useUpdateGuarantor() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, body }: { id: string; body: Partial<GuarantorWrite> }) =>
      api.patch(`/tenant/guarantors/${id}`, body).then((r) => r.data),
    onSuccess: () => qc.invalidateQueries({ queryKey: queryKeys.tenant.guarantorsAll() }),
  });
}

export function useDeleteGuarantor() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => api.delete(`/tenant/guarantors/${id}`).then((r) => r.data),
    onSuccess: () => qc.invalidateQueries({ queryKey: queryKeys.tenant.guarantorsAll() }),
  });
}

// ── tenant: products ───────────────────────────────────────────────────────

export interface ProductWrite {
  name_uz: string;
  name_ru?: string | null;
  sku?: string | null;
  category?: string | null;
  brand?: string | null;
  catalog_model_id?: string | null;
  base_price: number;
  cost_price?: number | null;
  stock?: number | null;
  min_stock_level?: number | null;
  description_uz?: string | null;
  description_ru?: string | null;
  attributes?: Record<string, unknown> | null;
  photo_urls?: string[];
  is_active?: boolean;
}

export function useCreateProduct() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: ProductWrite) => api.post('/tenant/products', body).then((r) => r.data),
    onSuccess: () => qc.invalidateQueries({ queryKey: queryKeys.tenant.productsAll() }),
  });
}

export function useUpdateProduct() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, body }: { id: string; body: Partial<ProductWrite> }) =>
      api.patch(`/tenant/products/${id}`, body).then((r) => r.data),
    onSuccess: () => qc.invalidateQueries({ queryKey: queryKeys.tenant.productsAll() }),
  });
}

export function useDeleteProduct() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => api.delete(`/tenant/products/${id}`).then((r) => r.data),
    onSettled: () => qc.invalidateQueries({ queryKey: queryKeys.tenant.productsAll() }),
  });
}

// ── tenant: installments + payments ────────────────────────────────────────

export interface InstallmentItemWrite {
  product_id?: string | null;
  catalog_model_id?: string | null;
  product_name_snapshot: string;
  /** (MUDD-83) A `catalog_categories.slug`. Optional — the server derives it
   *  from `catalog_model_id`, then `product_id`, when it is absent. Sending a
   *  slug the catalog does not know is a 400, not a silent write: the UI must
   *  never show a category the list filter cannot select. */
  category?: string | null;
  variant_label?: string | null;
  serial_number?: string | null;
  /** What `serial_number` is. Sent only alongside a value; the server refuses
   *  one without the other, and refuses `LEGACY_UNVERIFIED` outright. */
  identifier_type?: 'IMEI' | 'SERIAL_OR_CONTRACT' | null;
  /** (MUDD-85/98) Category-schema-declared fields (IMEI 1/2, VIN, …), keyed
   *  by `catalog_categories.identifier_schema[].key`. Validated against the
   *  resolved category's schema server-side — an unknown key, a missing
   *  required one, or a value already recorded for this tenant is a 400. */
  identifiers?: Record<string, string>;
  qty?: number;
  unit_price: number;
}

interface InstallmentWrite {
  customer_id: string;
  months: number;
  down_payment?: number;
  interest_rate?: number | null;
  /** Financing model — falls back to the tenant default server-side when omitted. */
  calc_formula?: 'none' | 'markup' | 'annuity' | 'differentiated' | null;
  markup_pct?: number | null;
  start_date?: string | null;
  items: InstallmentItemWrite[];
  guarantor_ids?: string[];
  notes?: string | null;
}

export function useCreateInstallment() {
  const qc = useQueryClient();
  return useMutation({
    // (F-12) An optional Idempotency-Key makes a retried/double-submitted create a
    // server-side no-op that returns the existing contract — a DB-enforced backstop
    // behind the modal's synchronous submit guard.
    mutationFn: ({ body, idempotencyKey }: { body: InstallmentWrite; idempotencyKey?: string }) =>
      api
        .post('/tenant/installments', body, {
          headers: idempotencyKey ? { 'Idempotency-Key': idempotencyKey } : undefined,
        })
        .then((r) => r.data),
    onSuccess: () => invalidateInstallments(qc),
  });
}

interface RemindResult { sent: boolean; channel: 'telegram' | null; phone: string }
export function useRemindInstallment() {
  return useMutation({
    mutationFn: (id: string) =>
      api.post<RemindResult>(`/tenant/installments/${id}/remind`).then((r) => r.data),
  });
}

function invalidateInstallments(qc: ReturnType<typeof useQueryClient>, id?: string) {
  qc.invalidateQueries({ queryKey: queryKeys.tenant.installmentsAll() });
  if (id) qc.invalidateQueries({ queryKey: queryKeys.tenant.installment(id) });
  // Cancelling, rescheduling and rewriting a contract all change whether it is
  // due — and the collections queue is mounted on the same screen. The paged key
  // carries (pageSize, search), so only the family prefix reaches every variant.
  qc.invalidateQueries({ queryKey: queryKeys.tenant.worklistAll() });
  qc.invalidateQueries({ queryKey: queryKeys.tenant.overview() });
}

export function useCancelInstallment() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, reason }: { id: string; reason: string }) =>
      api.post(`/tenant/installments/${id}/cancel`, { reason }).then((r) => r.data),
    onSuccess: (_d, v) => invalidateInstallments(qc, v.id),
  });
}

export function useRescheduleInstallment() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, add_months, next_due_date }: { id: string; add_months?: number; next_due_date?: string | null }) =>
      api.post(`/tenant/installments/${id}/reschedule`, { add_months, next_due_date }).then((r) => r.data),
    onSuccess: (_d, v) => invalidateInstallments(qc, v.id),
  });
}

interface InstallmentWriteBody {
  customer_id: string;
  months: number;
  down_payment: number;
  interest_rate: number | null;
  start_date: string | null;
  notes: string | null;
  guarantor_ids: string[];
  items: InstallmentItemWrite[];
}
export function useReplaceInstallment() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, body }: { id: string; body: InstallmentWriteBody }) =>
      api.put(`/tenant/installments/${id}`, body).then((r) => r.data),
    onSuccess: (_d, v) => invalidateInstallments(qc, v.id),
  });
}

export function useMarkNotificationsRead() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () => api.post('/me/notifications/read').then((r) => r.data),
    onSuccess: () => qc.invalidateQueries({ queryKey: queryKeys.me.notifications() }),
  });
}

interface RecordPaymentBody {
  installment_id: string;
  amount: number;
  method: string;
  receipt_image_url?: string | null;
  mark_approved?: boolean;
}
export function useRecordTenantPayment() {
  const qc = useQueryClient();
  return useMutation({
    // (H13) Optional Idempotency-Key mirrors useCreateInstallment's backstop
    // above: a duplicated record-payment request becomes a server-side no-op
    // instead of a second credit. `vars` still carries every RecordPaymentBody
    // field (idempotencyKey is stripped only for the wire body), so existing
    // callers/tests that omit it are unaffected.
    mutationFn: ({ idempotencyKey, ...body }: RecordPaymentBody & { idempotencyKey?: string }) =>
      api
        .post('/tenant/payments', body, {
          headers: idempotencyKey ? { 'Idempotency-Key': idempotencyKey } : undefined,
        })
        .then((r) => r.data),
    onSuccess: (_d, vars) => {
      qc.invalidateQueries({ queryKey: queryKeys.tenant.installmentsAll() });
      qc.invalidateQueries({ queryKey: queryKeys.tenant.installment(vars.installment_id) });
      qc.invalidateQueries({ queryKey: queryKeys.tenant.payments() });
      // Same screen as the collections queue, and the row may now be settled.
      qc.invalidateQueries({ queryKey: queryKeys.tenant.worklistAll() });
      // The shift tiles count what this user took today and how many payments
      // await approval; both move here. `tenant.staff` does not prefix-match
      // `tenant.staff-today`, so it needs naming explicitly.
      qc.invalidateQueries({ queryKey: queryKeys.tenant.staffToday() });
      qc.invalidateQueries({ queryKey: queryKeys.tenant.overview() });
    },
  });
}

/** The server returns the contract's post-apply balance so the success message
 *  can state the resulting debt. Server-authoritative: nothing is rendered until
 *  this resolves, and a failure never reports success. */
interface ApprovePaymentResult {
  id: string;
  installment?: {
    id: string; contract_number: string | null;
    total_amount: number; paid_amount: number; remaining: number;
    status: string; next_due_date: string | null;
  };
}

export function useApprovePayment() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: string) =>
      api.post<ApprovePaymentResult>(`/tenant/payments/${id}/approve`).then((r) => r.data),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: queryKeys.tenant.payments() });
      qc.invalidateQueries({ queryKey: queryKeys.tenant.installmentsAll() });
      qc.invalidateQueries({ queryKey: queryKeys.tenant.overview() });
      // Approval runs apply_payment server-side, so the contract's balance and
      // next due date move — and with them its place in the queue.
      qc.invalidateQueries({ queryKey: queryKeys.tenant.worklistAll() });
      qc.invalidateQueries({ queryKey: queryKeys.tenant.staffToday() });
    },
  });
}

export function useRejectPayment() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => api.post(`/tenant/payments/${id}/reject`).then((r) => r.data),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: queryKeys.tenant.payments() });
      // Rejection only flips the payment's status — it never applies it to the
      // contract — so the queue is deliberately left alone. The tiles' pending
      // count does change.
      qc.invalidateQueries({ queryKey: queryKeys.tenant.staffToday() });
    },
  });
}

interface BroadcastCreateIn {
  title: string;
  body_uz: string;
  body_ru?: string | null;
  media_url?: string | null;
  button_label?: string | null;
  button_url?: string | null;
  target?: 'all' | 'active';
}

export function useCreateBroadcast() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: BroadcastCreateIn) => api.post('/tenant/broadcasts', body).then((r) => r.data),
    onSuccess: () => qc.invalidateQueries({ queryKey: queryKeys.tenant.broadcasts() }),
  });
}

export function useDeleteBroadcast() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => api.delete(`/tenant/broadcasts/${id}`),
    onSuccess: () => qc.invalidateQueries({ queryKey: queryKeys.tenant.broadcasts() }),
  });
}

// ── tenant: settings + staff ───────────────────────────────────────────────

import type { TenantSettingsBundle } from '../queries';

export function usePatchTenantSettings() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: Partial<TenantSettingsBundle>) =>
      api.patch<TenantSettingsBundle>('/tenant/settings', body).then((r) => r.data),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: queryKeys.tenant.settings() });
      // Invalidate topup-info if payment methods (including cards) were modified.
      // This ensures TopUpModal sees fresh card list after user saves settings (#sync-bug-fix).
      qc.invalidateQueries({ queryKey: queryKeys.tenant.topupInfo() });
    },
  });
}

interface RiskReportWrite {
  customer_id: string;
  severity?: 'fraud_reported' | 'charged_off';
  note?: string;
}

/** Manually flag a customer in the cross-tenant risk network (Round 23). */
export function useReportRisk() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: RiskReportWrite) =>
      api.post<{ ok: boolean; created: boolean; signal_id: string | null }>(
        '/tenant/risk/report', body,
      ).then((r) => r.data),
    onSuccess: (_d, vars) =>
      qc.invalidateQueries({ queryKey: queryKeys.tenant.riskScore(vars.customer_id) }),
  });
}

export function useUpdateStaff() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, body }: { id: string; body: { role?: string; is_active?: boolean } }) =>
      api.patch(`/tenant/staff/${id}`, body).then((r) => r.data),
    onSuccess: () => qc.invalidateQueries({ queryKey: queryKeys.tenant.staff() }),
  });
}

export function useRevokeStaff() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => api.delete(`/tenant/staff/${id}`).then((r) => r.data),
    onSuccess: () => qc.invalidateQueries({ queryKey: queryKeys.tenant.staff() }),
  });
}

// ── tenant: invitations (customer + staff onboarding via one-shot links) ───

export function useCreateInvitation() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: {
      kind: 'customer' | 'staff' | 'guarantor';
      role?: 'owner' | 'manager' | 'sales' | 'accountant' | 'custom';
      preset?: Record<string, unknown>;
      expires_hours?: number;
    }) => api.post('/tenant/invitations', body).then((r) => r.data),
    onSuccess: () => qc.invalidateQueries({ queryKey: queryKeys.tenant.invitationsAll() }),
  });
}

export function useRevokeInvitation() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => api.delete(`/tenant/invitations/${id}`).then((r) => r.data),
    onSuccess: () => qc.invalidateQueries({ queryKey: queryKeys.tenant.invitationsAll() }),
  });
}

export function useActivateTenantBot() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: { bot_token: string }) =>
      api.post<{ ok: boolean; bot_username: string }>('/tenant/settings/bot/activate', body)
         .then((r) => r.data),
    onSuccess: () => qc.invalidateQueries({ queryKey: queryKeys.tenant.settings() }),
  });
}

export function useDeactivateTenantBot() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () => api.post('/tenant/settings/bot/deactivate').then((r) => r.data),
    onSuccess: () => qc.invalidateQueries({ queryKey: queryKeys.tenant.settings() }),
  });
}
