// @vitest-environment jsdom
/**
 * MUDD-147 — `LifecycleSection`'s reschedule and cancel buttons both fed
 * `Button`'s `loading` prop something other than a plain mutation-pending
 * flag:
 *
 *   - Reschedule: `loading={reschedule.isPending || (!addMonths && !newDue)}`
 *     — the second half is pure form validation (nothing to change yet), not
 *     a request in flight, so it read as permanently `aria-busy="true"` the
 *     moment the reschedule form opened with its defaults.
 *   - Both reschedule and cancel additionally rendered a SECOND, manual
 *     `<Loader2/>` in their children on top of `Button`'s own spinner, so a
 *     real pending mutation showed two spinners instead of one.
 */
import { describe, expect, it, vi, beforeEach, afterEach } from 'vitest';
import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import '@testing-library/jest-dom/vitest';
import { MemoryRouter } from 'react-router';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import i18n from '@/i18n';
import type { InstallmentDetail } from '@/lib/queries';

vi.mock('@/lib/feedback', async (orig) => ({
  ...(await orig<Record<string, unknown>>()),
  confirmDialog: async () => true,
  toast: { success: vi.fn(), error: vi.fn(), warning: vi.fn(), info: vi.fn() },
}));

vi.mock('@/store/session', () => ({
  useSession: (sel: (s: unknown) => unknown) => sel({ activeRole: () => 'owner' }),
}));

const DETAIL: InstallmentDetail = {
  id: 'i1', reference: 'R1000', contract_number: 'MDD-2026-0001',
  customer_id: 'cu1', customer_name: 'Aziz Karimov', customer_phone: null,
  status: 'active', total_amount: 6_000_000, down_payment: 0, principal: 6_000_000,
  months: 6, monthly_amount: 1_000_000, paid_count: 1, paid_amount: 1_000_000,
  start_date: '2026-01-05', next_due_date: '2026-08-05',
  calc_formula: 'none', markup_pct: null, interest_rate: null,
  guarantors: [], items: [], payments: [],
};

vi.mock('@/lib/queries', async (orig) => ({
  ...(await orig<Record<string, unknown>>()),
  useTenantInstallmentDetail: () => ({ data: DETAIL, isLoading: false }),
}));

let rescheduleIsPending = false;
let cancelIsPending = false;
const rescheduleMutateAsync = vi.fn(async () => ({}));
const cancelMutateAsync = vi.fn(async () => ({}));

vi.mock('@/lib/mutations', async (orig) => ({
  ...(await orig<Record<string, unknown>>()),
  useRescheduleInstallment: () => ({ mutateAsync: rescheduleMutateAsync, isPending: rescheduleIsPending }),
  useCancelInstallment: () => ({ mutateAsync: cancelMutateAsync, isPending: cancelIsPending }),
  useRecordTenantPayment: () => ({ mutateAsync: vi.fn(), isPending: false }),
}));

const { InstallmentDetailModal } = await import('./InstallmentsPage');

function renderDetail() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={['/uz/tenant/installments']}>
        <InstallmentDetailModal id="i1" onClose={() => {}} />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

const rescheduleOpenBtn = () => screen.getByRole('button', { name: /qayta rejalashtirish/i });
const cancelOpenBtn = () => screen.getByRole('button', { name: /bekor qilish/i });
const rescheduleSaveBtn = () => screen.getByRole('button', { name: /saqlash/i }) as HTMLButtonElement;
// Two buttons read "Bekor qilish" once the cancel form is open — the danger
// submit button (`installments.cancel`) and the outline "go back" button
// (`common.cancel`, same uz string). The submit button renders first.
const cancelSaveBtn = () => screen.getAllByRole('button', { name: /bekor qilish/i })[0] as HTMLButtonElement;

beforeEach(async () => {
  rescheduleIsPending = false;
  cancelIsPending = false;
  vi.clearAllMocks();
  await i18n.changeLanguage('uz');
});
afterEach(cleanup);

describe('reschedule form — validation is disabled, not busy', () => {
  it('the Save button is disabled but not aria-busy with the untouched defaults', () => {
    renderDetail();
    fireEvent.click(rescheduleOpenBtn());
    // addMonths=0, newDue='' by default: nothing to change yet, no mutation
    // is running — this must be plain `disabled`, not a fake busy state.
    expect(rescheduleSaveBtn().disabled).toBe(true);
    expect(rescheduleSaveBtn()).not.toHaveAttribute('aria-busy', 'true');
  });

  it('enables once a change is entered, still without a spurious busy state', () => {
    renderDetail();
    fireEvent.click(rescheduleOpenBtn());
    fireEvent.change(screen.getByLabelText(/oy qo.shish/i), { target: { value: '3' } });
    expect(rescheduleSaveBtn().disabled).toBe(false);
    expect(rescheduleSaveBtn()).not.toHaveAttribute('aria-busy', 'true');
  });

  it('shows exactly one spinner while the mutation is actually pending', () => {
    rescheduleIsPending = true;
    renderDetail();
    fireEvent.click(rescheduleOpenBtn());
    const btn = rescheduleSaveBtn();
    expect(btn).toHaveAttribute('aria-busy', 'true');
    expect(btn.querySelectorAll('.animate-spin').length).toBe(1);
  });
});

describe('cancel form — duplicate spinner', () => {
  it('shows exactly one spinner while the cancel mutation is pending', () => {
    cancelIsPending = true;
    renderDetail();
    fireEvent.click(cancelOpenBtn());
    const btn = cancelSaveBtn();
    expect(btn).toHaveAttribute('aria-busy', 'true');
    expect(btn.querySelectorAll('.animate-spin').length).toBe(1);
  });
});
