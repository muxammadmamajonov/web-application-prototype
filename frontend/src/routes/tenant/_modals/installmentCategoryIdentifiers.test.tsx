// @vitest-environment jsdom
/**
 * MUDD-98 — a category can declare per-unit identifier fields
 * (`catalog_categories.identifier_schema`, e.g. phones: IMEI 1 required,
 * IMEI 2 optional) that the backend has enforced since MUDD-85 (PR #1015),
 * but the wizard never rendered them: a phone-shop line 400'd on submit with
 * no visible reason, since the form never asked for IMEI 1 in the first
 * place.
 *
 * RED (before this change): picking a category with a schema showed nothing
 * beyond the free-text single-identifier field; a required key was silently
 * never collected.
 * GREEN (after): the schema's own fields render under the category picker,
 * a blank required one blocks submit with an inline, field-named message
 * (never the generic "Nimadir notoʻgʻri ketdi"), a valid one reaches the API
 * as `items[].identifiers`, and a server-side duplicate 400 binds back to
 * the exact field that caused it.
 */
import { describe, expect, it, beforeEach, afterEach, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, within } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router';
import i18n from '@/i18n';

const CUSTOMERS = [{ id: 'c-ali', full_name: 'Ali Valiyev', phone: '+998901110001', status: 'active' }];

const category = (id: string, slug: string, uz: string, ru: string, identifierSchema: unknown[]) => ({
  id, slug, name_uz: uz, name_ru: ru,
  icon_key: null, icon_emoji: null, icon_url: null,
  attribute_schema: [], identifier_schema: identifierSchema,
  business_types: [], sort_order: 1, is_active: true, brand_count: 1,
});

const PHONES = category('cat-phones', 'smartfonlar', 'Smartfonlar', 'Смартфоны', [
  { key: 'imei1', label_uz: 'IMEI 1', label_ru: 'IMEI 1', type: 'text', required: true, max_len: 17 },
  { key: 'imei2', label_uz: 'IMEI 2', label_ru: 'IMEI 2', type: 'text', required: false, max_len: 17 },
]);
const NO_SCHEMA = category('cat-other', 'boshqa', 'Boshqa', 'Другое', []);

let createInstallment = vi.fn(async () => ({ id: 'i1', contract_number: 'C-1' }));

vi.mock('@/lib/queries', async (orig) => ({
  ...(await orig<Record<string, unknown>>()),
  useTenantCustomers: () => ({ data: { items: CUSTOMERS, total: 1 }, isLoading: false, isError: false }),
  useTenantGuarantors: () => ({ data: { items: [], total: 0 }, isLoading: false, isError: false }),
  useTenantSettings: () => ({ data: undefined, isLoading: false, isError: false }),
  checkImei: vi.fn(async () => ({ serial: '', in_tenant: false, in_tenant_count: 0, cross_tenant_count: 0 })),
  useCatalogCategories: () => ({ data: [PHONES, NO_SCHEMA], isPending: false, isLoading: false, isError: false }),
  useCatalogBrands: () => ({ data: [], isLoading: false, isError: false }),
  useCatalogModels: () => ({ data: [], isLoading: false, isError: false }),
}));

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

function submitContract() {
  const label = new RegExp(i18n.t('installments.createContract'), 'i');
  for (let i = 0; i < 6; i += 1) {
    const done = screen.queryByRole('button', { name: label });
    if (done) { fireEvent.click(done); return; }
    fireEvent.click(next());
  }
  throw new Error('never reached the final wizard step');
}

const categoryCombo = () =>
  within(editors()[0]).getByRole('combobox', {
    name: new RegExp(i18n.t('installments.itemCategory'), 'i'),
  });

function pickCategory(name: string) {
  fireEvent.click(categoryCombo());
  fireEvent.mouseDown(within(screen.getByRole('option', { name })).getByRole('button'));
}

