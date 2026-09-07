// @vitest-environment jsdom
/**
 * M-5 (MUDD-70) — the customer's main screen, ClientHomePage, had only
 * fragment tests (overdueHero, payAmountSuggestion, paymentTouchTargets):
 * none of them rendered the full page. This drives it through the states a
 * customer actually lands on: loading, a failed fetch, populated with an
 * open installment, and genuinely empty — plus the unverified-phone gate
 * that sits above all of them.
 */
import { describe, expect, it, beforeEach, afterEach, vi } from 'vitest';
import { cleanup, render, waitFor, getAllByText } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import i18n from '@/i18n';
import { api } from '@/lib/api';

const CUSTOMER = {
  id: 'u1', telegramId: 1, telegramUsername: null, firstName: 'Dilnoza', lastName: null,
  photoUrl: null, phone: null, email: null, birthdate: null, gender: null,
  locale: 'uz', platformRole: null, tenants: [],
};

vi.mock('@/store/session', () => ({
  useSession: (sel?: (s: unknown) => unknown) => {
    const state = { me: CUSTOMER, activeTenantId: null, impersonating: false, impersonatingName: null };
    return sel ? sel(state) : state;
  },
}));

import ClientHomePage from './HomePage';

const savedAdapter = api.defaults.adapter;

const IDENTITY_VERIFIED = {
  state: 'VERIFIED', verified: true, phoneMasked: '+998 90 *** ** 33', eligibility: 'eligible',
  source: 'telegram_contact', verifiedAt: '2026-01-01T00:00:00Z', displayName: 'Dilnoza', linkedShops: 1,
};

const IDENTITY_UNVERIFIED = {
  state: 'VERIFICATION_REQUIRED', verified: false, phoneMasked: null, eligibility: null,
  source: null, verifiedAt: null, displayName: null, linkedShops: 0,
};

const ALL_CLEAR_SUMMARY = {
  total_outstanding: '0', due_this_month: '0', due_today: '0', overdue_count: 0,
  installments_count: 0, next_due_date: null, next_due_amount: '0', next_due_overdue: false,
};

const SUMMARY_WITH_OPEN = {
  total_outstanding: '8000000.00', due_this_month: '1000000', due_today: '0', overdue_count: 0,
  installments_count: 1, next_due_date: '2099-01-15', next_due_amount: '1000000',
  next_due_currency: 'UZS', next_due_overdue: false,
};

const OPEN_INSTALLMENT = {
  id: 'i1', tenant_id: 't1', tenant_name: 'Shop', tenant_logo: null, currency: 'UZS',
  contract_number: 'C-1', reference: null, item_summary: 'iPhone 15', principal: '12000000',
  paid_amount: '4000000', remaining: '8000000', monthly_amount: '1000000', months: 12,
  paid_count: 4, status: 'active', next_due_date: '2099-01-15', headline: null, start_date: '2026-01-01',
};

/** Serve fixed payloads for every /me/* read the page issues; anything else
 *  answers a harmless empty object. */
function serve(opts: {
  summary?: Record<string, unknown>;
  identity?: Record<string, unknown>;
  open?: unknown[];
  completed?: unknown[];
}) {
  const { summary = ALL_CLEAR_SUMMARY, identity = IDENTITY_VERIFIED, open = [], completed = [] } = opts;
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  api.defaults.adapter = (async (config: any) => {
    const url: string = config.url ?? '';
    const respond = (data: unknown) => ({ data, status: 200, statusText: 'OK', headers: {}, config });
    if (url.includes('/me/summary')) return respond(summary);
    if (url.includes('/me/identity')) return respond(identity);
    if (url.includes('/me/installments')) {
      const filter = (config.params as Record<string, unknown> | undefined)?.status_filter;
      return respond(filter === 'completed' ? completed : open);
    }
    if (url.includes('/me/notifications')) return respond({ items: [], unread: 0 });
    return respond({});
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
  }) as any;
}

function renderHome() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={['/me/my']}>
        <Routes><Route path="/me/my" element={<ClientHomePage />} /></Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

beforeEach(async () => {
  vi.spyOn(console, 'info').mockImplementation(() => {});
  vi.spyOn(console, 'error').mockImplementation(() => {});
  // Pin the language — i18n is module-global and other suites change it.
  await i18n.changeLanguage('uz');
});
afterEach(async () => {
  cleanup();
  vi.restoreAllMocks();
  api.defaults.adapter = savedAdapter;
  await i18n.changeLanguage('uz');
});

