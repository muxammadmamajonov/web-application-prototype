/** Platform (HQ) console writes: tenants, plans, promos, ads, admins, billing oversight. */

import { useMutation, useQueryClient } from '@tanstack/react-query';
import { api } from '../api';
import { queryKeys } from '../queryKeys';

// ── platform ───────────────────────────────────────────────────────────────

export function useCreateTenant() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: { name: string; plan_code: string; locale?: string; contact_phone?: string; billing_email?: string; business_type?: string; business_type_other?: string }) =>
      api.post('/platform/tenants', body).then((r) => r.data),
    onSuccess: () => qc.invalidateQueries({ queryKey: queryKeys.platform.tenantsAll() }),
  });
}

interface PlatformBroadcastInput {
  title: string;
  body_uz: string;
  body_ru?: string;
  media_url?: string;
  button_label?: string;
  button_url?: string;
  segment: string;
}

/** Send a platform-wide (HQ) broadcast to a chosen user segment. */
export function useSendPlatformBroadcast() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: PlatformBroadcastInput) =>
      api.post('/platform/broadcasts', body).then((r) => r.data),
    onSuccess: () => qc.invalidateQueries({ queryKey: queryKeys.platform.broadcasts() }),
  });
}

export function useSetTenantStatus() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, status }: { id: string; status: string }) =>
      api.patch(`/platform/tenants/${id}/status`, { status }).then((r) => r.data),
    onSuccess: () => qc.invalidateQueries({ queryKey: queryKeys.platform.tenantsAll() }),
  });
}

interface AdminInviteLink {
  id: string;
  token: string;
  role: string;
  telegram_url: string;
  web_url: string;
  expires_at: string;
}

/** Mint a single-use, role-scoped admin invite link. */
export function useCreateAdminInvitation() {
  return useMutation({
    mutationFn: (body: { role: 'super_admin' | 'support'; expires_hours?: number }) =>
      api.post<AdminInviteLink>('/platform/admin-invitations', body).then((r) => r.data),
  });
}

export function useUpdatePlan() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ code, body }: { code: string; body: Record<string, unknown> }) =>
      api.patch(`/platform/plans/${code}`, body).then((r) => r.data),
    onSuccess: () => qc.invalidateQueries({ queryKey: queryKeys.platform.plans() }),
  });
}

export function useCreatePromo() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: { code: string; kind: 'percent' | 'fixed'; value: number; description?: string; max_uses?: number | null }) =>
      api.post('/platform/promos', body).then((r) => r.data),
    onSuccess: () => qc.invalidateQueries({ queryKey: queryKeys.platform.promos() }),
  });
}

export function useDeleteTenant() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => api.delete(`/platform/tenants/${id}`).then((r) => r.data),
    onSuccess: () => qc.invalidateQueries({ queryKey: queryKeys.platform.tenantsAll() }),
  });
}

/** Mint a fresh single-use owner-claim link for an existing tenant. */
export function useTenantOwnerInvite() {
  return useMutation({
    mutationFn: (tenantId: string) =>
      api.post(`/platform/tenants/${tenantId}/owner-invite`).then(
        (r) => r.data as { share_url: string; expires_hours: number },
      ),
  });
}

export function useTopupBalance() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, amount, note }: { id: string; amount: number; note?: string }) =>
      api.post(`/platform/tenants/${id}/balance`, { amount, note }).then((r) => r.data as { balance: number }),
    onSuccess: (_d, v) => {
      qc.invalidateQueries({ queryKey: queryKeys.platform.tenantDetail(v.id) });
      qc.invalidateQueries({ queryKey: queryKeys.platform.tenantsAll() });
    },
  });
}

export function useSetPlanOverrides() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, body }: { id: string; body: { price_monthly?: number | null; limits?: Record<string, number | null>; overage?: Record<string, { price: number; enabled: boolean; mode: 'soft' | 'hard' }> } }) =>
      api.put(`/platform/tenants/${id}/plan-overrides`, body).then((r) => r.data),
    onSuccess: (_d, v) => {
      qc.invalidateQueries({ queryKey: queryKeys.platform.tenantDetail(v.id) });
    },
  });
}

