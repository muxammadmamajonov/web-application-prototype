// @vitest-environment jsdom
import { render, screen } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

const retryToday = vi.fn();

vi.mock('@/lib/queries', () => ({
  useStaffToday: () => ({
    data: undefined, isLoading: false, isError: true,
    error: new Error('today failed'), refetch: retryToday,
  }),
  useTenantPayments: () => ({
    data: { items: [] }, isLoading: false, isError: false,
  }),
  useTenantWorklistPages: () => ({
    data: { pages: [{ items: [], total: 0 }] }, isLoading: false, isError: false,
    hasNextPage: false, isFetchingNextPage: false,
  }),
}));
vi.mock('@/lib/currency', () => ({ useCurrency: () => 'UZS' }));
vi.mock('@/components/ui/ErrorCard', () => ({
  ErrorCard: ({ onRetry }: { onRetry: () => void }) => (
    <button data-testid="today-error" onClick={onRetry}>retry</button>
  ),
}));
vi.mock('../tenant/_modals/InstallmentModal', () => ({ InstallmentModal: () => null }));
vi.mock('../tenant/InstallmentsPage', () => ({ InstallmentDetailModal: () => null }));

import StaffTodayPage from './TodayPage';

describe('staff Today primary summary failure', () => {
  beforeEach(() => retryToday.mockClear());

  it('shows an explicit retry state instead of fabricated zero KPIs', () => {
    render(<StaffTodayPage />);
    const retry = screen.getByTestId('today-error');
    expect(retry).toBeTruthy();
    retry.click();
    expect(retryToday).toHaveBeenCalledTimes(1);
  });
});
