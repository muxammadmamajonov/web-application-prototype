// @vitest-environment jsdom
/**
 * H13 — `InstallmentsPage`'s `RecordPaymentSection` had NO double-submit guard
 * at all, and `useRecordTenantPayment` sent no `Idempotency-Key`.
 *
 * THE DEFECT. `submit` was guarded only by `record.isPending` — react-query
 * mutation state, which only flips `true` once `mutateAsync` has already
 * started. The entire `confirmDialog` await (and the render gap right after a
 * fast tap, before that `disabled` prop even commits) was completely
 * unguarded: two activations dispatched in the same frame both reach
 * `confirmDialog` before either can be stopped. `useCreateInstallment`
 * (lib/mutations/tenant.ts) sat a few lines above with the correct backstop —
 * an optional `Idempotency-Key` header — that `useRecordTenantPayment` never
 * had, so even a request that DID double-fire had no server-side no-op to
 * fall back on.
 *
 * THE FIX. `submit` now runs inside `useSubmitGuard().run`, which flips a ref
 * synchronously before the first `await` — including before `confirmDialog`
 * — so a re-entrant call is dropped before it can open a second dialog. A
 * per-session idempotency key travels on every `record.mutateAsync` call and
 * is sent as the `Idempotency-Key` header, mirroring `useCreateInstallment`.
 *
 * WHY THE SAME-FRAME TECHNIQUE. RTL wraps each `fireEvent` in its own `act`,
 * which flushes React — so by a second *separate* call the button is already
 * disabled and the DOM's native disabled-element guard swallows the click,
 * proving nothing about the synchronous ref. Firing both activations inside
 * ONE outer `act` shares the act queue, so React has not re-rendered between
 * them (see `paymentReviewDecisionGuard.test.tsx` for the same pattern on
 * `PaymentReviewPage`).
 *
 * WHY `confirmDialog` CALL COUNT IS THE ASSERTION, NOT `api.post` CALL COUNT.
 * `confirmDialog` is a module-level singleton (`src/lib/feedback.tsx`): a
 * second call before the first resolves silently OVERWRITES the pending
 * request, orphaning the first caller's promise forever. That means a
 * same-frame double-tap that reaches `confirmDialog` twice does NOT reliably
 * double-fire `api.post` in a test that only ever confirms once — the
 * orphaned first call just hangs. It is still the exact bug: a live app
 * leaves one call permanently stuck, and a user who dismisses/retries can
 * still walk a second confirmDialog into a second real POST. Asserting
 * `confirmDialog` call count catches the defect at its root — "the guard
 * must run before the async gap, not after" — instead of only catching one
 * of its possible downstream manifestations.
 */
import { describe, expect, it, vi, beforeEach, afterEach } from 'vitest';
import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import i18n from '@/i18n';
import type { InstallmentDetail } from '@/lib/queries';

const apiPost = vi.fn();
vi.mock('@/lib/api', () => ({
  api: {
    get: vi.fn(),
    post: (...a: unknown[]) => apiPost(...a),
    patch: vi.fn(), put: vi.fn(), delete: vi.fn(),
    defaults: { adapter: undefined },
  },
  setSessionToken: () => {},
}));

