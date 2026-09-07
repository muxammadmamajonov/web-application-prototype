/** Customer Mini-App reads (`/me/*`). */

import { keepPreviousData, useQuery } from '@tanstack/react-query';
import { api } from '../api';
import { queryKeys } from '../queryKeys';

export interface NotificationRow {
  id: string; kind: string; title: string; body: string | null;
  link: string | null; read: boolean; created_at: string;
}
export function useMyNotifications() {
  return useQuery({
    queryKey: queryKeys.me.notifications(),
    queryFn: async ({ signal }) => (await api.get<{ unread: number; items: NotificationRow[] }>('/me/notifications', { signal })).data,
    staleTime: 30_000,
  });
}

// ── Customer (Mini-App) /me/* queries — Round 21.1 ────────────────────────

export interface MyInstallmentHeadline {
  kind: 'upcoming' | 'due_today' | 'overdue';
  days?: number;
  due_date?: string;
  amount?: string;
}

export interface MyInstallmentRow {
  id: string;
  tenant_id: string;
  tenant_name: string;
  tenant_logo: string | null;
  currency: string;
  contract_number: string | null;
  reference: string | null;
  item_summary: string;
  principal: string;
  paid_amount: string;
  remaining: string;
  monthly_amount: string;
  months: number;
  paid_count: number;
  status: 'draft' | 'active' | 'overdue' | 'completed' | 'cancelled';
  next_due_date: string | null;
  headline: MyInstallmentHeadline | null;
  start_date: string | null;
}

interface MySummary {
  /** The DOMINANT currency (most contracts) that `total_outstanding` /
   *  `due_this_month` / `due_today` are denominated in — this person may hold
   *  contracts at other shops in other currencies too; see the `_by_currency`
   *  maps below for those. */
  currency?: string;
  total_outstanding: string;
  due_this_month: string;
  due_today: string;
  /** Per-currency breakdowns of the three scalars above, spanning every shop
   *  this person has bought from. Optional so an older cached response still
   *  renders. A currency with nothing owed has no key (zero buckets are
   *  dropped server-side), so `Object.keys(...).length > 0` is "owes
   *  something in some currency" without comparing amounts across currencies. */
  total_outstanding_by_currency?: Record<string, string>;
  due_this_month_by_currency?: Record<string, string>;
  due_today_by_currency?: Record<string, string>;
  overdue_count: number;
  installments_count: number;
  next_due_date: string | null;
  next_due_amount: string;
  next_due_currency: string | null;
  /** True when `next_due_date` has already passed. Sent by the server rather
   *  than derived here: the home screen used to infer "nothing is due" from a
   *  null date and told late customers they were all paid up. Optional so an
   *  older cached response still renders. */
  next_due_overdue?: boolean;
}

/** The SERVER's view of this person's phone verification.
 *
 *  Deliberately not cached for long and always refetched on mount: the fact it
 *  reports changes out of band, when a bot update lands, not in response to
 *  anything this app did. */
export interface MyIdentity {
  state: string;
  verified: boolean;
  phoneMasked: string | null;
  eligibility: string | null;
  source: string | null;
  verifiedAt: string | null;
  displayName: string | null;
  linkedShops: number;
}
export function useMyIdentity() {
  return useQuery({
    queryKey: queryKeys.me.identity(),
    queryFn: async ({ signal }) => (await api.get<MyIdentity>('/me/identity', { signal })).data,
    staleTime: 0,
  });
}

export function useMySummary() {
  return useQuery({
    queryKey: queryKeys.me.summary(),
    queryFn: async ({ signal }) => (await api.get<MySummary>('/me/summary', { signal })).data,
    staleTime: 30_000,
  });
}

export function useMyInstallments(filter: 'open' | 'completed' | 'all' = 'open') {
  return useQuery({
    queryKey: queryKeys.me.installments(filter),
    placeholderData: keepPreviousData,  // (#621)
    queryFn: async ({ signal }) =>
      (await api.get<MyInstallmentRow[]>('/me/installments', { params: { status_filter: filter }, signal })).data,
    staleTime: 30_000,
  });
}

interface MyInstallmentDetail {
  id: string;
  currency: string;
  tenant: {
    id: string; name: string; logo: string | null; slug: string;
    address?: string | null; phone?: string | null; telegram?: string | null;
    hours?: { start?: string; end?: string } | null;
  };
  contract_number: string | null;
  reference: string | null;
  status: 'draft' | 'active' | 'overdue' | 'completed' | 'cancelled';
  principal: string;
  paid_amount: string;
  remaining: string;
  monthly_amount: string;
  total_amount: string;
  down_payment: string;
  months: number;
  paid_count: number;
  start_date: string | null;
  next_due_date: string | null;
  headline: MyInstallmentHeadline | null;
  items: Array<{
    name: string; variant: string | null; serial: string | null;
    qty: number; unit_price: string; line_total: string;
  }>;
  schedule: Array<{
    n: number; due_date: string | null; amount: string;
    status: 'paid' | 'next' | 'future';
  }>;
  payments: Array<{
    id: string; amount: string; method: string; status: string;
    paid_at: string | null; external_ref: string | null;
    receipt_image_url: string | null;
  }>;
}

export function useMyInstallment(id: string | undefined) {
  return useQuery({
    queryKey: queryKeys.me.installment(id),
    queryFn: async ({ signal }) => (await api.get<MyInstallmentDetail>(`/me/installments/${id}`, { signal })).data,
    enabled: !!id,
    staleTime: 15_000,
  });
}
