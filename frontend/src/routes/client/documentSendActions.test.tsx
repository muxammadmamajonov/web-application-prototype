// @vitest-environment jsdom
/**
 * LX-01 / LX-03 — the customer's two PDF actions must report what they are doing.
 *
 * Both buttons on this screen used to be `onClick={() => someAsyncThing(id)}`:
 * the promise was dropped, the button stayed enabled and unmarked for the whole
 * grant + delivery round trip, and a failure left nothing at all on the control
 * that started it. This is the Telegram WebView path on mobile data — the
 * slowest delivery hop in the product — so "looks untouched for four seconds"
 * is the normal case, and tapping again is the natural response.
 *
 * `lib/useDocumentSend.ts` was written and unit-tested for exactly this and had
 * no production consumer (LX-03). These tests exercise the RENDERED call sites,
 * not the hook, because a tested state machine nobody calls is the appearance
 * of safety rather than safety.
 *
 * TWO THINGS THIS FILE IS CAREFUL ABOUT:
 *
 * 1. Every request is a DEFERRED promise held open by the test. A mock that
 *    resolves immediately never lets the pending UI exist, so asserting on it
 *    would prove nothing.
 *
 * 2. The same-frame double-tap is fired inside ONE outer `act`, so React has
 *    not re-rendered — and therefore has not disabled the button — between the
 *    two clicks. Firing two separate `fireEvent.click`s would let `disabled`
 *    swallow the second one and the assertion would pass even with the hook's
 *    synchronous ref guard deleted. This is the only arrangement in which the
 *    ref guard is the thing under test.
 */
import { describe, expect, it, vi, beforeEach, afterEach } from 'vitest';
import { act, cleanup, fireEvent, render, waitFor } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import i18n from '@/i18n';
import { money } from '@/lib/format';
import type { DownloadResult } from '@/lib/download';

const downloadGranted = vi.fn();
vi.mock('@/lib/download', () => ({
  downloadGranted: (...a: unknown[]) => downloadGranted(...a),
}));

/** Mutable so a test can reorder the payment list and re-render. */
let DETAIL: Record<string, unknown>;
vi.mock('@/lib/queries', async (orig) => ({
  ...(await orig<Record<string, unknown>>()),
  useMyInstallment: () => ({ data: DETAIL, isLoading: false, isError: false, error: null }),
}));

const ClientInstallmentDetailPage = (await import('./InstallmentDetailPage')).default;

// ── fixture ─────────────────────────────────────────────────────────────────

const payment = (id: string, amount: string, status: string) => ({
  id, amount, method: 'cash', status,
  paid_at: '2026-02-10T10:00:00Z', external_ref: null, receipt_image_url: null,
});

const PAY_A = payment('pay-a', '500000', 'approved');
const PAY_B = payment('pay-b', '700000', 'approved');
const PAY_PENDING = payment('pay-waiting', '900000', 'pending');

function detail(payments: unknown[]): Record<string, unknown> {
  return {
    id: 'i1',
    currency: 'UZS',
    tenant: { id: 't1', name: 'Muddato Shop', logo: null, slug: 'shop' },
    contract_number: 'C-001', reference: null, status: 'active',
    principal: '7000000', paid_amount: '1200000', remaining: '5800000',
    monthly_amount: '700000', total_amount: '7000000', down_payment: '0',
    months: 10, paid_count: 2,
    start_date: '2026-01-10', next_due_date: '2026-08-10',
    headline: null, items: [], schedule: [], payments,
  };
}

/** A promise the test holds open, so the pending UI is observable. */
function deferred<T>() {
  let resolve!: (v: T) => void;
  let reject!: (e: unknown) => void;
  const promise = new Promise<T>((res, rej) => { resolve = res; reject = rej; });
  return { promise, resolve, reject };
}

/** Arm the next request and hand back its settle handles. */
function armPending() {
  const d = deferred<DownloadResult>();
  downloadGranted.mockReturnValueOnce(d.promise);
  return d;
}

