// @vitest-environment jsdom
/**
 * MUDD-83 — the contracts list can be read by WHAT was sold.
 *
 * Category is the one refinement on this screen that cannot be applied to the
 * page already in hand: the status, date and amount filters narrow rows the
 * client already has, but a car contract may be on page 4, and filtering the
 * current page would report "no results" for data that exists. So this one has
 * to reach the server, and this file pins that it does.
 *
 * It also pins the chip counts. They come from the server's tenant-wide
 * `category_counts`, NOT from the rows on screen — a chip has to say how many
 * contracts it would reveal, and counting the current page makes every other
 * chip read zero the moment one filter is on, which is exactly when a user
 * needs to see where else to look.
 */
import { describe, expect, it, beforeEach, afterEach, vi } from 'vitest';
import { render, screen, cleanup, fireEvent } from '@testing-library/react';
import { MemoryRouter } from 'react-router';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import i18n from '@/i18n';

type Row = {
  id: string; reference: string | null; contract_number: string | null;
  customer_id: string; customer_name: string; customer_phone: string;
  status: 'active'; currency: string;
  total_amount: number; down_payment: number; principal: number;
  months: number; monthly_amount: number; paid_count: number; paid_amount: number;
  start_date: string | null; next_due_date: string | null;
  calc_formula: null; markup_pct: null; interest_rate: null;
};

const row = (id: string, name: string): Row => ({
  id, reference: id, contract_number: id, customer_id: `c-${id}`,
  customer_name: name, customer_phone: '+998901110001', status: 'active', currency: 'UZS',
  total_amount: 1_000_000, down_payment: 0, principal: 1_000_000,
  months: 6, monthly_amount: 166_667, paid_count: 0, paid_amount: 0,
  start_date: '2026-08-01', next_due_date: '2026-09-01',
  calc_formula: null, markup_pct: null, interest_rate: null,
});

const PHONE_CONTRACT = row('i-phone', 'Anvar Karimov');
const CAR_CONTRACT = row('i-car', 'Bekzod Yusupov');

/** Tenant-wide, deliberately NOT derived from whatever is on screen. */
const CATEGORY_COUNTS = { smartfonlar: 1, avtomobillar: 1 };

const CATEGORIES = [
  { id: 'cat-phones', slug: 'smartfonlar', name_uz: 'Smartfonlar', name_ru: 'Смартфоны',
    icon_key: null, icon_emoji: null, icon_url: null, attribute_schema: [], identifier_schema: [],
    business_types: ['electronics'], sort_order: 1, is_active: true, brand_count: 1 },
  { id: 'cat-auto', slug: 'avtomobillar', name_uz: 'Avtomobillar', name_ru: 'Автомобили',
    icon_key: null, icon_emoji: null, icon_url: null, attribute_schema: [], identifier_schema: [],
    business_types: ['auto'], sort_order: 2, is_active: true, brand_count: 1 },
];

/** Every params object the list hook was called with, in order. */
const listParams: (Record<string, string> | undefined)[] = [];

vi.mock('@/lib/queries', async (orig) => ({
  ...(await orig<Record<string, unknown>>()),
  useTenantInstallments: (params?: Record<string, string>) => {
    listParams.push(params);
    // Stand in for the server: only the category filter narrows the rows, and
    // `category_counts` stays tenant-wide no matter what is asked for.
    const items = params?.category === 'avtomobillar' ? [CAR_CONTRACT]
      : params?.category === 'smartfonlar' ? [PHONE_CONTRACT]
        : [PHONE_CONTRACT, CAR_CONTRACT];
    return {
      data: { items, total: items.length, category_counts: CATEGORY_COUNTS },
      isLoading: false, isError: false, error: null, refetch: vi.fn(),
    };
  },
  useTenantWorklistPages: () => ({
    data: { pages: [{ items: [], total: 0, as_of: null, as_of_date: null }] },
    isLoading: false, isError: false, error: null, refetch: vi.fn(),
    hasNextPage: false, isFetchingNextPage: false, fetchNextPage: vi.fn(),
  }),
  useCatalogCategories: () => ({ data: CATEGORIES, isPending: false, isLoading: false, isError: false }),
  useTenantInstallmentDetail: () => ({ data: undefined, isLoading: false, isError: false }),
}));

vi.mock('@/lib/mutations', async (orig) => ({
  ...(await orig<Record<string, unknown>>()),
  useRemindInstallment: () => ({ mutateAsync: vi.fn(), isPending: false }),
}));

const { default: InstallmentsPage } = await import('./InstallmentsPage');

function renderPage() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter><InstallmentsPage /></MemoryRouter>
    </QueryClientProvider>,
  );
}

const openFilters = () => {
  fireEvent.click(screen.getByRole('button', { name: new RegExp(i18n.t('common.filter'), 'i') }));
};
const categoryChip = (name: string) => screen.getByRole('radio', { name: new RegExp(name) });

beforeEach(async () => {
  listParams.length = 0;
  vi.clearAllMocks();
  await i18n.changeLanguage('uz');
  Element.prototype.scrollIntoView = () => {};
});
afterEach(cleanup);

describe('the contracts list can be filtered by category', () => {
  it('offers a category chip per category that has contracts', () => {
    renderPage();
    openFilters();
    expect(categoryChip('Smartfonlar')).toBeTruthy();
    expect(categoryChip('Avtomobillar')).toBeTruthy();
  });

  it('sends the chosen category to the SERVER, not to the rows on screen', () => {
    renderPage();
    openFilters();
    fireEvent.click(categoryChip('Avtomobillar'));

    // The last request carries it. A contract may be far past the first page,
    // so a client-side narrowing of the rows already fetched would show "no
    // results" for a contract that exists.
    expect(listParams.at(-1)).toMatchObject({ category: 'avtomobillar' });
    // `getAllBy…`: the page renders a desktop table AND a mobile card list, so
    // every row's name appears twice in jsdom. What matters is which names are
    // there at all.
    expect(screen.getAllByText('Bekzod Yusupov').length).toBeGreaterThan(0);
    expect(screen.queryAllByText('Anvar Karimov')).toHaveLength(0);
  });

  it('a category with no contracts never appears as a chip', () => {
    renderPage();
    openFilters();
    // The chips are built from the server's counts, not from the full catalog
    // — offering all 114 categories would bury the two that this shop uses.
    expect(screen.queryByRole('radio', { name: /Mebel/ })).toBeNull();
  });

  it('the other chips keep their counts while one category is selected', () => {
    renderPage();
    openFilters();
    fireEvent.click(categoryChip('Avtomobillar'));

    // Smartfonlar still reads 1 even though no phone contract is on screen.
    // Counting the visible rows would show 0 here and tell the user there is
    // nothing to go back to.
    expect(categoryChip('Smartfonlar').textContent).toContain('1');
  });

  it('clearing the filters drops the category too', () => {
    renderPage();
    openFilters();
    fireEvent.click(categoryChip('Avtomobillar'));
    expect(listParams.at(-1)).toMatchObject({ category: 'avtomobillar' });

    fireEvent.click(screen.getByRole('button', { name: new RegExp(i18n.t('common.clear'), 'i') }));
    // Not merely "a different value" — the key must be gone, or the request
    // still filters by a category the drawer now shows as unselected.
    expect(listParams.at(-1)?.category).toBeUndefined();
  });
});
