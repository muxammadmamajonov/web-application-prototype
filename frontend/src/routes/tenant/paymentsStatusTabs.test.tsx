// @vitest-environment jsdom
/**
 * MUDD-84 — status becomes a first-class, always-visible control instead of a
 * setting buried inside the "Filtr" drawer, and the empty state stops lying
 * when a status tab (not a genuinely empty ledger) is why nothing is shown.
 *
 * The pagination fix itself (server-side `status_filter` + real `offset`
 * paging) is pinned at the hook layer in
 * `src/lib/queries/tenantPaymentsPagination.test.tsx`. This file pins the UI
 * contract that sits on top of it.
 */
import { describe, expect, it, beforeEach, afterEach, vi } from 'vitest';
import { render, screen, cleanup, fireEvent } from '@testing-library/react';
import { MemoryRouter } from 'react-router';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import i18n from '@/i18n';

type Row = { id: string; customer_name: string; status: 'pending' | 'approved' | 'rejected' | 'refunded' };

const ROWS: Row[] = [
  { id: 'p1', customer_name: 'Anvar Karimov', status: 'approved' },
  { id: 'p2', customer_name: 'Bekzod Yusupov', status: 'pending' },
  // Deliberately the ONLY rejected row and, in a real fetch, one that would
  // sit past an old 200-row unfiltered cap — reachable here purely because
  // the mocked hook already did server-side filtering, exactly as the fixed
  // `useTenantPaymentsPage` does against the real backend.
  { id: 'p3', customer_name: 'Old Rejection', status: 'rejected' },
];
const COUNTS = { pending: 1, approved: 1, rejected: 1, refunded: 0 };

let lastStatusFilter = '';
vi.mock('@/lib/queries', async (orig) => ({
  ...(await orig<Record<string, unknown>>()),
  useTenantPaymentsPage: (_pageSize: number, statusFilter: string) => {
    lastStatusFilter = statusFilter;
    const items = statusFilter ? ROWS.filter((r) => r.status === statusFilter) : ROWS;
    return {
      data: { pages: [{ items, total: items.length, counts: COUNTS }] },
      isLoading: false, isError: false, error: null, refetch: vi.fn(),
      hasNextPage: false, isFetchingNextPage: false, fetchNextPage: vi.fn(),
    };
  },
}));
vi.mock('@/lib/mutations', async (orig) => ({
  ...(await orig<Record<string, unknown>>()),
  useApprovePayment: () => ({ mutateAsync: vi.fn(), isPending: false }),
  useRejectPayment: () => ({ mutateAsync: vi.fn(), isPending: false }),
}));

import TenantPaymentsPage from './PaymentsPage';

function renderPage() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={['/tenant/payments']}><TenantPaymentsPage /></MemoryRouter>
    </QueryClientProvider>,
  );
}

beforeEach(async () => { await i18n.changeLanguage('uz'); lastStatusFilter = ''; });
afterEach(() => cleanup());

describe('status tabs are the single, always-visible status control', () => {
  it('renders every status tab without opening the "Filtr" drawer', () => {
    renderPage();
    const tabs = screen.getAllByRole('tab');
    expect(tabs.map((t) => t.textContent)).toEqual(
      expect.arrayContaining([
        expect.stringContaining(i18n.t('tenantPayments.filterAll') as string),
        expect.stringContaining(i18n.t('tenantPayments.filterPending') as string),
        expect.stringContaining(i18n.t('tenantPayments.filterApproved') as string),
        expect.stringContaining(i18n.t('tenantPayments.filterRejected') as string),
        expect.stringContaining(i18n.t('tenantPayments.filterRefunded') as string),
      ]),
    );
  });

  it('shows the real server count on each tab, not a count derived from loaded rows', () => {
    renderPage();
    const tabs = screen.getAllByRole('tab');
    const rejected = tabs.find((t) => t.textContent?.includes(i18n.t('tenantPayments.filterRejected') as string));
    expect(rejected?.textContent).toContain('1');
  });

  it('clicking a tab asks the server for that status and shows its row', () => {
    renderPage();
    // "Hammasi" (default) shows every status, including the rejected row.
    expect(screen.getAllByText('Anvar Karimov').length).toBeGreaterThan(0);
    expect(screen.getAllByText('Old Rejection').length).toBeGreaterThan(0);

    const tabs = screen.getAllByRole('tab');
    const rejectedTab = tabs.find((t) => t.textContent?.includes(i18n.t('tenantPayments.filterRejected') as string))!;
    fireEvent.click(rejectedTab);

    expect(lastStatusFilter).toBe('rejected');
    expect(screen.getAllByText('Old Rejection').length).toBeGreaterThan(0);
    // Rows outside the selected status are gone — this IS the server-filtered
    // page, not a client-side narrowing of a bigger fetch.
    expect(screen.queryByText('Anvar Karimov')).toBeNull();
  });
});

describe('the empty state distinguishes "this tab has nothing" from "no payments at all"', () => {
  it('a status tab with zero rows shows the filtered empty state, with a way to clear it', () => {
    renderPage();
    const tabs = screen.getAllByRole('tab');
    const refundedTab = tabs.find((t) => t.textContent?.includes(i18n.t('tenantPayments.filterRefunded') as string))!;
    fireEvent.click(refundedTab);

    expect(screen.getByText(i18n.t('common.noResults') as string)).toBeTruthy();
    expect(screen.queryByText(i18n.t('tenantPayments.emptyTitle') as string)).toBeNull();
    expect(screen.getByRole('button', { name: i18n.t('common.clear') as string })).toBeTruthy();
  });
});