function renderPage() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={['/me/installments/i1']}>
        <Routes>
          <Route path="/me/installments/:id" element={<ClientInstallmentDetailPage />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

const T = (k: string, o?: Record<string, unknown>) => i18n.t(k, o) as string;

/** Accessible name as assistive technology would compute it (whitespace collapsed). */
function name(el: Element): string {
  return (el.getAttribute('aria-label') ?? el.textContent ?? '').replace(/\s+/g, ' ').trim();
}
const norm = (s: string) => s.replace(/\s+/g, ' ').trim();

function byName(text: string): HTMLButtonElement {
  const hit = [...document.querySelectorAll('button')].filter((b) => name(b) === norm(text));
  if (hit.length !== 1) {
    throw new Error(`expected exactly one button named "${text}", found ${hit.length}`);
  }
  return hit[0] as HTMLButtonElement;
}

/**
 * The document-send announcements only.
 *
 * The page has a second polite region — the "awaiting confirmation" banner —
 * which is visible, not `sr-only`, and predates this slice. Scoping by
 * `.sr-only` keeps this assertion about the thing under test.
 */
const sendRegions = () =>
  [...document.querySelectorAll('[role="status"][aria-live="polite"].sr-only')]
    .map((n) => norm(n.textContent ?? ''));

const contractIdleName = () => T('client.openContract');
const receiptIdleName = (amount: string) =>
  T('client.receiptPdfFor', { amount: money(amount) });

beforeEach(async () => {
  downloadGranted.mockReset();
  DETAIL = detail([PAY_A, PAY_B, PAY_PENDING]);
  await i18n.changeLanguage('uz');
});
afterEach(() => { cleanup(); vi.restoreAllMocks(); });

// ── 1 · the contract button ─────────────────────────────────────────────────

describe('LX-01 · contract PDF', () => {
  it('starts idle and enabled, with no busy claim', () => {
    renderPage();
    const btn = byName(contractIdleName());
    expect(btn.disabled).toBe(false);
    expect(btn.getAttribute('aria-busy')).toBeNull();
    expect(downloadGranted).not.toHaveBeenCalled();
  });

  it('acknowledges the first tap before the request settles', () => {
    armPending();
    renderPage();
    const btn = byName(contractIdleName());

    fireEvent.click(btn);

    // The request is still open — nothing has come back — and the button
    // already says so. That is the whole point of the finding.
    expect(downloadGranted).toHaveBeenCalledTimes(1);
    expect(btn.getAttribute('aria-busy')).toBe('true');
    expect(btn.disabled).toBe(true);
    expect(name(btn)).toBe(norm(T('file.sending')));
  });

  it('shows a spinner while sending', () => {
    armPending();
    renderPage();
    const btn = byName(contractIdleName());
    fireEvent.click(btn);
    expect(btn.querySelector('.animate-spin'), 'no visible spinner while sending').toBeTruthy();
  });

  it('two taps in the same frame produce one request', async () => {
    armPending();
    renderPage();
    const btn = byName(contractIdleName());

    // One outer act ⇒ React has not re-rendered between the two clicks, so
    // `disabled` cannot be what stops the second one.
    await act(async () => {
      fireEvent.click(btn);
      fireEvent.click(btn);
    });

    expect(downloadGranted).toHaveBeenCalledTimes(1);
  });

  it('claims success only after a confirmed arrival', async () => {
    const first = armPending();
    renderPage();
    const btn = byName(contractIdleName());

    fireEvent.click(btn);
    expect(name(btn), 'success was claimed before the server confirmed').not.toBe(
      norm(T('file.ready')),
    );

    await act(async () => { first.resolve('delegated'); });
    await waitFor(() => expect(name(btn)).toBe(norm(T('file.ready'))));
    expect(btn.disabled).toBe(false);
    expect(btn.getAttribute('aria-busy')).toBeNull();
  });

  it('a non-arrived outcome becomes a retry, not a success', async () => {
    const first = armPending();
    renderPage();
    const btn = byName(contractIdleName());

    fireEvent.click(btn);
    await act(async () => { first.resolve('failed'); });

    await waitFor(() => expect(name(btn)).toBe(norm(T('file.retry'))));
    expect(btn.disabled).toBe(false);
  });

  it('retries on a single activation from failed', async () => {
    const first = armPending();
    renderPage();
    const btn = byName(contractIdleName());

    fireEvent.click(btn);
    await act(async () => { first.resolve('failed'); });
    await waitFor(() => expect(name(btn)).toBe(norm(T('file.retry'))));

    // ONE press. Not press-to-reset, press-again-to-retry.
    armPending();
    fireEvent.click(btn);
    expect(downloadGranted).toHaveBeenCalledTimes(2);
    expect(btn.getAttribute('aria-busy')).toBe('true');
  });

  it('does not claim Telegram delivery for a browser save', async () => {
    const first = armPending();
    renderPage();
    const btn = byName(contractIdleName());

    fireEvent.click(btn);
    await act(async () => { first.resolve('saved'); });
    await waitFor(() => expect(name(btn)).toBe(norm(T('file.ready'))));

    // Whatever the label says, it must not assert a chat delivery that the
    // browser path never performed.
    expect(name(btn)).not.toMatch(/telegram/i);
    expect(sendRegions().join(' ')).not.toMatch(/telegram/i);
  });

  it('announces the outcome in a polite live region', async () => {
    const first = armPending();
    renderPage();

    // Present but silent before anything happens — a region added at the moment
    // of the change is not reliably announced.
    expect(sendRegions().length).toBeGreaterThan(0);
    expect(sendRegions().every((s) => s === '')).toBe(true);

    fireEvent.click(byName(contractIdleName()));
    await act(async () => { first.resolve('delegated'); });
    await waitFor(() => expect(sendRegions()).toContain(norm(T('file.ready'))));

    // The page's own pending-payment banner is a live region too, and it must
    // not have been disturbed — announcing the payment list again on every
    // document state change is exactly what scoping these regions avoids.
    const banner = document.querySelector('section[role="status"][aria-live="polite"]');
    expect(banner?.textContent ?? '').not.toContain(norm(T('file.ready')));
  });

  it('keeps focus on the button across every state change', async () => {
    const first = armPending();
    renderPage();
    const btn = byName(contractIdleName());
    btn.focus();
    expect(document.activeElement).toBe(btn);

    fireEvent.click(btn);
    await act(async () => { first.resolve('delegated'); });
    await waitFor(() => expect(name(btn)).toBe(norm(T('file.ready'))));

    // Same node throughout — the element is never swapped for a different one.
    expect(document.activeElement).toBe(btn);
    expect(byName(T('file.ready'))).toBe(btn);
  });
});

// ── 2 · the receipt buttons ─────────────────────────────────────────────────

describe('LX-01 · receipt PDF, one independent state per payment', () => {
  const nameA = () => receiptIdleName('500000');
  const nameB = () => receiptIdleName('700000');

  it('exposes the action only on approved payments', () => {
    renderPage();
    // Both approved rows have one, and neither the pending row nor anything
    // else on the page carries its amount on a control.
    expect(byName(nameA())).toBeTruthy();
    expect(byName(nameB())).toBeTruthy();
    const pendingOnAControl = [...document.querySelectorAll('button')]
      .some((b) => name(b).includes(money('900000')));
    expect(pendingOnAControl, 'a pending payment exposed a receipt action').toBe(false);
  });

  it('sending one receipt leaves the other actionable', () => {
    armPending();
    renderPage();
    const a = byName(nameA());
    const b = byName(nameB());

    fireEvent.click(a);

    expect(a.disabled).toBe(true);
    expect(a.getAttribute('aria-busy')).toBe('true');
    expect(b.disabled, 'receipt B was disabled by receipt A').toBe(false);
    expect(b.getAttribute('aria-busy')).toBeNull();
  });

  it('each receipt settles into its own state', async () => {
    const first = armPending();
    renderPage();
    const a = byName(nameA());
    const b = byName(nameB());

    fireEvent.click(a);
    await act(async () => { first.resolve('delegated'); });

    await waitFor(() => expect(name(a)).toBe(norm(T('client.receiptPdfReady', { amount: money('500000') }))));
    expect(name(b), 'B inherited A\'s state').toBe(norm(nameB()));
  });

  it('double-tapping one receipt produces one request for that receipt', async () => {
    armPending();
    renderPage();
    const a = byName(nameA());

    await act(async () => {
      fireEvent.click(a);
      fireEvent.click(a);
    });

    expect(downloadGranted).toHaveBeenCalledTimes(1);
    expect(downloadGranted).toHaveBeenCalledWith('payment_receipt', 'pay-a', expect.anything());
  });

  it('each receipt requests its own payment id', async () => {
    const first = armPending();
    renderPage();

    fireEvent.click(byName(nameA()));
    await act(async () => { first.resolve('delegated'); });

    const second = armPending();
    fireEvent.click(byName(nameB()));
    await act(async () => { second.resolve('delegated'); });

    expect(downloadGranted).toHaveBeenNthCalledWith(1, 'payment_receipt', 'pay-a', {
      path: '/tenant/payments/pay-a/receipt.pdf', filename: 'receipt-pay-a.pdf',
    });
    expect(downloadGranted).toHaveBeenNthCalledWith(2, 'payment_receipt', 'pay-b', {
      path: '/tenant/payments/pay-b/receipt.pdf', filename: 'receipt-pay-b.pdf',
    });
  });

  it('a reordered list does not move one receipt\'s state onto another', async () => {
    const first = armPending();
    const { rerender } = renderPage();

    fireEvent.click(byName(nameA()));
    await act(async () => { first.resolve('delegated'); });
    await waitFor(() => expect(byName(T('client.receiptPdfReady', { amount: money('500000') }))).toBeTruthy());

    // The list comes back from the server in a different order.
    DETAIL = detail([PAY_PENDING, PAY_B, PAY_A]);
    rerender(
      <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
        <MemoryRouter initialEntries={['/me/installments/i1']}>
          <Routes>
            <Route path="/me/installments/:id" element={<ClientInstallmentDetailPage />} />
          </Routes>
        </MemoryRouter>
      </QueryClientProvider>,
    );

    // A is still the one that was sent; B is still untouched.
    expect(byName(T('client.receiptPdfReady', { amount: money('500000') }))).toBeTruthy();
    expect(byName(nameB()).disabled).toBe(false);
    expect(name(byName(nameB()))).toBe(norm(nameB()));
  });

  it('a failed receipt offers a one-activation retry', async () => {
    const first = armPending();
    renderPage();
    const a = byName(nameA());

    fireEvent.click(a);
    await act(async () => { first.resolve('failed'); });
    await waitFor(() => expect(name(a)).toBe(norm(T('client.receiptPdfRetry', { amount: money('500000') }))));
    expect(a.disabled).toBe(false);

    armPending();
    fireEvent.click(a);
    expect(downloadGranted).toHaveBeenCalledTimes(2);
    expect(a.getAttribute('aria-busy')).toBe('true');
  });

  it('the accessible name is accurate in every state', async () => {
    const first = armPending();
    renderPage();
    const a = byName(nameA());
    const amount = money('500000');

    expect(name(a)).toBe(norm(T('client.receiptPdfFor', { amount })));

    fireEvent.click(a);
    expect(name(a)).toBe(norm(T('client.receiptPdfSending', { amount })));

    await act(async () => { first.resolve('failed'); });
    await waitFor(() => expect(name(a)).toBe(norm(T('client.receiptPdfRetry', { amount }))));

    const second = armPending();
    fireEvent.click(a);
    expect(name(a)).toBe(norm(T('client.receiptPdfSending', { amount })));
    await act(async () => { second.resolve('saved'); });
    await waitFor(() => expect(name(a)).toBe(norm(T('client.receiptPdfReady', { amount }))));

    // Every one of those is a distinct string — a label that never changes
    // would satisfy a weaker assertion than this one.
    const seen = new Set([
      norm(T('client.receiptPdfFor', { amount })),
      norm(T('client.receiptPdfSending', { amount })),
      norm(T('client.receiptPdfRetry', { amount })),
      norm(T('client.receiptPdfReady', { amount })),
    ]);
    expect(seen.size).toBe(4);
  });

  it('keeps its 44x44 box and its spinner through every state', async () => {
    const first = armPending();
    renderPage();
    const a = byName(nameA());
    const amount = money('500000');

    // jsdom has no layout engine: the unconditional `h-11 w-11` floor is the
    // fact a layout engine would turn into 44x44, and it must survive every
    // state — an icon swap must not take the box with it.
    const boxed = (el: Element, when: string) => {
      const cls = el.className.split(/\s+/);
      expect(cls, `${when}: h-11 lost`).toContain('h-11');
      expect(cls, `${when}: w-11 lost`).toContain('w-11');
    };

    boxed(a, 'idle');
    fireEvent.click(a);
    boxed(a, 'sending');
    expect(a.querySelector('.animate-spin'), 'no spinner while sending').toBeTruthy();

    await act(async () => { first.resolve('failed'); });
    await waitFor(() => expect(name(a)).toBe(norm(T('client.receiptPdfRetry', { amount }))));
    boxed(a, 'failed');

    const second = armPending();
    fireEvent.click(a);
    await act(async () => { second.resolve('delegated'); });
    await waitFor(() => expect(name(a)).toBe(norm(T('client.receiptPdfReady', { amount }))));
    boxed(a, 'sent');
  });

  it('does not signal sent or failed with colour alone', async () => {
    const first = armPending();
    renderPage();
    const a = byName(nameA());

    // The glyph itself, not its colour: compare the drawn paths.
    const glyph = () => a.querySelector('svg')?.innerHTML ?? '';
    const idleGlyph = glyph();
    expect(idleGlyph).not.toBe('');

    fireEvent.click(a);
    await act(async () => { first.resolve('delegated'); });
    await waitFor(() => expect(name(a)).toBe(
      norm(T('client.receiptPdfReady', { amount: money('500000') })),
    ));

    // The name changed (asserted above) and the glyph changed too, so the state
    // survives both a screen reader and a monochrome display.
    expect(glyph(), 'sent is signalled by colour alone').not.toBe(idleGlyph);
  });
});
