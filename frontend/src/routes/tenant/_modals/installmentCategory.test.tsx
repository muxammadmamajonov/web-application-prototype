// @vitest-environment jsdom
/**
 * MUDD-83 — a contract line says WHAT it sells, in one step.
 *
 * The old path was `business_type -> catalog -> brand -> model -> variant`:
 * four taps, and the tenant's segment gated the first one, so an
 * `electronics` shop was shown 15 of 114 categories and could never file a
 * sale under `avtomobillar`. A shop selling phones AND fridges was stuck.
 *
 * RED (before this change): the item editor had no category control at all,
 * and the submitted payload carried no `category` field.
 * GREEN (after): every row has a category select fed by the FULL category
 * list, its value reaches the API, and the catalog picker fills it in too so
 * the shop can see what it picked without submitting first.
 */
import { describe, expect, it, beforeEach, afterEach, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, within } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router';
import i18n from '@/i18n';

const CUSTOMERS = [{ id: 'c-ali', full_name: 'Ali Valiyev', phone: '+998901110001', status: 'active' }];

const category = (id: string, slug: string, uz: string, ru: string, businessTypes: string[]) => ({
  id, slug, name_uz: uz, name_ru: ru,
  icon_key: null, icon_emoji: null, icon_url: null,
  attribute_schema: [], identifier_schema: [],
  business_types: businessTypes, sort_order: 1, is_active: true, brand_count: 1,
});

const PHONES = category('cat-phones', 'smartfonlar', 'Smartfonlar', 'Смартфоны', ['electronics']);
const CARS = category('cat-auto', 'avtomobillar', 'Avtomobillar', 'Автомобили', ['auto']);
const RETIRED = { ...category('cat-old', 'pereferiya', 'Periferiya', 'Периферия', []), is_active: false };

/** What `useCatalogCategories` was called with, so the test can prove the
 *  segment is not being passed — that argument IS the reported bug. */
const categoriesCalledWith: unknown[] = [];

const BRAND = {
  id: 'brand-chevrolet', category_id: 'cat-auto', category_ids: ['cat-auto'], slug: 'chevrolet',
  name: 'Chevrolet', logo_url: null, sort_order: 1, is_active: true, model_count: 1,
};
const MODEL = {
  id: 'model-cobalt', brand_id: 'brand-chevrolet', category_id: 'cat-auto', slug: 'cobalt',
  name: 'Cobalt', release_year: 2024, image_url: null,
  attributes_template: { color: ['Oq'] }, sort_order: 1, is_active: true,
};

vi.mock('@/lib/queries', async (orig) => ({
  ...(await orig<Record<string, unknown>>()),
  useTenantCustomers: () => ({ data: { items: CUSTOMERS, total: 1 }, isLoading: false, isError: false }),
  useTenantGuarantors: () => ({ data: { items: [], total: 0 }, isLoading: false, isError: false }),
  // A shop whose segment is `electronics`. Under the old behaviour this is
  // exactly the tenant that could not sell a car.
  useTenantSettings: () => ({
    data: { profile: { business_type: 'electronics' } }, isLoading: false, isError: false,
  }),
  checkImei: vi.fn(async () => ({ serial: '', in_tenant: false, in_tenant_count: 0, cross_tenant_count: 0 })),
  useCatalogCategories: (...args: unknown[]) => {
    categoriesCalledWith.push(args[0]);
    return { data: [PHONES, CARS, RETIRED], isPending: false, isLoading: false, isError: false };
  },
  useCatalogBrands: (slug: string | null | undefined) => ({
    data: slug === 'avtomobillar' ? [BRAND] : [], isLoading: false, isError: false,
  }),
  useCatalogModels: (brandId: string | null | undefined) => ({
    data: brandId === 'brand-chevrolet' ? [MODEL] : [], isLoading: false, isError: false,
  }),
}));

