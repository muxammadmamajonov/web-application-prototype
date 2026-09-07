// @vitest-environment jsdom
/**
 * LX-02 / LX-07 at the four production call sites.
 *
 * `receiptImageFrame.test.tsx` proves the frame's contract in isolation. This
 * file proves the four surfaces actually USE it, and that adopting it did not
 * disturb what already worked: the money decision on `PaymentReviewPage`, the
 * "Open" escape in the two drawers, and the pick/replace/remove workflow in the
 * customer's upload preview.
 *
 * Same honesty limit as the component file: jsdom computes no boxes, so these
 * are DOM/CSS-CONTRACT assertions. Rectangles are measured in a real browser at
 * five viewports and recorded in §20 of the audit.
 */
import { describe, expect, it, vi, beforeAll, beforeEach, afterAll, afterEach } from 'vitest';
import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import i18n from '@/i18n';
import { RECEIPT_FRAME_HEIGHT_CLASS } from '@/components/ui/ReceiptImageFrame';

const RECEIPT_URL = '/api/receipts/blob-1?s=sig';

// ── mocks shared by every surface ───────────────────────────────────────────

const apiGet = vi.fn();
const apiPost = vi.fn(async () => ({ data: {} }));
vi.mock('@/lib/api', () => ({
  api: {
    get: (...a: unknown[]) => apiGet(...a),
    post: (...a: unknown[]) => apiPost(...a),
    defaults: { adapter: undefined },
  },
  setSessionToken: () => {},
}));

const approveMutate = vi.fn(async () => ({}));
const rejectMutate = vi.fn(async () => ({}));
vi.mock('@/lib/mutations', async (orig) => ({
  ...(await orig<Record<string, unknown>>()),
  useApprovePayment: () => ({ mutateAsync: approveMutate, isPending: false }),
  useRejectPayment: () => ({ mutateAsync: rejectMutate, isPending: false }),
}));

/** The rows both payment lists render. Mutable per test. */
let PAYMENT_ROWS: Array<Record<string, unknown>> = [];
const paymentStatusCounts = () => {
  const c = { pending: 0, approved: 0, rejected: 0, refunded: 0 };
  for (const p of PAYMENT_ROWS) {
    const s = p.status as keyof typeof c;
    if (s in c) c[s] += 1;
  }
  return c;
};
vi.mock('@/lib/queries', async (orig) => ({
  ...(await orig<Record<string, unknown>>()),
  // (MUDD-84) Both payment lists now page through `useTenantPaymentsPage`,
  // filtered server-side by status — this mock shape mirrors that hook's
  // useInfiniteQuery return, not the old flat `useTenantPayments` shape.
  useTenantPaymentsPage: () => ({
    data: { pages: [{ items: PAYMENT_ROWS, total: PAYMENT_ROWS.length, counts: paymentStatusCounts() }] },
    isLoading: false, isError: false, error: null, refetch: () => {},
    hasNextPage: false, isFetchingNextPage: false, fetchNextPage: () => {},
  }),
  useTenantInstallments: () => ({ data: { items: [] }, isLoading: false, isError: false }),
  useMyInstallment: () => ({ data: CLIENT_DETAIL, isLoading: false, isError: false, error: null }),
}));

// The client upload page downscales on the main thread with a canvas jsdom does
// not have; the frame under test is downstream of that, so it is stubbed out.
vi.mock('@/lib/image', async (orig) => ({
  ...(await orig<Record<string, unknown>>()),
  downscaleImage: async (f: File) => f,
}));

const CLIENT_DETAIL = {
  id: 'i1', currency: 'UZS',
  tenant: { id: 't1', name: 'Shop', logo: null, slug: 'shop' },
  contract_number: 'C-1', reference: null, status: 'active',
  principal: '1000000', paid_amount: '0', remaining: '1000000',
  monthly_amount: '100000', total_amount: '1000000', down_payment: '0',
  months: 10, paid_count: 0, start_date: '2026-01-10', next_due_date: '2026-08-10',
  headline: null, items: [], schedule: [], payments: [],
};

const paymentRow = (over: Record<string, unknown> = {}) => ({
  id: 'p1', installment_id: 'i1', customer_id: 'c1', customer_name: 'Anvar Karimov',
  contract_number: 'MDD-1', amount: 500000, currency: 'UZS', method: 'cash',
  status: 'approved', receipt_image_url: RECEIPT_URL,
  paid_at: '2026-02-10T10:00:00Z', created_at: '2026-02-10T10:00:00Z',
  ...over,
});