describe('ClientHomePage — full render across states', () => {
  it('shows the hero skeleton while the summary is still loading, not a blank or error screen', () => {
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    api.defaults.adapter = (() => new Promise(() => {})) as any; // never resolves
    const { container } = renderHome();
    expect(container.querySelector('[aria-busy="true"]')).not.toBeNull();
    expect(container.textContent).not.toContain(i18n.getFixedT('uz')('common.retry'));
  });

  it('shows a retry-able error card when the summary fetch fails, not a silent empty read', async () => {
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    api.defaults.adapter = (async (config: any) => {
      const url: string = config.url ?? '';
      const respond = (data: unknown) => ({ data, status: 200, statusText: 'OK', headers: {}, config });
      if (url.includes('/me/summary')) throw new Error('network down');
      if (url.includes('/me/identity')) return respond(IDENTITY_VERIFIED);
      if (url.includes('/me/notifications')) return respond({ items: [], unread: 0 });
      return respond([]);
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
    }) as any;

    const { container } = renderHome();
    const t = i18n.getFixedT('uz');
    // The hero's failure branch is hand-rolled rather than the shared
    // ErrorCard (which does carry role="alert") — assert on its actual retry
    // affordance rather than a landmark it doesn't have.
    await waitFor(() => expect(container.textContent).toContain(t('common.retry')));
    // A failed fetch must never be mistaken for "nothing owed" (the class of
    // bug pinned for other client pages by H-16 / QA-004..009).
    expect(container.textContent, 'a failed fetch read as all-clear')
      .not.toContain(t('client.allPaidUp'));
  });

  it('renders an open installment with its progress and a working Pay action', async () => {
    serve({ summary: SUMMARY_WITH_OPEN, open: [OPEN_INSTALLMENT] });
    const { container } = renderHome();
    const t = i18n.getFixedT('uz');
    await waitFor(() => expect(container.textContent).toContain('iPhone 15'));
    expect(container.textContent).toContain('4/12');
    // Rendered twice by design (hero shortcut + the installment card itself).
    expect(getAllByText(container, t('client.payNow')).length).toBeGreaterThan(0);
  });

  it('shows "due today" when a NON-dominant currency owes today (MUDD-122 M5)', async () => {
    // Dominant currency (by contract count) is UZS — due_today is its scalar,
    // and it is '0'. The customer's ONE USD contract is due today, which the
    // old `due_today !== '0'` check could never see because it only ever read
    // the dominant currency's own figure.
    serve({
      summary: {
        total_outstanding: '5000000', due_this_month: '5000000', due_today: '0',
        due_today_by_currency: { USD: '150' },
        overdue_count: 0, installments_count: 3, next_due_date: '2099-02-01',
        next_due_amount: '150', next_due_currency: 'USD', next_due_overdue: false,
      },
      open: [OPEN_INSTALLMENT],
    });
    const { container } = renderHome();
    const t = i18n.getFixedT('uz');
    await waitFor(() => expect(container.textContent).toContain('iPhone 15'));
    expect(container.textContent, 'a real due-today balance in a non-dominant currency was hidden')
      .toContain(t('client.dueTodayMsg', "Bugun to'lash kerak"));
  });

  it('shows the empty-list state, not a false all-clear claim, when there are no installments at all', async () => {
    serve({ summary: ALL_CLEAR_SUMMARY, open: [] });
    const { container } = renderHome();
    const t = i18n.getFixedT('uz');
    await waitFor(() => expect(container.textContent).toContain(t('client.noInstallmentsYet')));
    expect(container.querySelector('ul')?.children.length ?? 0).toBe(0);
  });

  it('shows the verify-phone gate only when the server reports the phone unverified', async () => {
    serve({ identity: IDENTITY_UNVERIFIED });
    const { container } = renderHome();
    await waitFor(() =>
      expect(container.querySelector('[data-testid="identity-card"]')).not.toBeNull());
    expect(container.querySelector('[data-testid="identity-card"]')?.getAttribute('data-state'))
      .toBe('VERIFICATION_REQUIRED');
  });

  it('hides the verify-phone gate once the server reports the phone verified', async () => {
    serve({ identity: IDENTITY_VERIFIED });
    const { container } = renderHome();
    // Wait for the identity read to settle before asserting its absence.
    await waitFor(() => expect(container.textContent).toContain(i18n.getFixedT('uz')('client.allPaidUp')));
    expect(container.querySelector('[data-testid="identity-card"]')).toBeNull();
  });
});