const confirmDialogMock = vi.fn();
const toastSuccess = vi.fn();
const toastError = vi.fn();
vi.mock('@/lib/feedback', async (orig) => ({
  ...(await orig<Record<string, unknown>>()),
  confirmDialog: (...args: unknown[]) => confirmDialogMock(...args),
  toast: {
    success: (...a: unknown[]) => toastSuccess(...a),
    error: (...a: unknown[]) => toastError(...a),
    warning: vi.fn(), info: vi.fn(),
  },
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

const { InstallmentDetailModal } = await import('./InstallmentsPage');

/** A promise the test holds open, so the in-flight window is observable. */
function deferred<T = void>() {
  let resolve!: (v: T) => void;
  const promise = new Promise<T>((res) => { resolve = res; });
  return { promise, resolve };
}

function renderDetail() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={['/uz/tenant/installments']}>
        <InstallmentDetailModal id="i1" onClose={() => {}} openPayment />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

const submitBtn = () =>
  screen.getByRole('button', { name: new RegExp(i18n.t('installments.recordApprove') as string) });

/** Two activations with NO React commit between them — see file header. */
async function sameFrame(getBtn: () => HTMLElement) {
  const btn = getBtn();
  await act(async () => {
    fireEvent.click(btn);
    fireEvent.click(btn);
  });
}

beforeEach(async () => {
  apiPost.mockReset();
  confirmDialogMock.mockReset();
  toastSuccess.mockReset();
  toastError.mockReset();
  apiPost.mockResolvedValue({ data: { id: 'p1', installment_id: 'i1' } });
  await i18n.changeLanguage('uz');
});
afterEach(() => { cleanup(); vi.restoreAllMocks(); });

describe('two record-payment activations in one frame', () => {
  it('open confirmDialog exactly once, not twice', async () => {
    const d = deferred<boolean>();
    confirmDialogMock.mockReturnValue(d.promise);
    renderDetail();

    await sameFrame(submitBtn);

    // THE DEFECT would call confirmDialog once per activation (2). The guard
    // must drop the second activation before it ever reaches confirmDialog.
    expect(confirmDialogMock).toHaveBeenCalledTimes(1);

    await act(async () => { d.resolve(true); });
    await waitFor(() => expect(apiPost).toHaveBeenCalledTimes(1));
  });

  it('commit exactly one record-payment request to the server', async () => {
    confirmDialogMock.mockResolvedValue(true);
    renderDetail();

    await sameFrame(submitBtn);

    await waitFor(() => expect(apiPost).toHaveBeenCalledTimes(1));
    expect(apiPost).toHaveBeenCalledWith(
      '/tenant/payments',
      expect.objectContaining({ installment_id: 'i1', amount: 1_000_000 }),
      expect.anything(),
    );
  });
});

describe('while a submission is in flight', () => {
  it('a further activation is dropped before it can open a second dialog', async () => {
    const dialogGate = deferred<boolean>();
    confirmDialogMock.mockReturnValueOnce(dialogGate.promise);
    renderDetail();

    fireEvent.click(submitBtn());
    await waitFor(() => expect(confirmDialogMock).toHaveBeenCalledTimes(1));

    // A second tap while still waiting on the (unresolved) confirm dialog must
    // not open a second one — the ref is held for the whole `run()` duration,
    // not just up to the point `confirmDialog` happens to be called.
    fireEvent.click(submitBtn());
    expect(confirmDialogMock).toHaveBeenCalledTimes(1);

    await act(async () => { dialogGate.resolve(true); });
    await waitFor(() => expect(apiPost).toHaveBeenCalledTimes(1));
  });
});

describe('the Idempotency-Key header', () => {
  it('is sent on every record-payment request', async () => {
    confirmDialogMock.mockResolvedValue(true);
    renderDetail();

    fireEvent.click(submitBtn());
    await waitFor(() => expect(apiPost).toHaveBeenCalledTimes(1));

    const [, , config] = apiPost.mock.calls[0] as [string, unknown, { headers?: Record<string, string> }];
    const key = config?.headers?.['Idempotency-Key'];
    expect(typeof key).toBe('string');
    expect(key!.length).toBeGreaterThan(0);
  });
});

describe('after the submission settles', () => {
  it('a failure keeps the form open and allows exactly one intentional retry', async () => {
    confirmDialogMock.mockResolvedValue(true);
    apiPost.mockRejectedValueOnce(new Error('network'));
    renderDetail();

    await act(async () => { fireEvent.click(submitBtn()); });

    await waitFor(() => expect(toastError).toHaveBeenCalledTimes(1));
    // Released in `finally`: the control comes back so a retry is possible.
    await waitFor(() => expect(submitBtn().hasAttribute('disabled')).toBe(false));

    apiPost.mockResolvedValueOnce({ data: { id: 'p1', installment_id: 'i1' } });
    const d = deferred<boolean>();
    confirmDialogMock.mockReturnValueOnce(d.promise);
    await sameFrame(submitBtn);
    await act(async () => { d.resolve(true); });

    await waitFor(() => expect(apiPost).toHaveBeenCalledTimes(2));
  });
});
