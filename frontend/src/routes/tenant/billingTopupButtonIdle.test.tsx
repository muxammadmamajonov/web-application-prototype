// @vitest-environment jsdom
/**
 * MUDD-133 — the balance card's "To'ldirish" button on `BillingPage` (the
 * button that OPENS `TopUpModal`) must never render a spinner or `aria-busy`
 * at idle: it takes no `loading` prop and its only state is whether a
 * receiving card exists, which is not "in flight".
 */
import { describe, expect, it, beforeEach, afterEach, vi } from 'vitest';
import { render, screen, cleanup } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import i18n from '@/i18n';

type Q<T> = { data: T | undefined; isLoading: boolean; isError: boolean; error: unknown; refetch: () => void };
const ok = <T,>(data: T): Q<T> => ({ data, isLoading: false, isError: false, error: null, refetch: vi.fn() });

vi.mock('@/store/session', () => ({
  useSession: (sel?: (s: unknown) => unknown) => {
    const state = { activeTenantId: 't1', impersonating: false, me: null, activeRole: () => 'owner', exitTenant: vi.fn() };
    return sel ? sel(state) : state;
  },
}));

vi.mock('@/lib/queries', async (orig) => ({
  ...(await orig<Record<string, unknown>>()),
  useTenantBilling: () => ok({ plan_code: 'pro', status: 'active', active_installments: 0, base_price: 0, projected: null, suggestion: null }),
  useTenantInvoices: () => ok([]),
  usePublicPlans: () => ok([]),
  useTenantTopups: () => ok([]),
  useTenantTopupInfo: () => ok({
    balance: 0,
    currency: 'UZS',
    terms: { uz: '', ru: '' },
    cards: [{ id: 'c1', number: '5614000000002802', bank: 'Kapital Bank', settlement_currency: 'UZS' }],
    cards_withheld: false,
    providers: { payme: false, click: false },
    can_topup: true,
  }),
}));

import BillingPage from './BillingPage';

function renderBilling() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={['/uz/tenant/billing']}>
        <Routes><Route path="/:locale/tenant/billing" element={<BillingPage />} /></Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

beforeEach(async () => { await i18n.changeLanguage('uz'); });
afterEach(cleanup);

describe('idle: 0 UZS balance, zero user or network action', () => {
  it('the "To\'ldirish" button shows no spinner and no aria-busy', () => {
    renderBilling();
    const btn = screen.getByRole('button', { name: new RegExp(i18n.t('topup.title'), 'i') });
    expect(btn.getAttribute('aria-busy')).not.toBe('true');
    expect(btn.querySelector('.animate-spin')).toBeNull();
    expect((btn as HTMLButtonElement).disabled).toBe(false);
  });
});