export function useSetTenantPlan() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, plan_code }: { id: string; plan_code: string }) =>
      api.post(`/platform/tenants/${id}/set-plan`, { plan_code }).then((r) => r.data),
    onSuccess: (_d, v) => {
      qc.invalidateQueries({ queryKey: queryKeys.platform.tenantDetail(v.id) });
      qc.invalidateQueries({ queryKey: queryKeys.platform.tenantsAll() });
    },
  });
}

export function useCreatePlan() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: { code: string; name_uz: string; name_ru: string; price_monthly?: number; max_active_installments?: number | null; is_active?: boolean }) =>
      api.post('/platform/plans', body).then((r) => r.data),
    onSuccess: () => qc.invalidateQueries({ queryKey: queryKeys.platform.plans() }),
  });
}

export function useDeletePlan() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (code: string) => api.delete(`/platform/plans/${code}`).then((r) => r.data),
    onSuccess: () => qc.invalidateQueries({ queryKey: queryKeys.platform.plans() }),
  });
}

export function useUpdatePromo() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, body }: { id: string; body: Record<string, unknown> }) =>
      api.patch(`/platform/promos/${id}`, body).then((r) => r.data),
    onSuccess: () => qc.invalidateQueries({ queryKey: queryKeys.platform.promos() }),
  });
}

export function useDeletePromo() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => api.delete(`/platform/promos/${id}`).then((r) => r.data),
    onSuccess: () => qc.invalidateQueries({ queryKey: queryKeys.platform.promos() }),
  });
}

export function useCreateAd() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: { name: string; channel: string; status?: string; budget?: number; spent?: number; notes?: string | null }) =>
      api.post('/platform/ads', body).then((r) => r.data),
    onSuccess: () => qc.invalidateQueries({ queryKey: queryKeys.platform.ads() }),
  });
}

export function useUpdateAd() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, body }: { id: string; body: Record<string, unknown> }) =>
      api.patch(`/platform/ads/${id}`, body).then((r) => r.data),
    onSuccess: () => qc.invalidateQueries({ queryKey: queryKeys.platform.ads() }),
  });
}

export function useDeleteAd() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => api.delete(`/platform/ads/${id}`).then((r) => r.data),
    onSuccess: () => qc.invalidateQueries({ queryKey: queryKeys.platform.ads() }),
  });
}

export function useUpdateAdmin() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, body }: { id: string; body: { role?: string; is_active?: boolean } }) =>
      api.patch(`/platform/admins/${id}`, body).then((r) => r.data),
    onSuccess: () => qc.invalidateQueries({ queryKey: queryKeys.platform.admins() }),
  });
}

export function useRemoveAdmin() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => api.delete(`/platform/admins/${id}`).then((r) => r.data),
    onSuccess: () => qc.invalidateQueries({ queryKey: queryKeys.platform.admins() }),
  });
}

export function useCreateReferral() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: { notes?: string | null }) =>
      api.post('/platform/referrals', body).then((r) => r.data as { id: string; share_url: string }),
    onSuccess: () => qc.invalidateQueries({ queryKey: queryKeys.platform.referrals() }),
  });
}

export function useDeleteReferral() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => api.delete(`/platform/referrals/${id}`),
    onSuccess: () => qc.invalidateQueries({ queryKey: queryKeys.platform.referrals() }),
  });
}

export function useReviewTopup() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, action }: { id: string; action: 'approve' | 'reject' }) =>
      api.post(`/platform/topups/${id}/${action}`).then((r) => r.data),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: queryKeys.platform.topupsAll() });
      qc.invalidateQueries({ queryKey: queryKeys.platform.tenantsAll() });
      qc.invalidateQueries({ queryKey: queryKeys.platform.tenantDetailAll() });
    },
  });
}

export function usePutSetting() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ key, value, description }: { key: string; value: unknown; description?: string }) =>
      api.put(`/platform/settings/${key}`, { value, description }).then((r) => r.data),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: queryKeys.platform.settings() });
      // Platform billing settings (receiving cards / providers / payment terms)
      // feed the tenant balance top-up modal. Without this, a just-saved card
      // wouldn't appear there until useTenantTopupInfo's staleTime elapsed —
      // which reads as "no receiving card configured" right after adding one.
      qc.invalidateQueries({ queryKey: queryKeys.tenant.topupInfo() });
    },
  });
}
