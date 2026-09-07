// @vitest-environment jsdom
/**
 * H16 — a failed fetch backing either Overview panel must not render as a
 * genuinely quiet day.
 *
 * The income chart derived `hasIncome` from `payments.data`, and the recent
 * payments list checked only `items.length === 0` — neither branch looked at
 * the query's `isError`. So when the request failed, the dashboard showed
 * "no income today" / "no recent payments" exactly as it would on a real quiet
 * day, hiding a backend outage from the shop owner instead of surfacing it.
 *
 * (MUDD-140) The income chart now reads `useReportCashflow` (the authoritative,
 * status-filtered aggregate) instead of summing raw `payments` rows in the
 * browser, so its error/empty states are pinned against `CASHFLOW`, not
 * `PAYMENTS` — the recent-payments list below is unaffected and still reads
 * `payments` directly.
 *
 * The correct pattern already lives one section down on the same page
 * (`worklistQ.isError` → `ErrorCard` + retry) and in staff/TodayPage.tsx
 * (#321, `payments.isError` → `ErrorCard` + retry). This pins the same
 * distinction for both of the tenant Overview's affected sections.
 */
import { describe, expect, it, beforeEach, afterEach, vi } from 'vitest';
import { render, screen, cleanup } from '@testing-library/react';
import { MemoryRouter } from 'react-router';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import i18n from '@/i18n';
import { localToday } from '@/lib/format';

type Q<T> = { data: T | undefined; isLoading: boolean; isError: boolean; error: unknown; refetch: () => void };
const ok = <T,>(data: T): Q<T> => ({ data, isLoading: false, isError: false, error: null, refetch: vi.fn() });
// Shaped like the axios error `errorKind()`/`isRetryable()` expect — a 500 so
// the ErrorCard actually offers a Retry button, matching a real backend outage.
const failed = <T,>(): Q<T> => ({ data: undefined, isLoading: false, isError: true, error: { response: { status: 500 } }, refetch: vi.fn() });

let PAYMENTS: Q<{ items: unknown[]; total: number }>;
let CASHFLOW: Q<{ series: { period: string; count: number; amount: number }[]; total: number; total_by_currency: Record<string, number> }>;

const OVERVIEW = {
  tenant: { name: 'Shop' },
  debt_total_uzs: 0, overdue_uzs: 0, due_today_uzs: 0, pending_receipts: 0,
  installments_by_status: { active: 0, overdue: 0, completed: 0 },
};

const EMPTY_CASHFLOW = { series: [], total: 0, total_by_currency: {} };

vi.mock('@/lib/queries', async (importOriginal) => ({
  ...(await importOriginal<Record<string, unknown>>()),
  useTenantOverview: () => ok(OVERVIEW),
  useTenantPayments: () => PAYMENTS,
  // (MUDD-140) The income chart now reads the authoritative cashflow
  // aggregate, not `useTenantPayments`, so its own error/empty states need
  // their own mock independent of the payments list below.
  useReportCashflow: () => CASHFLOW,
  useTenantWorklist: () => ok({ items: [], total: 0, as_of: null, as_of_date: null }),
  // The InstallmentModal/InstallmentDetailModal that Overview always mounts
  // (regardless of open state) call these on every render — stub them so the
  // hidden modals don't attempt a real, unmocked network fetch.
  useTenantCustomers: () => ok({ items: [], total: 0 }),
  useTenantGuarantors: () => ok([]),
  useTenantSettings: () => ok({ calc: {} }),
}));

import TenantOverviewPage from './OverviewPage';

function renderOverview() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={['/uz/tenant/overview']}><TenantOverviewPage /></MemoryRouter>
    </QueryClientProvider>,
  );
}

beforeEach(async () => {
  await i18n.changeLanguage('uz');
  if (!Element.prototype.scrollIntoView) Element.prototype.scrollIntoView = () => {};
  vi.spyOn(console, 'error').mockImplementation(() => {});
  // Default for the 'recent payments list' tests below, which exercise
  // `PAYMENTS` and don't care about the income chart's own aggregate.
  CASHFLOW = ok(EMPTY_CASHFLOW);
});
afterEach(() => { cleanup(); vi.restoreAllMocks(); });

describe('income chart', () => {
  it('shows a real zero-income empty state when the cashflow aggregate genuinely has none', () => {
    PAYMENTS = ok({ items: [], total: 0 });
    CASHFLOW = ok(EMPTY_CASHFLOW);
    renderOverview();
    expect(screen.getByText(i18n.t('tenantOverview.incomeChartEmptyTitle'))).toBeTruthy();
  });

  it('shows an error card, NOT the quiet-day empty state, when the cashflow aggregate fails', () => {
    PAYMENTS = ok({ items: [], total: 0 });
    CASHFLOW = failed();
    renderOverview();
    expect(screen.queryByText(i18n.t('tenantOverview.incomeChartEmptyTitle'))).toBeNull();
    expect(screen.getAllByRole('button', { name: i18n.t('common.retry') }).length).toBeGreaterThan(0);
  });

  // (MUDD-140) The 14-day chart used to sum whatever `/tenant/payments` (any
  // status, newest 200 rows) returned. A pending payment sitting in that raw
  // list must no longer move the chart at all — only the server aggregate
  // does, and it already excludes pending/rejected/refunded.
  it('renders from the cashflow aggregate, not from raw payment rows', () => {
    PAYMENTS = ok({
      items: [{ id: 'p1', amount: 999_999_999, currency: 'UZS', created_at: new Date().toISOString(), status: 'pending' }],
      total: 1,
    });
    CASHFLOW = ok({
      series: [{ period: localToday(), count: 1, amount: 42 }],
      total: 42,
      total_by_currency: { UZS: 42 },
    });
    renderOverview();
    // The aggregate reports real income for today, so neither the zero-income
    // empty state nor an error card should appear — the pending row above,
    // which the old client-side sum would have counted, is irrelevant here.
    expect(screen.queryByText(i18n.t('tenantOverview.incomeChartEmptyTitle'))).toBeNull();
    expect(screen.queryAllByRole('button', { name: i18n.t('common.retry') }).length).toBe(0);
  });
});

describe('recent payments list', () => {
  it('shows a real empty state when payments genuinely has none', () => {
    PAYMENTS = ok({ items: [], total: 0 });
    renderOverview();
    expect(screen.getByText(i18n.t('tenantOverview.noRecentPayments'))).toBeTruthy();
  });

  it('shows an error card, NOT the quiet-day empty state, when payments fails', () => {
    PAYMENTS = failed();
    renderOverview();
    expect(screen.queryByText(i18n.t('tenantOverview.noRecentPayments'))).toBeNull();
    expect(screen.getAllByRole('button', { name: i18n.t('common.retry') }).length).toBeGreaterThan(0);
  });
});
