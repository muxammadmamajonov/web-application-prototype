// @vitest-environment jsdom
/**
 * FB-004 / FB-006 — one product at a time, and an identifier that means something.
 *
 * The items step opened a second empty product form before the first had a name
 * or a price, so a shop stood at a counter holding two half-entered products in
 * their head. And the identifier was one free-text box labelled "IMEI /
 * Shartnoma raqami" — two different things with two different rules, so neither
 * was enforced.
 *
 * Now: exactly one expanded editor, "Add product" unavailable until it is
 * finished, finished lines collapsed to a one-row summary with Edit and Remove,
 * and an identifier that declares its kind and is validated under it.
 *
 * The cross-cutting Enter policy is re-checked here too: Enter must not add a
 * product, and must not advance the wizard.
 */
import { describe, expect, it, beforeEach, afterEach, vi } from 'vitest';
import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router';
import i18n from '@/i18n';

const CUSTOMERS = [{ id: 'c-ali', full_name: 'Ali Valiyev', phone: '+998901110001', status: 'active' }];
const VALID_IMEI = '352099001761481';

vi.mock('@/lib/queries', async (orig) => ({
  ...(await orig<Record<string, unknown>>()),
  useTenantCustomers: () => ({ data: { items: CUSTOMERS, total: 1 }, isLoading: false, isError: false }),
  useTenantGuarantors: () => ({ data: { items: [], total: 0 }, isLoading: false, isError: false }),
  useTenantSettings: () => ({ data: undefined, isLoading: false, isError: false }),
  checkImei: vi.fn(async () => ({ serial: '', in_tenant: false, in_tenant_count: 0, cross_tenant_count: 0 })),
}));

const createInstallment = vi.fn(async () => ({ id: 'i1', contract_number: 'C-1' }));
vi.mock('@/lib/mutations', async (orig) => ({
  ...(await orig<Record<string, unknown>>()),
  useCreateInstallment: () => ({ mutateAsync: createInstallment, isPending: false }),
  useReplaceInstallment: () => ({ mutateAsync: vi.fn(), isPending: false }),
  useCreateGuarantor: () => ({ mutateAsync: vi.fn(), isPending: false }),
  useCreateCustomer: () => ({ mutateAsync: vi.fn(async () => ({ id: 'c-new' })), isPending: false }),
  useResendCustomerInvite: () => ({ mutateAsync: vi.fn(), isPending: false }),
}));

vi.mock('@/store/session', () => ({
  useSession: (sel?: (s: unknown) => unknown) => {
    const state = { activeTenantId: 't1', me: { id: 'u1' }, activeRole: () => 'owner' };
    return sel ? sel(state) : state;
  },
}));

const { InstallmentModal } = await import('@/routes/tenant/_modals/InstallmentModal');

function open() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter><InstallmentModal open onClose={vi.fn()} /></MemoryRouter>
    </QueryClientProvider>,
  );
}

const next = () => screen.getByRole('button', { name: /keyingisi/i }) as HTMLButtonElement;
const editors = () => screen.queryAllByTestId('item-editor');
const summaries = () => screen.queryAllByTestId('item-summary');
const addBtn = () => screen.getByRole('button', { name: new RegExp(i18n.t('installments.addItem'), 'i') }) as HTMLButtonElement;
const setV = (el: HTMLElement, v: string) => fireEvent.change(el, { target: { value: v } });

/** Get past the customer step onto the items step. */
function toItemsStep() {
  fireEvent.click(screen.getByText('Ali Valiyev'));
  fireEvent.click(next());
}

/** Fill the open editor. Field order in the editor is name, qty, price. */
function priceInput(): HTMLInputElement {
  const numeric = within(editors()[0]).getAllByRole('textbox')
    .filter((i) => (i as HTMLInputElement).inputMode === 'numeric') as HTMLInputElement[];
  return numeric[numeric.length - 1];
}
function fillActive(name: string, price: string) {
  setV(within(editors()[0]).getByPlaceholderText(i18n.t('installments.itemNamePh')), name);
  setV(priceInput(), price);
}

beforeEach(async () => {
  vi.clearAllMocks();
  await i18n.changeLanguage('uz');
  // The wizard persists a session draft; without clearing it each test would
  // restore the previous test's products.
  globalThis.sessionStorage?.clear();
  globalThis.localStorage?.clear();
  // jsdom has no layout, so the Select's keyboard-scroll call is absent.
  Element.prototype.scrollIntoView = () => {};
});
afterEach(cleanup);

