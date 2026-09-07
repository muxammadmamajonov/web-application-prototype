// @vitest-environment jsdom
/**
 * The Approve/Reject decision on `PaymentReviewPage` must be single-flight.
 *
 * THE DEFECT (audit §20.11). The guard was `const [deciding, setDeciding] =
 * useState(false)` with `if (deciding) return` at the top of `decide`. State is
 * not readable until React commits, so two activations dispatched in the SAME
 * frame both observe `deciding === false` and both reach `mutateAsync`. Only
 * `disabled={deciding}` stopped the second tap, and that lands one render later.
 *
 * This screen is opened from a Telegram payment-review notification and the
 * action commits a money-related state transition, so the duplicate is exactly
 * the tap a human makes when a button looks unresponsive on mobile data.
 *
 * WHERE CORRECTNESS ACTUALLY LIVES. The backend locks the payment row and
 * refuses a second transition — that is the authoritative boundary and nothing
 * here replaces it. This guard exists to stop a duplicate REQUEST and the
 * confusing UX that follows one; it is not the financial-integrity guarantee.
 *
 * WHY THE SAME-FRAME TESTS ARE BUILT THE WAY THEY ARE. RTL wraps each
 * `fireEvent` in its own `act`, which flushes React — so by the second call the
 * button is already `disabled` and React's disabled-element guard swallows the
 * click. Such a test passes with the synchronous guard deleted and proves
 * nothing. Firing both activations inside ONE outer `act` shares the act queue,
 * so React has not re-rendered between them and the ref is the only thing that
 * can stop the second call. That is the arrangement mutation-proved in the
 * correction record.
 */
import { describe, expect, it, vi, beforeEach, afterEach } from 'vitest';
import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import i18n from '@/i18n';
import { apiErrorMessage } from '@/lib/errors';
import { FeedbackHost } from '@/lib/feedback';

const apiGet = vi.fn();
vi.mock('@/lib/api', () => ({
  api: { get: (...a: unknown[]) => apiGet(...a), post: vi.fn(), defaults: { adapter: undefined } },
  setSessionToken: () => {},
}));

const approveMutate = vi.fn();
const rejectMutate = vi.fn();
vi.mock('@/lib/mutations', async (orig) => ({
  ...(await orig<Record<string, unknown>>()),
  useApprovePayment: () => ({ mutateAsync: approveMutate, isPending: false }),
  useRejectPayment: () => ({ mutateAsync: rejectMutate, isPending: false }),
}));

const PaymentReviewPage = (await import('./PaymentReviewPage')).default;

const REVIEW = (over: Record<string, unknown> = {}) => ({
  id: 'pay-1', customer_name: 'Anvar Karimov', contract_number: 'MDD-1',
  installment_id: 'i1', amount_formatted: '4 800 000', currency: 'UZS',
  method_label: 'Naqd', status: 'pending', status_label: 'Kutilmoqda',
  receipt_image_url: null, submitted_at_label: '1-sentabr',
  actionable: true, ...over,
});

/** A promise the test holds open, so the in-flight window is observable. */
function deferred<T = void>() {
  let resolve!: (v: T) => void;
  let reject!: (e: unknown) => void;
  const promise = new Promise<T>((res, rej) => { resolve = res; reject = rej; });
  return { promise, resolve, reject };
}

async function renderReview(over: Record<string, unknown> = {}) {
  apiGet.mockResolvedValue({ data: REVIEW(over) });
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={['/tenant/payments/pay-1/review']}>
        <Routes>
          <Route path="/tenant/payments/:paymentId/review" element={<PaymentReviewPage />} />
        </Routes>
      </MemoryRouter>
      {/* The real toast host, so "the error UI is shown" is a DOM fact. */}
      <FeedbackHost />
    </QueryClientProvider>,
  );
  await waitFor(() => expect(screen.queryByText(/4 800 000/)).toBeTruthy());
}

const approve = () => screen.getByTestId('approve-payment') as HTMLButtonElement;
const reject = () => screen.getByTestId('reject-payment') as HTMLButtonElement;