const createInstallment = vi.fn(async () => ({ id: 'i1', contract_number: 'C-1' }));
vi.mock('@/lib/mutations', async (orig) => ({
  ...(await orig<Record<string, unknown>>()),
  useCreateInstallment: () => ({ mutateAsync: createInstallment, isPending: false }),
  useReplaceInstallment: () => ({ mutateAsync: vi.fn(), isPending: false }),
  useCreateGuarantor: () => ({ mutateAsync: vi.fn(), isPending: false }),
  useCreateCustomer: () => ({ mutateAsync: vi.fn(async () => ({ id: 'c-new' })), isPending: false }),
  useResendCustomerInvite: () => ({ mutateAsync: vi.fn(), isPending: false }),
  useProposeModel: () => ({ mutateAsync: vi.fn(), isPending: false }),
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

const editors = () => screen.queryAllByTestId('item-editor');
const next = () => screen.getByRole('button', { name: /keyingisi/i }) as HTMLButtonElement;

function toItemsStep() {
  fireEvent.click(screen.getByText('Ali Valiyev'));
  fireEvent.click(next());
}

/** Walk the remaining wizard steps and submit.
 *
 *  The wizard has five steps and only the last one shows the create button, so
 *  advance until it appears rather than hard-coding a click count — a step
 *  added or removed later should not silently turn this into a no-op test.
 */
function submitContract() {
  const label = new RegExp(i18n.t('installments.createContract'), 'i');
  for (let i = 0; i < 6; i += 1) {
    const done = screen.queryByRole('button', { name: label });
    if (done) { fireEvent.click(done); return; }
    fireEvent.click(next());
  }
  throw new Error('never reached the final wizard step');
}

/** The row has several comboboxes (category, identifier kind, …). `Field`
 *  wires `aria-labelledby` at its visible label, so name it. */
const categoryCombo = () =>
  within(editors()[0]).getByRole('combobox', {
    name: new RegExp(i18n.t('installments.itemCategory'), 'i'),
  });

function pickCategory(name: string) {
  fireEvent.click(categoryCombo());
  // `Select` commits on mouseDown, not click — it preventDefaults so the
  // trigger keeps focus while the portalled panel closes. A plain `click`
  // opens and closes the panel without ever selecting anything, which is the
  // silent no-op this comment exists to stop the next person rediscovering.
  fireEvent.mouseDown(within(screen.getByRole('option', { name })).getByRole('button'));
}

beforeEach(async () => {
  vi.clearAllMocks();
  categoriesCalledWith.length = 0;
  await i18n.changeLanguage('uz');
  globalThis.sessionStorage?.clear();
  globalThis.localStorage?.clear();
  Element.prototype.scrollIntoView = () => {};
});
afterEach(cleanup);

describe('a contract line records its category', () => {
  it('shows a category control on the item row', () => {
    open(); toItemsStep();
    expect(categoryCombo()).toBeTruthy();
  });

  it('offers EVERY category, not just the tenant’s own segment', () => {
    open(); toItemsStep();
    fireEvent.click(categoryCombo());

    // The reported bug, stated directly: an `electronics` shop must be able to
    // reach `Avtomobillar`.
    expect(screen.getByRole('option', { name: 'Avtomobillar' })).toBeTruthy();
    expect(screen.getByRole('option', { name: 'Smartfonlar' })).toBeTruthy();
  });

  it('never passes the tenant’s business_type to the category query', () => {
    open(); toItemsStep();
    // The API filters on this argument, and that filter is the mechanism that
    // hid 99 of 114 categories. The row's own list must be asked for WITHOUT
    // it. (`ProductPickerModal` still passes a segment when it is open — that
    // is the Products screen's behaviour and is covered elsewhere; what matters
    // here is that an unsegmented request is made at all.)
    expect(categoriesCalledWith).toContain(undefined);
  });

  it('hides a retired category', () => {
    open(); toItemsStep();
    fireEvent.click(categoryCombo());
    // `is_active: false` means it is no longer offered — and the API refuses
    // it with a 400, so showing it would only produce a dead end.
    expect(screen.queryByRole('option', { name: 'Periferiya' })).toBeNull();
  });

  it('sends the chosen category with the contract', async () => {
    open(); toItemsStep();
    pickCategory('Avtomobillar');
    const editor = editors()[0];
    fireEvent.change(within(editor).getByPlaceholderText(i18n.t('installments.itemNamePh')),
                     { target: { value: 'Nexia 3' } });
    fireEvent.change(within(editor).getByLabelText(new RegExp(i18n.t('installments.unitPrice'), 'i')),
                     { target: { value: '90000000' } });

    submitContract();

    await vi.waitFor(() => expect(createInstallment).toHaveBeenCalled());
    const payload = createInstallment.mock.calls[0][0] as {
      body: { items: { category: string | null; product_name_snapshot: string }[] };
    };
    expect(payload.body.items[0].category).toBe('avtomobillar');
    expect(payload.body.items[0].product_name_snapshot).toBe('Nexia 3');
  });

  it('a line with no category chosen sends null rather than an empty string', async () => {
    open(); toItemsStep();
    const editor = editors()[0];
    fireEvent.change(within(editor).getByPlaceholderText(i18n.t('installments.itemNamePh')),
                     { target: { value: 'Nomsiz narsa' } });
    fireEvent.change(within(editor).getByLabelText(new RegExp(i18n.t('installments.unitPrice'), 'i')),
                     { target: { value: '1000000' } });

    submitContract();

    await vi.waitFor(() => expect(createInstallment).toHaveBeenCalled());
    const payload = createInstallment.mock.calls[0][0] as {
      body: { items: { category: string | null }[] };
    };
    // `""` would be a slug the catalog does not know, and the API answers 400.
    // Absent is the honest shape for "not stated".
    expect(payload.body.items[0].category).toBeNull();
  });

  it('(MUDD-92 H1) an existing line keeps a since-retired category visible, not blank', () => {
    // The edit form prefills every line from what is already stored
    // (InstallmentModal's prefill effect), and `RETIRED` is excluded from
    // `categoryOptions` (see "hides a retired category" above). Before this
    // fix the Select's `current` lookup found nothing for `pereferiya` and
    // fell back to the placeholder — the field looked empty while the state
    // still held the slug, and there was no way to see or deliberately clear
    // what was about to be saved.
    const EDITING = {
      id: 'inst-1', reference: 'R1', contract_number: 'MDD-1',
      customer_id: 'c-ali', customer_name: 'Ali Valiyev', customer_phone: null,
      status: 'active', total_amount: 1_000_000, down_payment: 0,
      principal: 1_000_000, markup_amount: 0, calc_formula: 'none',
      markup_pct: null, interest_rate: null, months: 6, monthly_amount: 166_667,
      paid_count: 0, paid_amount: 0, start_date: '2026-01-01',
      next_due_date: '2026-02-01', created_at: '2026-01-01T00:00:00Z',
      items: [{
        id: 'item-1', product_id: null, catalog_model_id: null,
        product_name_snapshot: 'Eski sichqoncha', category: 'pereferiya',
        variant_label: null, serial_number: null, qty: 1, unit_price: 100_000,
        line_total: 100_000,
      }],
      guarantors: [], payments: [], schedule: [],
    };
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(
      <QueryClientProvider client={qc}>
        <MemoryRouter>
          <InstallmentModal open onClose={vi.fn()} editing={EDITING as never} />
        </MemoryRouter>
      </QueryClientProvider>,
    );
    // Step 0 (customer) is already satisfied by the prefill — just advance.
    fireEvent.click(next());

    expect(categoryCombo().textContent).toContain('pereferiya');
  });

  it('picking from the catalog fills the category in too', async () => {
    open(); toItemsStep();
    fireEvent.click(screen.getByRole('button',
      { name: new RegExp(i18n.t('installments.pickFromCatalog'), 'i') }));
    fireEvent.click(await screen.findByText('Avtomobillar'));
    fireEvent.click(await screen.findByText('Chevrolet'));
    fireEvent.click(await screen.findByText('Cobalt'));
    fireEvent.click(screen.getByRole('button', { name: i18n.t('catalog.pickConfirm') }));

    // The server would derive this anyway, but the shop should be able to SEE
    // what it picked without submitting the contract to find out.
    expect(categoryCombo().textContent).toContain('Avtomobillar');
  });
});