const REVIEW = (over: Record<string, unknown> = {}) => ({
  id: 'pay-1', customer_name: 'Anvar Karimov', contract_number: 'MDD-1',
  installment_id: 'i1', amount_formatted: '4 800 000', currency: 'UZS',
  method_label: 'Naqd', status: 'pending', status_label: 'Kutilmoqda',
  receipt_image_url: RECEIPT_URL, submitted_at_label: '1-sentabr',
  actionable: true, ...over,
});

const PaymentReviewPage = (await import('./tenant/PaymentReviewPage')).default;
const TenantPaymentsPage = (await import('./tenant/PaymentsPage')).default;
const StaffPaymentsPage = (await import('./staff/PaymentsPage')).default;
const ClientReceiptUploadPage = (await import('./client/ReceiptUploadPage')).default;

function renderAt(path: string, routePath: string, element: React.ReactNode) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={[path]}>
        <Routes><Route path={routePath} element={element} /></Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

const frames = () => screen.queryAllByTestId('receipt-frame');
const frame = () => screen.getByTestId('receipt-frame');
const frameImg = () => frame().querySelector('img') as HTMLImageElement;

/** The reserved-height contract as a class — jsdom's CSSOM drops `clamp()`. */
const height = (el: Element) => el.className.split(/\s+/).find((c) => c.startsWith('h-[')) ?? '';

/**
 * Both payment lists render a desktop table AND a mobile card list (`hidden
 * md:block` / `md:hidden`), so every customer name is in the DOM twice. jsdom
 * applies no media query, so both are "present"; either one opens the drawer.
 */
const openRow = (name: string) => screen.getAllByText(name)[0]!;

/** Where a node sits in the document, as a structural path of child indices. */
function position(el: Element): string {
  const parts: string[] = [];
  let node: Element | null = el;
  while (node && node.parentElement) {
    parts.unshift(String([...node.parentElement.children].indexOf(node)));
    node = node.parentElement;
  }
  return parts.join('/');
}

beforeEach(async () => {
  apiGet.mockReset();
  approveMutate.mockClear();
  rejectMutate.mockClear();
  PAYMENT_ROWS = [];
  await i18n.changeLanguage('uz');
});
afterEach(() => { cleanup(); vi.restoreAllMocks(); });

// ── 1 · PaymentReviewPage — the HIGH finding ────────────────────────────────