describe('one product at a time', () => {
  it('starts with exactly one editor and no summaries', () => {
    open(); toItemsStep();
    expect(editors()).toHaveLength(1);
    expect(summaries()).toHaveLength(0);
  });

  it('will not add a second product before the first is finished', () => {
    open(); toItemsStep();
    expect(addBtn().disabled).toBe(true);
    // …and says why, instead of leaving a dead button.
    expect(screen.getByText(new RegExp(i18n.t('installments.finishItemFirst').slice(0, 20)))).toBeTruthy();
  });

  it('is disabled for an unfinished line, not "busy" — no mutation is ever running here', () => {
    // MUDD-147: this button's disabled state comes purely from validation
    // (`!activeItem || !itemComplete(...)`), never from a pending mutation.
    // Feeding that into `loading` instead of `disabled` announced the button
    // as `aria-busy="true"` to screen readers forever, since no add-product
    // request is ever in flight.
    open(); toItemsStep();
    expect(addBtn().disabled).toBe(true);
    expect(addBtn().getAttribute('aria-busy')).not.toBe('true');
  });

  it('unlocks adding once the open product has a name and a price', () => {
    open(); toItemsStep();
    fillActive('Muzlatgich', '6000000');
    expect(addBtn().disabled).toBe(false);
  });

  it('offers Add as a full-size touch target on a phone', () => {
    // Measured in a browser at 320x568: the `sm` button renders 147x36, under
    // the 44px minimum, on the primary action of the step. `.tap-target` is
    // coarse-pointer-only, so this grows the hit area on a phone and leaves
    // the desktop button exactly as it was.
    open(); toItemsStep();
    expect(addBtn().className).toMatch(/\btap-target\b/);
  });

  it('collapses the finished product and opens a fresh editor', async () => {
    open(); toItemsStep();
    fillActive('Muzlatgich', '6000000');
    fireEvent.click(addBtn());
    await waitFor(() => expect(summaries()).toHaveLength(1));
    expect(editors()).toHaveLength(1);           // still exactly one
    const summary = summaries()[0];
    expect(summary.textContent).toContain('Muzlatgich');
    expect(summary.textContent).toMatch(/1 ×/);
    expect(summary.textContent).toMatch(/6\s?000\s?000/);
  });

  it('keeps Edit and Remove on a collapsed product', async () => {
    open(); toItemsStep();
    fillActive('Muzlatgich', '6000000');
    fireEvent.click(addBtn());
    await waitFor(() => expect(summaries()).toHaveLength(1));
    const summary = summaries()[0];
    expect(within(summary).getByRole('button', { name: /tahrirlash/i })).toBeTruthy();
    expect(within(summary).getByRole('button', { name: /olib tashlash/i })).toBeTruthy();
  });

  it('expands a collapsed product without losing its data', async () => {
    open(); toItemsStep();
    fillActive('Muzlatgich', '6000000');
    fireEvent.click(addBtn());
    await waitFor(() => expect(summaries()).toHaveLength(1));
    fireEvent.click(within(summaries()[0]).getByRole('button', { name: /tahrirlash/i }));
    expect(editors()).toHaveLength(1);
    const editor = editors()[0];
    expect((within(editor).getByPlaceholderText(i18n.t('installments.itemNamePh')) as HTMLInputElement).value)
      .toBe('Muzlatgich');
  });

  it('moves focus to the new editor, so typing continues where the caret is', async () => {
    open(); toItemsStep();
    fillActive('Muzlatgich', '6000000');
    fireEvent.click(addBtn());
    await waitFor(() => {
      const active = document.activeElement as HTMLInputElement | null;
      expect(active?.id).toMatch(/^item-name-/);
      expect(active?.value).toBe('');
    });
  });

  it('a double-tap on Add cannot create two blank products', async () => {
    open(); toItemsStep();
    fillActive('Muzlatgich', '6000000');
    const btn = addBtn();
    fireEvent.click(btn); fireEvent.click(btn); fireEvent.click(btn);
    await waitFor(() => expect(summaries()).toHaveLength(1));
    expect(editors()).toHaveLength(1);
  });

  it('…even when the taps land inside one frame, before React re-renders', async () => {
    // Testing Library flushes between events, so sequential clicks each see
    // fresh state and the completeness check alone would look sufficient. A
    // real double-tap gets no render in between — that is what the in-flight
    // guard is for, and this is the only way to exercise it.
    open(); toItemsStep();
    fillActive('Muzlatgich', '6000000');
    const btn = addBtn();
    await act(async () => {
      btn.dispatchEvent(new MouseEvent('click', { bubbles: true }));
      btn.dispatchEvent(new MouseEvent('click', { bubbles: true }));
      btn.dispatchEvent(new MouseEvent('click', { bubbles: true }));
    });
    await waitFor(() => expect(summaries()).toHaveLength(1));
    expect(editors()).toHaveLength(1);
    // Extra blank rows would be invisible — only the active one renders an
    // editor and only complete ones render a summary — so the position counter
    // is what actually shows how many lines exist.
    expect(editors()[0].textContent).toContain('#2');
    expect(editors()[0].textContent).not.toContain('#3');
  });
});

