// @vitest-environment jsdom
/**
 * MUDD-83 — the new-installment wizard could not pick from the catalog at
 * all: every item row was free text, despite a doc-comment claiming a
 * "tenant products OR global catalog OR free-text" choice existed. The
 * catalog picker (`ProductPickerModal`, with its category/brand/model/variant
 * steps and segment toggle) was wired into the tenant Products page only —
 * never into contract creation, which is exactly where the reported
 * complaint happened ("mahsulot tanlay olmayapman shartnoma yaratishda").
 *
 * RED (before this fix): no "Katalogdan" affordance existed in the item
 * editor; `ProductPickerModal` was never imported by this file.
 * GREEN (after): the item editor opens the same catalog picker, and a
 * completed pick writes catalog_model_id + a brand/model snapshot onto the
 * active row — the exact shape the backend already hydrates category/brand
 * from server-side.
 */
import { describe, expect, it, beforeEach, afterEach, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, within } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router';
import i18n from '@/i18n';

const CUSTOMERS = [{ id: 'c-ali', full_name: 'Ali Valiyev', phone: '+998901110001', status: 'active' }];

const CATEGORY = {
  id: 'cat-auto', slug: 'avtomobillar', name_uz: 'Avtomobillar', name_ru: 'Автомобили',
  icon_key: null, icon_emoji: null, icon_url: null, attribute_schema: [], identifier_schema: [],
  business_types: [], sort_order: 1, is_active: true, brand_count: 1,
};
const BRAND = {
  id: 'brand-chevrolet', category_id: 'cat-auto', category_ids: ['cat-auto'], slug: 'chevrolet',
  name: 'Chevrolet', logo_url: null, sort_order: 1, is_active: true, model_count: 1,
};
const MODEL = {
  id: 'model-cobalt', brand_id: 'brand-chevrolet', category_id: 'cat-auto', slug: 'cobalt', name: 'Cobalt',
  release_year: 2024, image_url: null, attributes_template: { color: ['Oq', 'Qora'] }, sort_order: 1, is_active: true,
};

vi.mock('@/lib/queries', async (orig) => ({
  ...(await orig<Record<string, unknown>>()),
  useTenantCustomers: () => ({ data: { items: CUSTOMERS, total: 1 }, isLoading: false, isError: false }),
  useTenantGuarantors: () => ({ data: { items: [], total: 0 }, isLoading: false, isError: false }),
  // No business_type → the picker is unsegmented; segment behaviour is
  // covered separately in productPickerSegments.test.tsx.
  useTenantSettings: () => ({ data: undefined, isLoading: false, isError: false }),
  checkImei: vi.fn(async () => ({ serial: '', in_tenant: false, in_tenant_count: 0, cross_tenant_count: 0 })),
  useCatalogCategories: () => ({ data: [CATEGORY], isLoading: false, isError: false }),
  useCatalogBrands: (catSlug: string | null | undefined) => ({
    data: catSlug === 'avtomobillar' ? [BRAND] : [], isLoading: false, isError: false,
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

beforeEach(async () => {
  vi.clearAllMocks();
  await i18n.changeLanguage('uz');
  globalThis.sessionStorage?.clear();
  globalThis.localStorage?.clear();
  Element.prototype.scrollIntoView = () => {};
});
afterEach(cleanup);

describe('the new-contract item row can pull from the global catalog', () => {
  it('offers a "pick from catalog" affordance in the open item editor', () => {
    open(); toItemsStep();
    expect(screen.getByRole('button', { name: new RegExp(i18n.t('installments.pickFromCatalog'), 'i') })).toBeTruthy();
  });

  it('walking category → brand → model → variant writes brand + model onto the row', async () => {
    open(); toItemsStep();
    fireEvent.click(screen.getByRole('button', { name: new RegExp(i18n.t('installments.pickFromCatalog'), 'i') }));

    // Category step
    fireEvent.click(await screen.findByText('Avtomobillar'));
    // Brand step
    fireEvent.click(await screen.findByText('Chevrolet'));
    // Model step
    fireEvent.click(await screen.findByText('Cobalt'));
    // Variant step — confirm the seeded first option
    fireEvent.click(screen.getByRole('button', { name: i18n.t('catalog.pickConfirm') }));

    // The item name field is now filled from the catalog pick, not typed.
    const nameInput = within(editors()[0])
      .getByPlaceholderText(i18n.t('installments.itemNamePh')) as HTMLInputElement;
    expect(nameInput.value).toBe('Chevrolet Cobalt');
    // Picker's "linked" state replaces the free-text hint with the picked
    // model + variant, proving catalog_model_id (not just the name) was set.
    expect(within(editors()[0]).queryByText(i18n.t('installments.pickFromCatalogHint'))).toBeNull();
    expect(within(editors()[0]).getByText(/Oq/)).toBeTruthy();
  });

  it('a name filled in by the catalog pick still requires a price before the line can be added', async () => {
    open(); toItemsStep();
    const addBtn = () => screen.getByRole('button', { name: new RegExp(i18n.t('installments.addItem'), 'i') }) as HTMLButtonElement;
    expect(addBtn().disabled).toBe(true);

    fireEvent.click(screen.getByRole('button', { name: new RegExp(i18n.t('installments.pickFromCatalog'), 'i') }));
    fireEvent.click(await screen.findByText('Avtomobillar'));
    fireEvent.click(await screen.findByText('Chevrolet'));
    fireEvent.click(await screen.findByText('Cobalt'));
    fireEvent.click(screen.getByRole('button', { name: i18n.t('catalog.pickConfirm') }));

    // Name is filled, but price is still 0 — the picker fills identity, not
    // the sale terms, so the line stays incomplete until priced.
    expect(addBtn().disabled).toBe(true);
  });
});
