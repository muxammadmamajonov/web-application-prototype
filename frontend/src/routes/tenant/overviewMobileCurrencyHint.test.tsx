// @vitest-environment jsdom
/**
 * MUDD-121 (HIGH-2) — the mobile hero must state a second currency too.
 *
 * (MUDD-114) `debt_total_uzs` / `due_today_uzs` / `overdue_uzs` are each the
 * PRIMARY currency's share, with the full `*_by_currency` map beside them.
 * The desktop KPI cards (`hidden md:grid`, this page's `KpiCard`s) already
 * render that second currency via `secondaryCurrencyHint`. The mobile hero —
 * `MobileOverview`, `md:hidden` — is the primary surface in this Telegram
 * Mini App, and it carried the same three figures through bare `money()`
 * with no hint at all: a mixed-currency shop's USD debt was fully invisible
 * on a phone while the desktop dashboard (`≥1024`) showed it correctly.
 *
 * MUDD-116 QA flagged exactly this gap: no test proves the mobile/desktop
 * duality for a page built from two parallel JSX trees (jsdom renders BOTH —
 * `hidden`/`md:hidden` are CSS, not conditional rendering — so a query must
 * be scoped to the breakpoint it claims to test, or it proves nothing).
 */
import { describe, expect, it, beforeEach, afterEach, vi } from 'vitest';
import { render, screen, cleanup, within } from '@testing-library/react';
import { MemoryRouter } from 'react-router';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import i18n from '@/i18n';

const TENANT_NAME = 'Aralash valyuta do‘koni';

let mixedCurrency = true;

const overview = () => ({
  tenant: { id: 't1', name: TENANT_NAME, status: 'active' },
  primary_currency: 'UZS',
  debt_total_uzs: 5_000_000,
  debt_total_by_currency: mixedCurrency ? { UZS: 5_000_000, USD: 1_500 } : { UZS: 5_000_000 },
  overdue_uzs: 1_000_000,
  overdue_by_currency: mixedCurrency ? { UZS: 1_000_000, USD: 300 } : { UZS: 1_000_000 },
  due_today_uzs: 500_000,
  due_today_by_currency: mixedCurrency ? { UZS: 500_000, USD: 150 } : { UZS: 500_000 },
  pending_receipts: 0,
  installments_by_status: { active: 9, overdue: 2, completed: 1 },
  installments_by_category: [],
});

vi.mock('@/lib/queries', async (orig) => ({
  ...(await orig<Record<string, unknown>>()),
  useTenantOverview: () => ({ data: overview(), isLoading: false, isError: false, error: null, refetch: vi.fn() }),
  useTenantPayments: () => ({ data: { items: [], total: 0 }, isLoading: false, isError: false, error: null, refetch: vi.fn() }),
  useTenantWorklist: () => ({ data: { items: [], total: 0, as_of: null, as_of_date: null }, isLoading: false, isError: false, error: null, refetch: vi.fn() }),
  useCatalogCategories: () => ({ data: [], isPending: false, isLoading: false, isError: false }),
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

/** The `md:hidden` mobile hero tree — jsdom renders it alongside the desktop
 *  tree, so every assertion about "mobile" must be scoped to this element.
 *  Anchored on the tenant name (unique to `MobileOverview`) rather than a
 *  bare class query, so it can't accidentally match an unrelated element
 *  that happens to carry the same single class. */
const mobileHero = () => screen.getByText(TENANT_NAME).closest('.md\\:hidden') as HTMLElement;

/** The `hidden md:grid` desktop KPI section — scoped the same way. */
const desktopKpis = () => document.querySelector('section[class*="md:grid"]') as HTMLElement;

beforeEach(async () => {
  vi.clearAllMocks();
  cleanup();
  await i18n.changeLanguage('uz');
  Element.prototype.scrollIntoView = () => {};
  mixedCurrency = true;
});
afterEach(cleanup);

describe('mixed-currency shop', () => {
  it('states the USD debt in the mobile hero, not just on desktop', () => {
    renderPage();
    expect(mobileHero()).toBeTruthy();
    // Debt, due-today AND overdue each carry their own USD hint in the hero.
    expect(within(mobileHero()).getAllByText(/USD/).length).toBeGreaterThanOrEqual(3);
    expect(within(mobileHero()).getByText(/1[\s ]?500/)).toBeTruthy();
  });

  it('states the USD overdue amount in the mobile hero AND the overdue stat tile', () => {
    renderPage();
    // Both the compact hero line and the dedicated overdue MStat tile show
    // the second currency — two independent surfaces on the same screen.
    const overdueMentions = within(mobileHero()).getAllByText(/300/);
    expect(overdueMentions.length).toBeGreaterThanOrEqual(2);
  });

  it('states the USD due-today amount in the mobile hero', () => {
    renderPage();
    expect(within(mobileHero()).getByText(/150/)).toBeTruthy();
  });

  it('still states every secondary currency on desktop (regression guard)', () => {
    renderPage();
    expect(desktopKpis()).toBeTruthy();
    expect(within(desktopKpis()).getByText(/1[\s ]?500/)).toBeTruthy();
    expect(within(desktopKpis()).getByText(/300/)).toBeTruthy();
    expect(within(desktopKpis()).getByText(/150/)).toBeTruthy();
  });
});

describe('single-currency shop (the overwhelming majority)', () => {
  it('shows no currency hint anywhere in the mobile hero', () => {
    mixedCurrency = false;
    renderPage();
    // secondaryCurrencyHint returns undefined with nothing to add — the
    // dashboard must look exactly as it did before this fix.
    expect(within(mobileHero()).queryByText(/USD/)).toBeNull();
  });

  it('shows no currency hint on desktop either', () => {
    mixedCurrency = false;
    renderPage();
    expect(within(desktopKpis()).queryByText(/USD/)).toBeNull();
  });
});
