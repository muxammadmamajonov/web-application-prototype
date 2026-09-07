// @vitest-environment jsdom
/**
 * FB-011 — the installment list has no row menu.
 *
 * Every row carried a "⋯" whose entire contents were "Batafsil" (open this row)
 * and "Eslatish" (send a reminder). The first duplicated the row's own job and
 * the second is a scheduled behaviour the shop does not drive by hand. The
 * founder rejected the menu for MVP.
 *
 * This file previously pinned QA-012 — that sibling kebabs must have distinct
 * accessible names — on this surface. That rule still holds wherever a kebab
 * remains (customers, products, payments, broadcasts); it simply has nothing to
 * say here now. What replaces it is stronger: no menu may come back, and none
 * may be left behind invisibly in the accessibility tree.
 *
 * Two rows in the fixture deliberately share a customer name, so a regression
 * that reintroduced per-row controls would still be caught by the uniqueness
 * check below rather than passing on distinct names.
 */
import { describe, expect, it, beforeEach, afterEach, vi } from 'vitest';
import { cleanup, render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import i18n from '@/i18n';

/** Five contracts; rows 1 and 2 are the same customer with different contracts. */
const ROWS = [
  ['c1', 'MDD-2026-0001', 'Aziz Karimov'],
  ['c2', 'MDD-2026-0002', 'Aziz Karimov'],
  ['c3', 'MDD-2026-0003', 'Dilnoza Rahimova'],
  ['c4', 'MDD-2026-0004', 'Sardor Alimov'],
  ['c5', 'MDD-2026-0005', 'Nodira Yusupova'],
].map(([id, contract_number, customer_name], i) => ({
  id, reference: `R${1000 + i}`, contract_number, customer_id: `cu${i}`,
  customer_name, customer_phone: null,
  status: 'active', is_overdue: false,
  total_amount: 6_000_000, down_payment: 0, principal: 6_000_000, markup_amount: 0,
  calc_formula: 'none', markup_pct: null, interest_rate: null,
  months: 6, monthly_amount: 1_000_000, paid_count: 1, paid_amount: 1_000_000,
  start_date: '2026-01-05', next_due_date: '2026-08-05',
  created_at: '2026-01-05T09:00:00Z',
}));

vi.mock('@/store/session', () => ({
  useSession: (sel: (s: unknown) => unknown) => sel({ activeRole: () => 'owner' }),
}));
vi.mock('@/lib/queries', async (orig) => ({
  ...(await orig<Record<string, unknown>>()),
  useTenantInstallments: () => ({
    data: { items: ROWS, total: ROWS.length }, isLoading: false, isError: false, error: null, refetch: vi.fn(),
  }),
  // The collections view is an infinite query with a different shape; both
  // branches serve the same rows so the assertions do not depend on which
  // status filter the page happens to default to.
  useTenantWorklistPages: () => ({
    data: {
      pages: [{
        items: ROWS, total: ROWS.length, overdue_count: 0,
        due_today_count: ROWS.length, as_of_date: '2026-08-05',
      }],
    },
    isLoading: false, isError: false, error: null, refetch: vi.fn(),
    hasNextPage: false, fetchNextPage: vi.fn(), isFetchingNextPage: false,
  }),
  useTenantInstallmentDetail: () => ({ data: undefined, isLoading: false }),
}));
vi.mock('@/lib/mutations', async (orig) => ({
  ...(await orig<Record<string, unknown>>()),
  useRemindInstallment: () => ({ mutateAsync: vi.fn(), isPending: false }),
  useRecordTenantPayment: () => ({ mutateAsync: vi.fn(), isPending: false }),
  useCancelInstallment: () => ({ mutateAsync: vi.fn(), isPending: false }),
  useRescheduleInstallment: () => ({ mutateAsync: vi.fn(), isPending: false }),
}));

import TenantInstallmentsPage from './InstallmentsPage';

const LOCALES = ['uz', 'ru'] as const;

function renderPage() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={['/uz/tenant/installments']}>
        <TenantInstallmentsPage />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

/** Every row kebab, split by layout — the desktop table and the mobile card
 *  list are both in the DOM under jsdom, and only one is ever visible. */
function kebabsByLayout(container: HTMLElement) {
  const all = Array.from(container.querySelectorAll<HTMLButtonElement>('button[aria-haspopup="menu"]'));
  const grid = screen.getByRole('grid');
  return {
    desktop: all.filter((b) => grid.contains(b)),
    mobile: all.filter((b) => !grid.contains(b)),
  };
}

const nameOf = (b: HTMLElement) => b.getAttribute('aria-label') ?? '';

/** Anything that behaves like a row-level menu, however it is built. */
function menuLikeControls(container: HTMLElement) {
  return Array.from(container.querySelectorAll<HTMLElement>(
    '[aria-haspopup="menu"], [role="menu"], [role="menuitem"]',
  ));
}

describe.each(LOCALES)('the installment list in %s', (lng) => {
  beforeEach(async () => { cleanup(); await i18n.changeLanguage(lng); });
  afterEach(async () => { cleanup(); await i18n.changeLanguage('uz'); });

  it('renders no row menu on either layout', () => {
    const { container } = renderPage();
    expect(kebabsByLayout(container).desktop).toHaveLength(0);
    expect(kebabsByLayout(container).mobile).toHaveLength(0);
  });

  it('leaves nothing menu-shaped in the accessibility tree', () => {
    const { container } = renderPage();
    expect(menuLikeControls(container)).toHaveLength(0);
    expect(screen.queryAllByRole('menu')).toHaveLength(0);
    expect(screen.queryAllByRole('menuitem')).toHaveLength(0);
  });

  it('offers no per-row "open details" or "remind" control', () => {
    const { container } = renderPage();
    const t = i18n.getFixedT(lng);
    const names = Array.from(container.querySelectorAll('button')).map(nameOf).filter(Boolean);
    expect(names).not.toContain(t('common.viewDetails'));
    expect(names).not.toContain(t('installments.remind'));
    for (const n of names) expect(n).not.toMatch(new RegExp(t('common.actions'), 'i'));
  });

  it('still reaches the contract — the row itself is the control', () => {
    renderPage();
    const rows = screen.getAllByRole('button').filter((b) =>
      (b.textContent ?? '').includes(ROWS[0]!.contract_number));
    expect(rows.length).toBeGreaterThan(0);
    // Keyboard-operable, not a click-only div.
    expect(rows[0]!.getAttribute('tabindex') ?? '0').not.toBe('-1');
  });

  it('shows every contract, so nothing was lost with the menu', () => {
    renderPage();
    for (const row of ROWS) {
      expect(screen.getAllByText(new RegExp(row.contract_number)).length).toBeGreaterThan(0);
    }
  });
});

describe('the contract detail surface', () => {
  beforeEach(async () => { cleanup(); await i18n.changeLanguage('uz'); });
  afterEach(cleanup);

  it('declares exactly one contract-PDF action in its source', async () => {
    // FB-010: the action was rendered twice — once in the footer as the primary
    // action and again at the foot of the body — so two controls competed for
    // the same job with independent loading states.
    const { readFileSync } = await import('node:fs');
    const { resolve } = await import('node:path');
    const src = readFileSync(resolve(__dirname, 'InstallmentsPage.tsx'), 'utf8');
    const detail = src.slice(src.indexOf('export function InstallmentDetailModal'));
    const body = detail.slice(0, detail.indexOf('function LifecycleSection'));
    expect(body.match(/openContractPdf\(d\.id\)/g) ?? []).toHaveLength(1);
    expect(body.match(/installments\.contractPdf/g) ?? []).toHaveLength(1);
  });
});