function fillNameAndPrice(name: string, price: string) {
  const editor = editors()[0];
  fireEvent.change(within(editor).getByPlaceholderText(i18n.t('installments.itemNamePh')),
    { target: { value: name } });
  fireEvent.change(within(editor).getByLabelText(new RegExp(i18n.t('installments.unitPrice'), 'i')),
    { target: { value: price } });
}

beforeEach(async () => {
  vi.clearAllMocks();
  createInstallment = vi.fn(async () => ({ id: 'i1', contract_number: 'C-1' }));
  await i18n.changeLanguage('uz');
  globalThis.sessionStorage?.clear();
  globalThis.localStorage?.clear();
  Element.prototype.scrollIntoView = () => {};
});
afterEach(cleanup);

describe('a category can require its own identifier fields', () => {
  it('renders no extra fields for a category with an empty schema', () => {
    open(); toItemsStep();
    pickCategory('Boshqa');
    expect(screen.queryByLabelText(/IMEI 1/)).toBeNull();
  });

  it('renders the schema\'s own fields once a category with one is picked', () => {
    open(); toItemsStep();
    pickCategory('Smartfonlar');
    expect(screen.getByLabelText(/IMEI 1/)).toBeTruthy();
    expect(screen.getByLabelText(/IMEI 2/)).toBeTruthy();
  });

  it('a required field left blank blocks submit with an inline, field-named message — not the generic error screen', async () => {
    open(); toItemsStep();
    pickCategory('Smartfonlar');
    fillNameAndPrice('iPhone', '5000000');
    // IMEI 2 is optional and left blank; IMEI 1 (required) is also left blank.

    submitContract();

    await vi.waitFor(() => {
      expect(screen.getByRole('alert').textContent).toMatch(/IMEI 1/);
    });
    expect(createInstallment).not.toHaveBeenCalled();
    // Never the generic "something went wrong" fallback.
    expect(screen.queryByText(i18n.t('errors.unknown'))).toBeNull();
  });

  it('a filled required field reaches the API as items[].identifiers', async () => {
    open(); toItemsStep();
    pickCategory('Smartfonlar');
    fillNameAndPrice('iPhone', '5000000');
    fireEvent.change(screen.getByLabelText(/IMEI 1/), { target: { value: '352099001761481' } });

    submitContract();

    await vi.waitFor(() => expect(createInstallment).toHaveBeenCalled());
    const payload = createInstallment.mock.calls[0][0] as {
      body: { items: { identifiers?: Record<string, string> }[] };
    };
    expect(payload.body.items[0].identifiers).toEqual({ imei1: '352099001761481' });
  });

  it('the optional field is omitted from the payload when left blank', async () => {
    open(); toItemsStep();
    pickCategory('Smartfonlar');
    fillNameAndPrice('iPhone', '5000000');
    fireEvent.change(screen.getByLabelText(/IMEI 1/), { target: { value: '352099001761481' } });

    submitContract();

    await vi.waitFor(() => expect(createInstallment).toHaveBeenCalled());
    const payload = createInstallment.mock.calls[0][0] as {
      body: { items: { identifiers?: Record<string, string> }[] };
    };
    expect(payload.body.items[0].identifiers).not.toHaveProperty('imei2');
  });

  it('a duplicate-identifier 400 binds back to the field that caused it, not a generic banner', async () => {
    createInstallment = vi.fn(async () => {
      throw {
        response: { status: 400, data: { detail: 'category_identifier_duplicate:imei1' } },
      };
    });
    open(); toItemsStep();
    pickCategory('Smartfonlar');
    fillNameAndPrice('iPhone', '5000000');
    fireEvent.change(screen.getByLabelText(/IMEI 1/), { target: { value: '352099001761481' } });

    submitContract();

    await vi.waitFor(() => {
      expect(screen.getByText(i18n.t('identifier.category_identifier_duplicate'))).toBeTruthy();
    });
    // Back on the items step, at the offending row — not a step-3/terms banner.
    expect(editors().length).toBe(1);
  });
});
