// @vitest-environment jsdom
/**
 * MUDD-83 — the dashboard says what the shop actually sells.
 *
 * The status donut answers "how are the contracts going". It cannot answer
 * "what should I stock next month", and that is the question an owner opens
 * this screen with. The breakdown answers it with money as well as counts:
 * three phone contracts and one car contract are four contracts either way,
 * but the car is the business.
 */
import { describe, expect, it, beforeEach, afterEach, vi } from 'vitest';
import { render, screen, cleanup, within } from '@testing-library/react';
import { MemoryRouter } from 'react-router';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import i18n from '@/i18n';

const CATEGORIES = [
  { id: 'cat-phones', slug: 'smartfonlar', name_uz: 'Smartfonlar', name_ru: 'Смартфоны',
    icon_key: null, icon_emoji: null, icon_url: null, attribute_schema: [], identifier_schema: [],
    business_types: ['electronics'], sort_order: 1, is_active: true, brand_count: 1 },
  { id: 'cat-auto', slug: 'avtomobillar', name_uz: 'Avtomobillar', name_ru: 'Автомобили',
    icon_key: null, icon_emoji: null, icon_url: null, attribute_schema: [], identifier_schema: [],
    business_types: ['auto'], sort_order: 2, is_active: true, brand_count: 1 },
];

let byCategory: {
  category: string; count: number; amount_uzs: number;
  units_sold?: number; contract_count?: number; amount_by_currency?: Record<string, number>;
}[] = [];

const overview = () => ({
  tenant: { id: 't1', name: 'Shop', status: 'active' },
  debt_total_uzs: 0, overdue_uzs: 0, due_today_uzs: 0, pending_receipts: 0,
  customers_count: 0, active_products_count: 0,
  installments_by_status: { active: 1, overdue: 0, completed: 0 },
  installments_by_category: byCategory,
});

vi.mock('@/lib/queries', async (orig) => ({
  ...(await orig<Record<string, unknown>>()),
  useTenantOverview: () => ({ data: overview(), isLoading: false, isError: false, error: null, refetch: vi.fn() }),
  useTenantPayments: () => ({ data: { items: [], total: 0 }, isLoading: false, isError: false, error: null, refetch: vi.fn() }),
  useTenantWorklist: () => ({ data: { items: [], total: 0, as_of: null, as_of_date: null }, isLoading: false, isError: false, error: null, refetch: vi.fn() }),
  useCatalogCategories: () => ({ data: CATEGORIES, isPending: false, isLoading: false, isError: false }),
  useTenantInstallmentDetail: () => ({ data: undefined, isLoading: false, isError: false }),
}));

vi.mock('@/lib/mutations', async (orig) => ({
  ...(await orig<Record<string, unknown>>()),
  useCreateInstallment: () => ({ mutateAsync: vi.fn(), isPending: false }),
  useReplaceInstallment: () => ({ mutateAsync: vi.fn(), isPending: false }),
  useCreateGuarantor: () => ({ mutateAsync: vi.fn(), isPending: false }),
  useCreateCustomer: () => ({ mutateAsync: vi.fn(), isPending: false }),
}));

const { default: OverviewPage } = await import('./OverviewPage');

function renderPage() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter><OverviewPage /></MemoryRouter>
    </QueryClientProvider>,
  );
}

/** The breakdown card, found by its heading rather than a test id. */
const card = () =>
  screen.getByRole('heading', { name: i18n.t('tenantOverview.byCategory') })
    .closest('section') as HTMLElement;

beforeEach(async () => {
  vi.clearAllMocks();
  await i18n.changeLanguage('uz');
  Element.prototype.scrollIntoView = () => {};
  byCategory = [
    { category: 'avtomobillar', count: 1, amount_uzs: 90_000_000 },
    { category: 'smartfonlar', count: 3, amount_uzs: 6_000_000 },
  ];
});
afterEach(cleanup);

describe('the dashboard breaks the book down by category', () => {
  it('names each category in the active language', () => {
    renderPage();
    expect(within(card()).getByText('Avtomobillar')).toBeTruthy();
    expect(within(card()).getByText('Smartfonlar')).toBeTruthy();
  });

  it('keeps the server’s order — biggest by money first, not by count', () => {
    renderPage();
    const labels = within(card()).getAllByText(/Avtomobillar|Smartfonlar/)
      .map((el) => el.textContent);
    // Three phone contracts outnumber one car contract; the car is still the
    // bigger part of the business, and re-sorting client-side would hide that.
    expect(labels).toEqual(['Avtomobillar', 'Smartfonlar']);
  });

  it('shows money AND contract count for each category', () => {
    renderPage();
    // Money is the answer to "what is my book made of"; the count is the answer
    // to "how many customers is that". Neither substitutes for the other.
    expect(within(card()).getByText(/90[\s ]?000[\s ]?000/)).toBeTruthy();
    expect(within(card()).getByText(i18n.t('tenantOverview.categoryContracts', { count: 3 }))).toBeTruthy();
  });

  it('falls back to the slug when the catalog row was retired', () => {
    byCategory = [{ category: 'a-retired-slug', count: 1, amount_uzs: 1_000_000 }];
    renderPage();
    // History outlives the catalog. Dropping the line, or rendering a blank
    // label, would quietly remove real money from the breakdown.
    expect(within(card()).getByText('a-retired-slug')).toBeTruthy();
  });

  it('says so plainly when nothing is categorised yet', () => {
    byCategory = [];
    renderPage();
    // Not an empty card and not a zero bar: both read as "this shop sold
    // nothing", which is a different claim from "nothing carries a category".
    expect(within(card()).getByText(i18n.t('tenantOverview.byCategoryEmpty'))).toBeTruthy();
  });
});

describe('MUDD-141 — units sold is the primary metric, contract count secondary', () => {
  it('shows units sold AND contract count as two distinctly-labelled numbers', () => {
    byCategory = [
      { category: 'smartfonlar', count: 1, contract_count: 1, units_sold: 5, amount_uzs: 6_000_000 },
    ];
    renderPage();
    // Both numbers must be present, each with the word that says what it is —
    // "5 dona sotildi" and "1 ta shartnoma" — never a bare "5 / 1".
    expect(within(card()).getByText(i18n.t('tenantOverview.categoryUnits', { count: 5 }))).toBeTruthy();
    expect(within(card()).getByText(i18n.t('tenantOverview.categoryContracts', { count: 1 }))).toBeTruthy();
  });

  it('does not show a units row for a stale cached payload with no units_sold', () => {
    // The overview response sits behind a 30s read-through cache with no
    // version segment, so a request right after deploy can still come back
    // in the old shape: no `units_sold`, no `contract_count`, only `count`.
    byCategory = [{ category: 'smartfonlar', count: 3, amount_uzs: 6_000_000 }];
    renderPage();
    // No units line — showing "0 dona sotildi" would be a lie, not a fallback.
    expect(within(card()).queryByText(/dona sotildi/)).toBeNull();
    // Contract count still reads correctly from the legacy `count` field.
    expect(within(card()).getByText(i18n.t('tenantOverview.categoryContracts', { count: 3 }))).toBeTruthy();
  });

  it('still shows the amount_by_currency hint next to a category (no regression)', () => {
    byCategory = [
      {
        category: 'smartfonlar', count: 3, contract_count: 3, units_sold: 5, amount_uzs: 6_000_000,
        amount_by_currency: { UZS: 6_000_000, USD: 200 },
      },
    ];
    renderPage();
    expect(within(card()).getByText(/200/)).toBeTruthy();
    expect(within(card()).getByText(/USD/)).toBeTruthy();
  });
});
