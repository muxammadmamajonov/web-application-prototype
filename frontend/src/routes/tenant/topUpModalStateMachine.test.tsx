// @vitest-environment jsdom
/**
 * MUDD-133 — the top-up action's `idle → submitting → success | error` state
 * machine, on the surfaces this lane owns (`TopUpModal`, `BillingPage`).
 *
 * The reported defect (an eternal spinner + `aria-busy` at true idle, zero
 * actions taken) was traced to `routes/platform/_modals/TenantDetailDrawer.tsx`
 * (`loading={topup.isPending || !topupAmt}` — an empty-amount form state was
 * feeding the SAME flag that drives the spinner and `aria-busy`), which this
 * lane does not own. On the owned surfaces below, `busy`/`preparing` already
 * tracked only real in-flight work, not form completeness — these tests pin
 * that down as a regression guard and cover the transitions the state machine
 * must honour: no spinner/`aria-busy` at idle, both during an actual
 * submission, back to idle-actionable on error, and no crash if the surface is
 * unmounted mid-request.
 */
import { StrictMode } from 'react';
import { describe, expect, it, vi, beforeEach, afterEach } from 'vitest';
import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router';
import i18n from '@/i18n';

type Info = Record<string, unknown>;
let INFO: Info;

const base = (over: Info = {}): Info => ({
  balance: 0,
  currency: 'UZS',
  terms: { uz: '', ru: '' },
  cards: [
    { id: 'c-uz', number: '5614000000002802', bank: 'Kapital Bank', network: 'UZCARD', settlement_currency: 'UZS' },
  ],
  cards_withheld: false,
  providers: { payme: false, click: false },
  can_topup: true,
  ...over,
});

const createTopupMock = vi.fn();
const confirmDialogMock = vi.fn();
const toastSuccess = vi.fn();
const toastError = vi.fn();
const downscaleImageMock = vi.fn(async (f: File) => f);

vi.mock('@/lib/queries', async (importOriginal) => ({
  ...(await importOriginal<Record<string, unknown>>()),
  useTenantTopupInfo: () => ({ data: INFO, isLoading: false, isError: false }),
  createTopup: (...a: unknown[]) => createTopupMock(...a),
}));

vi.mock('@/lib/image', async (orig) => ({
  ...(await orig<Record<string, unknown>>()),
  downscaleImage: (f: File) => downscaleImageMock(f),
}));

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
  useSession: (sel?: (s: unknown) => unknown) => {
    const state = { activeTenantId: 't1', me: { id: 'u1' }, activeRole: () => 'owner',
      tenants: [{ id: 't1', currency: 'UZS' }] };
    return sel ? sel(state) : state;
  },
}));

const { TopUpModal } = await import('@/routes/tenant/_modals/TopUpModal');

/** A promise the test holds open, so the in-flight window is observable. */
function deferred<T = void>() {
  let resolve!: (v: T) => void;
  let reject!: (e: unknown) => void;
  const promise = new Promise<T>((res, rej) => { resolve = res; reject = rej; });
  return { promise, resolve, reject };
}

function open(onClose = vi.fn()) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter><TopUpModal open onClose={onClose} /></MemoryRouter>
    </QueryClientProvider>,
  );
}

/**
 * React 19 double-invokes effects under StrictMode: setup → cleanup → setup.
 * `frontend/src/main.tsx` renders the whole app inside `<StrictMode>`, so this
 * is what production actually mounts through in development — a component
 * whose "unmounted" ref never gets re-armed on the second setup looks mounted
 * to every other check yet behaves as permanently unmounted.
 */
function openStrict(onClose = vi.fn()) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <StrictMode>
      <QueryClientProvider client={qc}>
        <MemoryRouter><TopUpModal open onClose={onClose} /></MemoryRouter>
      </QueryClientProvider>
    </StrictMode>,
  );
}

const sendBtn = () => screen.getByRole('button', { name: /yuborish/i }) as HTMLButtonElement;

/** Fill the form to the point the Send button is actionable. */
async function fillValid() {
  const amountInput = screen.getByLabelText(/summa/i) as HTMLInputElement;
  fireEvent.change(amountInput, { target: { value: '50000' } });
  fireEvent.click(screen.getAllByRole('radio')[0]);
  const file = new File(['x'], 'receipt.pdf', { type: 'application/pdf' });
  const fileInput = document.querySelector('input[type="file"]') as HTMLInputElement;
  await act(async () => { fireEvent.change(fileInput, { target: { files: [file] } }); });
}

beforeEach(async () => {
  INFO = base();
  createTopupMock.mockReset();
  confirmDialogMock.mockReset();
  toastSuccess.mockReset();
  toastError.mockReset();
  downscaleImageMock.mockReset();
  downscaleImageMock.mockImplementation(async (f: File) => f);
  Object.assign(navigator, { clipboard: { writeText: vi.fn(async () => undefined) } });
  await i18n.changeLanguage('uz');
});
afterEach(() => { cleanup(); vi.restoreAllMocks(); });

