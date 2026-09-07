// @vitest-environment jsdom
/**
 * MUDD-142 — folded into MUDD-133's lane while investigating the same
 * component. Three gaps distinct from the idle-spinner defect (covered in
 * `topUpModalStateMachine.test.tsx`):
 *
 *  1. `TopUpModal` never unmounts when the modal closes (only `<Modal>`'s
 *     children do), so the draft (amount/method/receipt/cardId/errors)
 *     survived any close path except a successful submit — reopening showed
 *     stale data.
 *  2. A rejected replacement file (wrong type, or too large after downscale)
 *     left the previously-armed receipt in place, so the owner believed they
 *     replaced their proof of payment and could silently submit the stale one.
 *  3. `downscaleImage()` rejecting was unhandled — no feedback, and the
 *     picked file silently went nowhere.
 */
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

vi.mock('@/lib/feedback', async (orig) => ({
  ...(await orig<Record<string, unknown>>()),
  confirmDialog: (...args: unknown[]) => confirmDialogMock(...args),
  toast: {
    success: (...a: unknown[]) => toastSuccess(...a),
    error: (...a: unknown[]) => toastError(...a),
    warning: vi.fn(), info: vi.fn(),
  },
}));

vi.mock('@/lib/image', async (orig) => ({
  ...(await orig<Record<string, unknown>>()),
  downscaleImage: (f: File) => downscaleImageMock(f),
}));

vi.mock('@/store/session', () => ({
  useSession: (sel?: (s: unknown) => unknown) => {
    const state = { activeTenantId: 't1', me: { id: 'u1' }, activeRole: () => 'owner',
      tenants: [{ id: 't1', currency: 'UZS' }] };
    return sel ? sel(state) : state;
  },
}));

const { TopUpModal } = await import('@/routes/tenant/_modals/TopUpModal');

function renderModal(open: boolean, onClose = vi.fn()) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const utils = render(
    <QueryClientProvider client={qc}>
      <MemoryRouter><TopUpModal open={open} onClose={onClose} /></MemoryRouter>
    </QueryClientProvider>,
  );
  return { ...utils, qc };
}

const sendBtn = () => screen.getByRole('button', { name: /yuborish/i }) as HTMLButtonElement;
const fileInput = () => document.querySelector('input[type="file"]') as HTMLInputElement;

async function pickFile(name: string, type: string, content = 'x') {
  const file = new File([content], name, { type });
  await act(async () => { fireEvent.change(fileInput(), { target: { files: [file] } }); });
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

describe('draft reset on close (MUDD-142 #1)', () => {
  it('closing by any path clears amount/method/receipt/cardId — reopen shows a fresh form', async () => {
    const onClose = vi.fn();
    const { rerender, qc } = renderModal(true, onClose);

    fireEvent.change(screen.getByLabelText(/summa/i), { target: { value: '50000' } });
    await pickFile('receipt.pdf', 'application/pdf');
    await waitFor(() => expect((screen.getByLabelText(/summa/i) as HTMLInputElement).value).not.toBe(''));

    // TopUpModal itself never unmounts on close — only `<Modal>`'s children do
    // — so the only observable close signal is the parent flipping `open`,
    // regardless of which control (confirmed discard, backdrop, Esc, Cancel)
    // triggered it.
    await act(async () => {
      rerender(
        <QueryClientProvider client={qc}>
          <MemoryRouter><TopUpModal open={false} onClose={onClose} /></MemoryRouter>
        </QueryClientProvider>,
      );
    });
    await act(async () => {
      rerender(
        <QueryClientProvider client={qc}>
          <MemoryRouter><TopUpModal open onClose={onClose} /></MemoryRouter>
        </QueryClientProvider>,
      );
    });

    expect((screen.getByLabelText(/summa/i) as HTMLInputElement).value).toBe('');
    expect(sendBtn().disabled).toBe(true);
    expect(sendBtn().getAttribute('title')).toBe('Summani kiriting');
  });
});

describe('rejected replacement receipt (MUDD-142 #2)', () => {
  it('an oversize/wrong-type replacement clears the previously-armed receipt', async () => {
    renderModal(true);
    await pickFile('good.pdf', 'application/pdf');
    fireEvent.change(screen.getByLabelText(/summa/i), { target: { value: '50000' } });
    fireEvent.click(screen.getAllByRole('radio')[0]);
    await waitFor(() => expect(sendBtn().disabled).toBe(false));

    // Replace with a rejected type — must not leave the good.pdf silently armed.
    await pickFile('malware.exe', 'application/x-msdownload');

    expect(toastError).toHaveBeenCalled();
    await waitFor(() => expect(sendBtn().disabled).toBe(true));
    expect(sendBtn().getAttribute('title')).toBe('Chek rasmini yuklang');
  });
});

describe('downscaleImage rejection (MUDD-142 #3)', () => {
  it('shows feedback and clears the receipt instead of failing silently', async () => {
    renderModal(true);
    downscaleImageMock.mockRejectedValueOnce(new Error('corrupt image'));
    fireEvent.change(screen.getByLabelText(/summa/i), { target: { value: '50000' } });
    fireEvent.click(screen.getAllByRole('radio')[0]);

    await pickFile('receipt.png', 'image/png');

    await waitFor(() => expect(toastError).toHaveBeenCalledWith("Chekni tayyorlab bo'lmadi. Boshqa fayl tanlang."));
    await waitFor(() => expect(sendBtn().disabled).toBe(true));
    expect(sendBtn().getAttribute('title')).toBe('Chek rasmini yuklang');
    // Not left in the `preparing` state either — no stuck spinner.
    expect(sendBtn().getAttribute('aria-busy')).not.toBe('true');
  });
});