/** The value field, once a kind has been chosen. */
async function identifierInput(): Promise<HTMLInputElement> {
  return await waitFor(() => {
    const el = within(editors()[0]).getAllByLabelText(i18n.t('installments.identifier'))
      .find((e) => e.tagName === 'INPUT');
    if (!el) throw new Error('identifier input not rendered');
    return el as HTMLInputElement;
  });
}

/** Choose an identifier kind in the open editor.
 *  The shared Select commits on mousedown (so the trigger keeps focus), which
 *  a bare click never fires. */
function chooseKind(label: string) {
  fireEvent.click(within(editors()[0]).getByText(i18n.t('installments.identifierNone')));
  const option = screen.getAllByRole('option').find((o) => (o.textContent ?? '').includes(label));
  if (!option) throw new Error(`no option "${label}"`);
  fireEvent.mouseDown(option.querySelector('button') ?? option);
}

describe('the identifier declares what it is', () => {
  it('offers no identifier input until a kind is chosen', () => {
    open(); toItemsStep();
    const editor = editors()[0];
    const named = within(editor).queryAllByLabelText(i18n.t('installments.identifier'));
    // The kind picker is there; the value field is not, because a value with no
    // stated kind is the ambiguity this replaces.
    expect(named.filter((e) => e.tagName === 'INPUT')).toHaveLength(0);
  });

  it('names both kinds rather than one ambiguous box', () => {
    open(); toItemsStep();
    const editor = editors()[0];
    fireEvent.click(within(editor).getByText(i18n.t('installments.identifierNone')));
    expect(screen.getByText(i18n.t('identifier.kindImei'))).toBeTruthy();
    expect(screen.getByText(i18n.t('identifier.kindSerial'))).toBeTruthy();
  });

  it('rejects a bad IMEI inline, with a reason', async () => {
    open(); toItemsStep();
    chooseKind(i18n.t('identifier.kindImei'));
    const input = await identifierInput();
    setV(input, '352099001761482');            // one digit off the check digit
    expect(await screen.findByText(i18n.t('identifier.identifier_imei_checksum'))).toBeTruthy();
  });

  it('accepts a valid IMEI and clears the error', async () => {
    open(); toItemsStep();
    chooseKind(i18n.t('identifier.kindImei'));
    const input = await identifierInput();
    setV(input, '35-209900-176148-2');
    expect(await screen.findByText(i18n.t('identifier.identifier_imei_checksum'))).toBeTruthy();
    setV(input, VALID_IMEI);
    await waitFor(() =>
      expect(screen.queryByText(i18n.t('identifier.identifier_imei_checksum'))).toBeNull());
  });

  it('holds the error on the item, so Add stays unavailable', async () => {
    open(); toItemsStep();
    fillActive('Muzlatgich', '6000000');
    expect(addBtn().disabled).toBe(false);
    chooseKind(i18n.t('identifier.kindImei'));
    const input = await identifierInput();
    setV(input, '123');
    await waitFor(() => expect(addBtn().disabled).toBe(true));
  });

  it('caps the field at the length the column and API both enforce', async () => {
    open(); toItemsStep();
    chooseKind(i18n.t('identifier.kindSerial'));
    expect((await identifierInput()).maxLength).toBe(64);
  });
});

describe('Enter does not act', () => {
  it('Enter in the product name does not add a product', () => {
    open(); toItemsStep();
    fillActive('Muzlatgich', '6000000');
    const nameInput = within(editors()[0]).getByPlaceholderText(i18n.t('installments.itemNamePh'));
    fireEvent.keyDown(nameInput, { key: 'Enter' });
    fireEvent.keyDown(nameInput, { key: 'Enter' });
    expect(summaries()).toHaveLength(0);
    expect(editors()).toHaveLength(1);
  });

  it('Enter does not advance the wizard off the items step', () => {
    open(); toItemsStep();
    fillActive('Muzlatgich', '6000000');
    const nameInput = within(editors()[0]).getByPlaceholderText(i18n.t('installments.itemNamePh'));
    fireEvent.keyDown(nameInput, { key: 'Enter' });
    expect(editors()).toHaveLength(1);          // still on items
    expect(createInstallment).not.toHaveBeenCalled();
  });

  it('Enter in a numeric field is inert too', () => {
    open(); toItemsStep();
    fillActive('Muzlatgich', '6000000');
    fireEvent.keyDown(priceInput(), { key: 'Enter' });
    expect(summaries()).toHaveLength(0);
  });
});