describe('LX-02 · tenant/PaymentReviewPage', () => {
  async function renderReview(over: Record<string, unknown> = {}) {
    apiGet.mockResolvedValue({ data: REVIEW(over) });
    renderAt('/tenant/payments/pay-1/review', '/tenant/payments/:paymentId/review', <PaymentReviewPage />);
    await screen.findByTestId('approve-payment').catch(() => null);
    await waitFor(() => expect(screen.queryByText(/4 800 000/)).toBeTruthy());
  }

  const approve = () => screen.getByTestId('approve-payment');
  const reject = () => screen.getByTestId('reject-payment');

  it('reserves the receipt box before the image loads', async () => {
    await renderReview();
    expect(frames(), 'the review screen does not use the shared frame').toHaveLength(1);
    expect(height(frame())).toBe(RECEIPT_FRAME_HEIGHT_CLASS);
  });

  it('the decision controls do not move when the receipt loads', async () => {
    await renderReview();
    const [a, r] = [approve(), reject()];
    const before = { a: position(a), r: position(r), h: height(frame()) };

    fireEvent.load(frameImg());

    // Same nodes (no remount), same structural position, same reserved height.
    expect(approve(), 'Approve was remounted by the image load').toBe(a);
    expect(reject()).toBe(r);
    expect({ a: position(approve()), r: position(reject()), h: height(frame()) }).toEqual(before);
  });

  it('the decision controls do not move when the receipt fails', async () => {
    await renderReview();
    const [a, r] = [approve(), reject()];
    const before = { a: position(a), r: position(r), h: height(frame()) };

    fireEvent.error(frameImg());

    expect(approve()).toBe(a);
    expect(reject()).toBe(r);
    expect({ a: position(approve()), r: position(reject()), h: height(frame()) }).toEqual(before);
    // …and the reviewer is told why, in their own language.
    expect(screen.getByText(i18n.t('receipt.imageFailed') as string)).toBeTruthy();
  });

  it('keeps the evidence above the decision, uncropped', async () => {
    await renderReview();
    // The frame must precede the controls in document order — the reviewer sees
    // the proof before the buttons, which is the whole point of the screen.
    expect(frame().compareDocumentPosition(approve()) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(frameImg().className).toContain('object-contain');
    expect(frameImg().className).not.toContain('object-cover');
  });

  it('does not overlap the controls', async () => {
    await renderReview();
    // An absolutely-positioned frame would sit on top of the buttons; a normal
    // in-flow block cannot.
    expect(frame().className.split(/\s+/)).not.toContain('absolute');
    expect(frame().className.split(/\s+/)).not.toContain('fixed');
    expect(frame().contains(approve()), 'the frame swallowed the decision').toBe(false);
  });

  it('both controls stay keyboard reachable through every image state', async () => {
    await renderReview();
    for (const step of [() => {}, () => fireEvent.load(frameImg()), () => fireEvent.error(frameImg())]) {
      step();
      for (const b of [approve(), reject()]) {
        expect(b.tagName).toBe('BUTTON');
        expect(b.hasAttribute('disabled')).toBe(false);
        expect(b.getAttribute('tabindex')).not.toBe('-1');
      }
    }
  });

  it('still guards a repeated decision while one is in flight', async () => {
    let settle: () => void = () => {};
    approveMutate.mockImplementationOnce(() => new Promise<void>((r) => { settle = r; }));
    await renderReview();

    fireEvent.click(approve());
    // `deciding` has flipped, so the pair is disabled and a second press —
    // on either button — cannot start a second money transition.
    await waitFor(() => expect(approve().hasAttribute('disabled')).toBe(true));
    expect(reject().hasAttribute('disabled')).toBe(true);
    fireEvent.click(approve());
    fireEvent.click(reject());
    expect(approveMutate).toHaveBeenCalledTimes(1);
    expect(rejectMutate).not.toHaveBeenCalled();

    await act(async () => { settle(); });
  });

  it('sends the decision for this payment', async () => {
    await renderReview();
    fireEvent.click(approve());
    await waitFor(() => expect(approveMutate).toHaveBeenCalledTimes(1));
    expect(approveMutate).toHaveBeenCalledWith('pay-1');
    // This test used to be titled "the guard is state-based…" and recorded that
    // two same-frame clicks both reached `mutateAsync` — the defect Slice 2
    // found but left in scope-boundary (audit §20.11). That is fixed: the
    // screen now takes a synchronous ref through `useSubmitGuard`, and the
    // same-frame guarantee is owned by `paymentReviewDecisionGuard.test.tsx`.
    // The narrative is corrected here rather than left standing as a false
    // comment; the Slice 2 assertions themselves are unchanged.
  });

  it('offers no decision at all on an already-processed payment', async () => {
    await renderReview({ actionable: false, status: 'approved' });
    expect(screen.queryByTestId('approve-payment')).toBeNull();
    expect(screen.queryByTestId('reject-payment')).toBeNull();
    expect(screen.getByText(i18n.t('review.alreadyProcessed') as string)).toBeTruthy();
    // …and the receipt is still framed, because it is still the evidence.
    expect(height(frame())).toBe(RECEIPT_FRAME_HEIGHT_CLASS);
  });

  it('announces the image state without taking over the page', async () => {
    await renderReview();
    const status = frame().querySelector('[role="status"]');
    expect(status).toBeTruthy();
    expect(frame().contains(status!)).toBe(true);
    // The page itself is not replaced by an error state on image failure.
    fireEvent.error(frameImg());
    expect(screen.getByTestId('approve-payment')).toBeTruthy();
    expect(screen.getByText(/4 800 000/)).toBeTruthy();
  });
});

// ── 2 · the two detail drawers ──────────────────────────────────────────────

describe.each([
  ['tenant', () => renderAt('/tenant/payments', '/tenant/payments', <TenantPaymentsPage />)],
  ['staff', () => renderAt('/staff/payments', '/staff/payments', <StaffPaymentsPage />)],
] as const)('LX-07 · %s payment detail', (_who, mount) => {
  beforeEach(() => { PAYMENT_ROWS = [paymentRow()]; });

  async function openDetail() {
    mount();
    await screen.findAllByText('Anvar Karimov');
    fireEvent.click(openRow('Anvar Karimov'));
    await screen.findByTestId('receipt-frame');
  }

  it('uses the shared frame', async () => {
    await openDetail();
    expect(frames()).toHaveLength(1);
    expect(height(frame())).toBe(RECEIPT_FRAME_HEIGHT_CLASS);
    expect(frameImg().className).toContain('object-contain');
  });

  it('does not change the drawer geometry once the receipt decodes', async () => {
    await openDetail();
    const node = frame();
    const before = height(node);
    fireEvent.load(frameImg());
    expect(frame()).toBe(node);
    expect(height(frame())).toBe(before);
    fireEvent.error(frameImg());
    expect(height(frame())).toBe(before);
  });

  it('keeps the Open escape', async () => {
    await openDetail();
    const link = screen.getByRole('link', { name: i18n.t('common.open') as string });
    expect(link.getAttribute('href')).toBe(RECEIPT_URL);
    // It survives a failed decode, which is exactly when it matters most —
    // a PDF receipt can never render in an <img> but opens fine in a tab.
    fireEvent.error(frameImg());
    expect(screen.getByRole('link', { name: i18n.t('common.open') as string })).toBeTruthy();
  });

  it('leaves the modal body scrollable and the footer clear of the image', async () => {
    await openDetail();
    const body = document.querySelector('[role="dialog"] .overflow-y-auto');
    expect(body, 'the modal body is no longer a scroll container').toBeTruthy();
    expect(body!.contains(frame()), 'the receipt escaped the scrolling body').toBe(true);
    const footer = screen.queryByTestId('modal-footer');
    if (footer) expect(footer.contains(frame())).toBe(false);
  });

  it('introduces no horizontal scroller', async () => {
    await openDetail();
    expect(frame().className).not.toMatch(/overflow-x-/);
    expect(frame().className).toContain('w-full');
  });
});

describe('the two drawers do not drift apart', () => {
  it('render the receipt through the same component', async () => {
    const shots: string[] = [];
    for (const mount of [
      () => renderAt('/tenant/payments', '/tenant/payments', <TenantPaymentsPage />),
      () => renderAt('/staff/payments', '/staff/payments', <StaffPaymentsPage />),
    ]) {
      PAYMENT_ROWS = [paymentRow()];
      mount();
      await screen.findAllByText('Anvar Karimov');
    fireEvent.click(openRow('Anvar Karimov'));
      await screen.findByTestId('receipt-frame');
      shots.push(`${frame().className}|${height(frame())}|${frameImg().className}`);
      cleanup();
    }
    expect(shots[0], 'tenant and staff receipts have drifted apart').toBe(shots[1]);
  });
});

// ── 3 · the customer's upload preview ───────────────────────────────────────

describe('LX-07 · client/ReceiptUploadPage preview', () => {
  const urls: string[] = [];
  // jsdom implements no object-URL store at all — `URL.createObjectURL` is not
  // merely unimplemented, it is absent, so `vi.spyOn` has nothing to wrap.
  // Defined and torn down by hand; the page's revoke bookkeeping is left
  // observable rather than stubbed away.
  const revoked: string[] = [];
  const original = Object.getOwnPropertyDescriptor(URL, 'createObjectURL');
  let n = 0;
  // beforeAll, not beforeEach: hooks unwind innermost-first, so a per-test
  // teardown here would remove `revokeObjectURL` BEFORE the outer `cleanup()`
  // unmounts the page — and the page revokes its object URL on unmount.
  beforeAll(() => {
    Object.defineProperty(URL, 'createObjectURL', {
      configurable: true, writable: true,
      value: () => { const u = `blob:receipt-${++n}`; urls.push(u); return u; },
    });
    Object.defineProperty(URL, 'revokeObjectURL', {
      configurable: true, writable: true,
      value: (u: string) => { revoked.push(u); },
    });
  });
  afterAll(() => {
    if (original) Object.defineProperty(URL, 'createObjectURL', original);
    else Reflect.deleteProperty(URL as unknown as object, 'createObjectURL');
    Reflect.deleteProperty(URL as unknown as object, 'revokeObjectURL');
  });
  beforeEach(() => {
    urls.length = 0;
    revoked.length = 0;
    apiGet.mockResolvedValue({ data: { card_instructions: null } });
  });

  function mount() {
    return renderAt('/me/installments/i1/receipt', '/me/installments/:id/receipt', <ClientReceiptUploadPage />);
  }

  const fileInput = () => document.querySelector('input[type="file"]') as HTMLInputElement;

  async function pick(name: string, type = 'image/jpeg') {
    const f = new File([new Uint8Array([1, 2, 3])], name, { type });
    await act(async () => { fireEvent.change(fileInput(), { target: { files: [f] } }); });
  }

  it('reserves the preview box as soon as a file is chosen', async () => {
    mount();
    await pick('a.jpg');
    await waitFor(() => expect(frames()).toHaveLength(1));
    expect(height(frame())).toBe(RECEIPT_FRAME_HEIGHT_CLASS);
    expect(frameImg().className).toContain('object-contain');
  });

  it('keeps the removal control usable over the frame', async () => {
    mount();
    await pick('a.jpg');
    await waitFor(() => expect(frames()).toHaveLength(1));
    const remove = screen.getByRole('button', { name: i18n.t('client.removePhoto') as string });
    expect(remove).toBeTruthy();
    fireEvent.load(frameImg());
    expect(screen.getByRole('button', { name: i18n.t('client.removePhoto') as string })).toBe(remove);
  });

  it('a replacement photo does not inherit the previous one\'s failure', async () => {
    mount();
    await pick('a.jpg');
    await waitFor(() => expect(frames()).toHaveLength(1));
    const firstUrl = urls[0]!;
    fireEvent.error(frameImg());
    expect(screen.getByText(i18n.t('receipt.imageFailed') as string)).toBeTruthy();

    // The picker is mounted only while there is NO preview, so "replace" on
    // this page means remove-then-choose. That is the workflow it offers, and
    // asserting an in-place swap here would be asserting a fiction — the
    // src-keyed reset itself is pinned in `receiptImageFrame.test.tsx`.
    fireEvent.click(screen.getByRole('button', { name: i18n.t('client.removePhoto') as string }));
    await waitFor(() => expect(frames()).toHaveLength(0));
    await pick('b.jpg');
    await waitFor(() => expect(frames()).toHaveLength(1));

    expect(screen.queryByText(i18n.t('receipt.imageFailed') as string)).toBeNull();
    expect(frameImg().getAttribute('src')).toBe(urls[1]);
    expect(height(frame())).toBe(RECEIPT_FRAME_HEIGHT_CLASS);
    // The object-URL bookkeeping this page already had is untouched by the frame.
    expect(revoked, 'the replaced blob was not revoked').toContain(firstUrl);
  });

  it('removes the frame cleanly and can start over', async () => {
    mount();
    await pick('a.jpg');
    await waitFor(() => expect(frames()).toHaveLength(1));

    fireEvent.click(screen.getByRole('button', { name: i18n.t('client.removePhoto') as string }));
    await waitFor(() => expect(frames()).toHaveLength(0));
    expect(fileInput(), 'the picker is gone, so the customer cannot retry').toBeTruthy();

    await pick('c.jpg');
    await waitFor(() => expect(frames()).toHaveLength(1));
    expect(height(frame())).toBe(RECEIPT_FRAME_HEIGHT_CLASS);
  });

  it('leaves the PDF branch alone — it has nothing to decode', async () => {
    mount();
    await pick('receipt.pdf', 'application/pdf');
    await waitFor(() => expect(screen.getByText('receipt.pdf')).toBeTruthy());
    // No image, so no reserved image box: the row is its own fixed content.
    expect(frames()).toHaveLength(0);
  });

  it('shows no upload percentage (that is LX-06, not this slice)', async () => {
    mount();
    await pick('a.jpg');
    await waitFor(() => expect(frames()).toHaveLength(1));
    expect(document.body.textContent ?? '').not.toMatch(/\d\s*%/);
    expect(document.querySelector('progress,[role="progressbar"]')).toBeNull();
  });
});

// ── 4 · every surface, both locales ─────────────────────────────────────────

describe.each(['uz', 'ru'] as const)('image states render in %s', (lng) => {
  beforeEach(async () => { await i18n.changeLanguage(lng); });

  it('on the review screen', async () => {
    apiGet.mockResolvedValue({ data: REVIEW() });
    renderAt('/tenant/payments/pay-1/review', '/tenant/payments/:paymentId/review', <PaymentReviewPage />);
    await screen.findByTestId('receipt-frame');

    const loading = i18n.getResource(lng, 'translation', 'receipt.imageLoading') as string;
    const failed = i18n.getResource(lng, 'translation', 'receipt.imageFailed') as string;
    expect(within(frame()).getByText(loading)).toBeTruthy();
    fireEvent.error(frameImg());
    expect(within(frame()).getByText(failed)).toBeTruthy();
  });
});