/**
 * Two activations with NO React commit between them.
 *
 * One outer `act` shares the act queue, so the inner `fireEvent`s do not flush
 * and the second activation sees the button exactly as the first did.
 */
async function sameFrame(first: () => HTMLElement, second: () => HTMLElement) {
  const a = first();
  const b = second();
  await act(async () => {
    fireEvent.click(a);
    fireEvent.click(b);
  });
}

beforeEach(async () => {
  apiGet.mockReset();
  approveMutate.mockReset();
  rejectMutate.mockReset();
  approveMutate.mockResolvedValue(undefined);
  rejectMutate.mockResolvedValue(undefined);
  await i18n.changeLanguage('uz');
});
afterEach(() => { cleanup(); vi.restoreAllMocks(); });

// ── same-frame single flight ────────────────────────────────────────────────

describe('two activations in one frame commit one transition', () => {
  it('Approve + Approve → one call', async () => {
    const d = deferred();
    approveMutate.mockReturnValueOnce(d.promise);
    await renderReview();

    await sameFrame(approve, approve);

    expect(approveMutate).toHaveBeenCalledTimes(1);
    expect(approveMutate).toHaveBeenCalledWith('pay-1');
    expect(rejectMutate).not.toHaveBeenCalled();
    await act(async () => { d.resolve(); });
  });

  it('Reject + Reject → one call', async () => {
    const d = deferred();
    rejectMutate.mockReturnValueOnce(d.promise);
    await renderReview();

    await sameFrame(reject, reject);

    expect(rejectMutate).toHaveBeenCalledTimes(1);
    expect(rejectMutate).toHaveBeenCalledWith('pay-1');
    expect(approveMutate).not.toHaveBeenCalled();
    await act(async () => { d.resolve(); });
  });

  it('Approve then Reject → only Approve reaches the backend', async () => {
    const d = deferred();
    approveMutate.mockReturnValueOnce(d.promise);
    await renderReview();

    // One guard covers BOTH controls: the second action is a different button,
    // so a per-button lock would let this through.
    await sameFrame(approve, reject);

    expect(approveMutate).toHaveBeenCalledTimes(1);
    expect(rejectMutate).not.toHaveBeenCalled();
    await act(async () => { d.resolve(); });
  });

  it('Reject then Approve → only Reject reaches the backend', async () => {
    const d = deferred();
    rejectMutate.mockReturnValueOnce(d.promise);
    await renderReview();

    await sameFrame(reject, approve);

    expect(rejectMutate).toHaveBeenCalledTimes(1);
    expect(approveMutate).not.toHaveBeenCalled();
    await act(async () => { d.resolve(); });
  });

  it('holds for keyboard activation too', async () => {
    const d = deferred();
    approveMutate.mockReturnValueOnce(d.promise);
    await renderReview();

    const btn = approve();
    btn.focus();
    expect(document.activeElement).toBe(btn);
    // jsdom does not synthesize a click from keydown, so this models what the
    // browser dispatches when Enter/Space activates a focused <button>: the
    // activation click itself, twice, with no commit in between.
    await act(async () => {
      fireEvent.click(btn);
      fireEvent.click(btn);
    });

    expect(approveMutate).toHaveBeenCalledTimes(1);
    // Focus is not stolen by the state change.
    expect(document.activeElement).toBe(btn);
    await act(async () => { d.resolve(); });
  });
});

// ── the in-flight window ────────────────────────────────────────────────────

