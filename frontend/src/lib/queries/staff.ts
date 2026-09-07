/** Sales-staff reads. The staff persona otherwise shares the tenant queries. */

import { useQuery } from '@tanstack/react-query';
import { api } from '../api';
import { queryKeys } from '../queryKeys';
import type { MoneyByCurrency } from './tenant';

export interface StaffTodaySummary {
  /** (MUDD-114) The currency `received_today` is denominated in — the
   *  tenant's primary currency, same convention as `TenantOverview`. */
  currency?: string;
  received_today: number;
  /** The part of today's collections NOT covered by `received_today` — a
   *  salesperson working a multi-currency shop reconciles a cash drawer
   *  against this, so a second currency must be stated, never folded in. */
  received_today_by_currency?: MoneyByCurrency;
  pending_count: number;
  new_customers_today: number;
}
export function useStaffToday() {
  return useQuery({
    queryKey: queryKeys.tenant.staffToday(),
    queryFn: async ({ signal }) => (await api.get<StaffTodaySummary>('/tenant/staff-today', { signal })).data,
    staleTime: 30_000,
  });
}