describe('idle: 0 balance, empty amount, zero actions', () => {
  it('the Send button shows no spinner and no aria-busy', () => {
    open();
    const btn = sendBtn();
    expect(btn.getAttribute('aria-busy')).not.toBe('true');
    expect(btn.querySelector('.animate-spin')).toBeNull();
  });

  it('is disabled with a stated reason, not a silent stand-in for loading', () => {
    open();
    const btn = sendBtn();
    expect(btn.disabled).toBe(true);
    expect(btn.getAttribute('title')).toBeTruthy();
  });
});

describe('submitting: an actual in-flight request', () => {
  it('shows the spinner and aria-busy only once the request has started', async () => {
    confirmDialogMock.mockResolvedValue(true);
    const gate = deferred<{ checkout_url?: string }>();
    createTopupMock.mockReturnValue(gate.promise);
    open();
    await fillValid();

    await waitFor(() => expect(sendBtn().disabled).toBe(false));
    fireEvent.click(sendBtn());

    await waitFor(() => expect(createTopupMock).toHaveBeenCalledTimes(1));
    expect(sendBtn().getAttribute('aria-busy')).toBe('true');
    expect(sendBtn().querySelector('.animate-spin')).toBeTruthy();

    await act(async () => { gate.resolve({}); });
  });

  it('a further activation is dropped while the request is in flight (existing useSubmitGuard, kept)', async () => {
    confirmDialogMock.mockResolvedValue(true);
    const gate = deferred<{ checkout_url?: string }>();
    createTopupMock.mockReturnValue(gate.promise);
    open();
    await fillValid();

    await waitFor(() => expect(sendBtn().disabled).toBe(false));
    fireEvent.click(sendBtn());
    await waitFor(() => expect(createTopupMock).toHaveBeenCalledTimes(1));

    fireEvent.click(sendBtn());
    expect(createTopupMock).toHaveBeenCalledTimes(1);

    await act(async () => { gate.resolve({}); });
  });
});

describe('error: the request settles rejected', () => {
  it('returns to idle-actionable — no stuck spinner, a retry is possible', async () => {
    confirmDialogMock.mockResolvedValue(true);
    createTopupMock.mockRejectedValueOnce(new Error('network'));
    open();
    await fillValid();

    await waitFor(() => expect(sendBtn().disabled).toBe(false));
    await act(async () => { fireEvent.click(sendBtn()); });

    await waitFor(() => expect(toastError).toHaveBeenCalledTimes(1));
    expect(sendBtn().getAttribute('aria-busy')).not.toBe('true');
    expect(sendBtn().querySelector('.animate-spin')).toBeNull();
  });
});

describe('unmounted mid-request', () => {
  it('does not call onClose or show a success toast when the request resolves after unmount', async () => {
    confirmDialogMock.mockResolvedValue(true);
    const gate = deferred<{ checkout_url?: string }>();
    createTopupMock.mockReturnValue(gate.promise);
    const onClose = vi.fn();
    open(onClose);
    await fillValid();
    // `fillValid` selects a card, which fires its own "copied to clipboard"
    // success toast — clear it so the assertion below is about the top-up
    // submission, not this unrelated one.
    toastSuccess.mockClear();
    await waitFor(() => expect(sendBtn().disabled).toBe(false));
    fireEvent.click(sendBtn());
    await waitFor(() => expect(createTopupMock).toHaveBeenCalledTimes(1));

    cleanup(); // unmounts while createTopup is still pending
    await act(async () => { gate.resolve({}); });

    // The mountedRef guard exists specifically to stop this: a request that
    // settles after the surface is gone must not act as if it were still on
    // screen. If the guard is missing (or never re-armed), this fires anyway
    // and `onClose` gets called against a parent that already believes the
    // modal is closed.
    expect(onClose).not.toHaveBeenCalled();
    expect(toastSuccess).not.toHaveBeenCalled();
  });
});

describe('StrictMode: effects double-invoke (setup → cleanup → setup)', () => {
  it('clears `preparing` after a receipt is downscaled, so no spinner is left at idle', async () => {
    openStrict();
    fireEvent.click(screen.getAllByRole('radio')[0]);
    const file = new File(['x'], 'receipt.png', { type: 'image/png' });
    const fileInput = document.querySelector('input[type="file"]') as HTMLInputElement;
    await act(async () => { fireEvent.change(fileInput, { target: { files: [file] } }); });

    const btn = sendBtn();
    expect(btn.getAttribute('aria-busy')).not.toBe('true');
    expect(btn.querySelector('.animate-spin')).toBeNull();
  });

  it('still closes and toasts success on a real submit, instead of looking permanently unmounted', async () => {
    confirmDialogMock.mockResolvedValue(true);
    createTopupMock.mockResolvedValue({});
    const onClose = vi.fn();
    openStrict(onClose);
    await fillValid();
    // Same unrelated "copied to clipboard" toast as above — clear before
    // isolating the submit's own toast.
    toastSuccess.mockClear();

    await waitFor(() => expect(sendBtn().disabled).toBe(false));
    await act(async () => { fireEvent.click(sendBtn()); });

    await waitFor(() => expect(onClose).toHaveBeenCalledTimes(1));
    expect(toastSuccess).toHaveBeenCalledTimes(1);
  });
});
