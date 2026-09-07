// @vitest-environment jsdom
/**
 * H14 — the platform top-up review queue's Approve/Reject guard.
 *
 * THE DEFECT. `TopupReviewSection` guarded a double-tap with
 * `const [acting, setActing] = useState(false)` and `if (acting) return` —
 * placed AFTER `await confirmDialog(...)` had already resolved. That is late
 * twice over: `useState` is not readable until React commits, AND the check
 * itself only ran once a whole async gap had already passed. Two activations
 * dispatched in the same frame both reach `confirmDialog` before either can
 * be stopped. Approving a top-up credits a tenant's real platform balance —
 * this is the same class of bug `paymentReviewDecisionGuard.test.tsx` pins on
 * `PaymentReviewPage`, on the surface that was never fixed alongside it.
 *
 * THE FIX. `act` now runs inside `useSubmitGuard().run`, which flips a ref
 * synchronously before the first `await` — including before `confirmDialog`
 * — so a re-entrant activation is dropped before it can open a second dialog.
 *
 * WHY `confirmDialog` CALL COUNT IS THE ASSERTION, NOT `review.mutateAsync`
 * CALL COUNT. `confirmDialog` is a module-level singleton
 * (`src/lib/feedback.tsx`): a second call before the first resolves silently
 * OVERWRITES the pending request, orphaning the first caller's promise
 * forever. A same-frame double-tap that reaches `confirmDialog` twice will
 * not reliably double-fire `review.mutateAsync` in a test that only ever
 * confirms once — the orphaned first call just hangs — but it is still the
 * exact defect: a live app leaves one call permanently stuck, and a second,
 * later confirmDialog can still walk a second real POST through. Asserting
 * `confirmDialog` call count catches the defect at its root — "the guard
 * must run before the async gap, not after" — matching the technique
 * `recordPaymentGuard.test.tsx` uses for the sibling H13 fix.
 */
import { describe, expect, it, vi, beforeEach, afterEach } from 'vitest';
import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { MemoryRouter } from 'react-router';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import i18n from '@/i18n';

const reviewMutate = vi.fn();
vi.mock('@/lib/mutations', async (orig) => ({
  ...(await orig<Record<string, unknown>>()),
  useReviewTopup: () => ({ mutateAsync: reviewMutate }),
}));

const confirmDialogMock = vi.fn();
const toastSuccess = vi.fn();
vi.mock('@/lib/feedback', async (orig) => ({
  ...(await orig<Record<string, unknown>>()),
  confirmDialog: (...args: unknown[]) => confirmDialogMock(...args),
  toast: { success: (...a: unknown[]) => toastSuccess(...a), error: vi.fn(), warning: vi.fn(), info: vi.fn() },
}));

const TOPUP = {
  id: 'tp-1', tenant_id: 't1', tenant_name: "Tomdi savdo",
  amount: 500_000, method: 'card' as const, status: 'pending' as const,
  receipt_url: null, created_at: '2026-08-01T10:00:00Z',
  dest_card_label: null, dest_card_id: null, note: null,
  created_by_name: null, reviewed_by_name: null, reviewed_at: null,
};

vi.mock('@/lib/queries', async (orig) => ({
  ...(await orig<Record<string, unknown>>()),
  usePlatformTopups: () => ({ data: [TOPUP], isLoading: false, isError: false, refetch: vi.fn() }),
  usePlatformPayments: () => ({ data: { items: [] }, isLoading: false, isError: false, refetch: vi.fn() }),
}));

const PlatformPaymentsPage = (await import('./PaymentsPage')).default;

/** A promise the test holds open, so the in-flight window is observable. */
function deferred<T = void>() {
  let resolve!: (v: T) => void;
  const promise = new Promise<T>((res) => { resolve = res; });
  return { promise, resolve };
}

async function renderPage() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={['/uz/platform/payments']}>
        <PlatformPaymentsPage />
      </MemoryRouter>
    </QueryClientProvider>,
  );
  await waitFor(() => expect(screen.queryByText('Tomdi savdo')).toBeTruthy());
}

/** Opens the row's detail drawer, which carries the Approve/Reject footer. */
async function openDetail() {
  fireEvent.click(screen.getByRole('button', { name: /Tomdi savdo/ }));
  await waitFor(() => expect(screen.queryByTestId('drawer-footer')).toBeTruthy());
}

const approveBtn = () =>
  within(screen.getByTestId('drawer-footer'))
    .getByRole('button', { name: new RegExp(i18n.t('common.approve') as string) });

/** Two activations with NO React commit between them — see file header. */
async function sameFrame(getBtn: () => HTMLElement) {
  const btn = getBtn();
  await act(async () => {
    fireEvent.click(btn);
    fireEvent.click(btn);
  });
}

beforeEach(async () => {
  reviewMutate.mockReset();
  confirmDialogMock.mockReset();
  toastSuccess.mockReset();
  reviewMutate.mockResolvedValue(undefined);
  await i18n.changeLanguage('uz');
});
afterEach(() => { cleanup(); vi.restoreAllMocks(); });

describe('two Approve activations in one frame', () => {
  it('open confirmDialog exactly once, not twice', async () => {
    const d = deferred<boolean>();
    confirmDialogMock.mockReturnValue(d.promise);
    await renderPage();
    await openDetail();

    await sameFrame(approveBtn);

    // THE DEFECT would call confirmDialog once per activation (2). The guard
    // must drop the second activation before it ever reaches confirmDialog.
    expect(confirmDialogMock).toHaveBeenCalledTimes(1);

    await act(async () => { d.resolve(true); });
    await waitFor(() => expect(reviewMutate).toHaveBeenCalledTimes(1));
    expect(reviewMutate).toHaveBeenCalledWith({ id: 'tp-1', action: 'approve' });
  });

  it('credit the tenant exactly once', async () => {
    confirmDialogMock.mockResolvedValue(true);
    await renderPage();
    await openDetail();

    await sameFrame(approveBtn);

    await waitFor(() => expect(reviewMutate).toHaveBeenCalledTimes(1));
  });
});

describe('while a decision is in flight', () => {
  it('disables both controls', async () => {
    const d = deferred<boolean>();
    confirmDialogMock.mockReturnValue(d.promise);
    await renderPage();
    await openDetail();

    fireEvent.click(approveBtn());
    await waitFor(() => expect(approveBtn().hasAttribute('disabled')).toBe(true));
    const footer = screen.getByTestId('drawer-footer');
    const rejectBtn = within(footer).getByRole('button', { name: new RegExp(i18n.t('common.reject') as string) });
    expect(rejectBtn.hasAttribute('disabled')).toBe(true);

    await act(async () => { d.resolve(true); });
    await waitFor(() => expect(reviewMutate).toHaveBeenCalledTimes(1));
  });
});