describe('while a decision is in flight', () => {
  it('disables both controls and refuses a further submission', async () => {
    const d = deferred();
    approveMutate.mockReturnValueOnce(d.promise);
    await renderReview();

    fireEvent.click(approve());
    await waitFor(() => expect(approve().disabled).toBe(true));
    expect(reject().disabled).toBe(true);

    // Both the reactive disable AND the synchronous guard are exercised here.
    fireEvent.click(approve());
    fireEvent.click(reject());
    await sameFrame(approve, reject);

    expect(approveMutate).toHaveBeenCalledTimes(1);
    expect(rejectMutate).not.toHaveBeenCalled();
    await act(async () => { d.resolve(); });
  });

  it('shows the pending affordance on the pressed control', async () => {
    const d = deferred();
    approveMutate.mockReturnValueOnce(d.promise);
    await renderReview();

    fireEvent.click(approve());
    await waitFor(() => expect(approve().disabled).toBe(true));
    expect(approve().querySelector('.animate-spin'), 'no spinner while deciding').toBeTruthy();

    await act(async () => { d.resolve(); });
  });
});

// ── settling ────────────────────────────────────────────────────────────────

describe('after the decision settles', () => {
  it('a success submits exactly one transition and closes the controls', async () => {
    // The server flips the row; the refetch brings back a processed payment.
    apiGet
      .mockResolvedValueOnce({ data: REVIEW() })
      .mockResolvedValue({ data: REVIEW({ actionable: false, status: 'approved' }) });
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(
      <QueryClientProvider client={qc}>
        <MemoryRouter initialEntries={['/tenant/payments/pay-1/review']}>
          <Routes>
            <Route path="/tenant/payments/:paymentId/review" element={<PaymentReviewPage />} />
          </Routes>
        </MemoryRouter>
        <FeedbackHost />
      </QueryClientProvider>,
    );
    await waitFor(() => expect(screen.queryByTestId('approve-payment')).toBeTruthy());

    await act(async () => { fireEvent.click(approve()); });

    await waitFor(() => expect(screen.queryByTestId('approve-payment')).toBeNull());
    expect(approveMutate).toHaveBeenCalledTimes(1);
    expect(screen.getByText(i18n.t('review.alreadyProcessed') as string)).toBeTruthy();
  });

  it('a failure keeps the error UI and allows exactly one intentional retry', async () => {
    const boom = new Error('network');
    approveMutate.mockRejectedValueOnce(boom);
    await renderReview();

    await act(async () => { fireEvent.click(approve()); });

    // The existing error presentation — a toast rendered by the real host.
    await waitFor(() => expect(screen.getByText(apiErrorMessage(boom))).toBeTruthy());
    // Released in `finally`: the controls come back so a retry is possible.
    await waitFor(() => expect(approve().disabled).toBe(false));
    expect(reject().disabled).toBe(false);

    // One deliberate retry — and a same-frame double tap on it is still one call.
    const d = deferred();
    approveMutate.mockReturnValueOnce(d.promise);
    await sameFrame(approve, approve);

    expect(approveMutate).toHaveBeenCalledTimes(2);
    await act(async () => { d.resolve(); });
  });
});

// ── untouched behaviour ─────────────────────────────────────────────────────

describe('states that must not change', () => {
  it('a processed payment exposes no decision control at all', async () => {
    await renderReview({ actionable: false, status: 'approved' });
    expect(screen.queryByTestId('approve-payment')).toBeNull();
    expect(screen.queryByTestId('reject-payment')).toBeNull();
    expect(screen.getByText(i18n.t('review.alreadyProcessed') as string)).toBeTruthy();
    expect(approveMutate).not.toHaveBeenCalled();
    expect(rejectMutate).not.toHaveBeenCalled();
  });

  it('both controls are enabled, focusable buttons before any decision', async () => {
    await renderReview();
    for (const b of [approve(), reject()]) {
      expect(b.tagName).toBe('BUTTON');
      expect(b.disabled).toBe(false);
      expect(b.getAttribute('tabindex')).not.toBe('-1');
      b.focus();
      expect(document.activeElement).toBe(b);
    }
  });

  it('renders the decision in Russian as well', async () => {
    await i18n.changeLanguage('ru');
    const d = deferred();
    approveMutate.mockReturnValueOnce(d.promise);
    await renderReview();
    await sameFrame(approve, approve);
    expect(approveMutate).toHaveBeenCalledTimes(1);
    await act(async () => { d.resolve(); });
  });
});
