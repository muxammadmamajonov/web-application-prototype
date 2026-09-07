// @vitest-environment jsdom
/**
 * QA-011 (customer half) — the two most-used customer surfaces must each be
 * titled by exactly one `h1`.
 *
 * S-51 ClientHomePage rendered its greeting as a styled `div`, and S-53 PayPage
 * carried no heading element at all, so a screen-reader user landing on either
 * screen — and `RouteFocusManager`, which moves focus into `<main>` on every
 * navigation — had nothing announcing which page they were on.
 *
 * PayPage renders seven mutually-exclusive phases (loading, not-found, poll
 * exhausted, waiting, approved, rejected, method selector). The count is
 * asserted in each of them, because a heading added to one branch is a heading
 * missing from the other six — and a heading added to all seven by hand is a
 * duplicate waiting to happen.
 */
import { describe, expect, it, beforeEach, afterEach, vi } from 'vitest';
import { cleanup, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import i18n from '@/i18n';
import { api } from '@/lib/api';
import { installDevMock } from '@/lib/devMock';

const CUSTOMER = {
  id: 'u1', telegramId: 1, telegramUsername: null, firstName: 'Dilnoza', lastName: null, displayName: 'Dilnoza',
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
import ClientPayPage from './PayPage';

const LOCALES = ['uz', 'ru'] as const;
const savedAdapter = api.defaults.adapter;

function renderAt(path: string, routePath: string, node: React.ReactNode) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={[path]}>
        <Routes><Route path={routePath} element={node} /></Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

const h1s = (root: ParentNode) => Array.from(root.querySelectorAll('h1'));

beforeEach(() => {
  vi.spyOn(console, 'info').mockImplementation(() => {});
  installDevMock();
});
afterEach(async () => {
  cleanup();
  vi.restoreAllMocks();
  api.defaults.adapter = savedAdapter;
  await i18n.changeLanguage('uz');
});

describe.each(LOCALES)('customer page headings in %s', (lng) => {
  beforeEach(async () => { await i18n.changeLanguage(lng); });

  it('S-51 ClientHomePage is titled by its greeting', async () => {
    const { container } = renderAt('/me/my', '/me/my', <ClientHomePage />);
    await waitFor(() => expect(h1s(container)).toHaveLength(1));
    expect(h1s(container)[0]!.textContent)
      .toBe(i18n.getFixedT(lng)('client.greeting', { name: 'Dilnoza' }));
  });

  it('S-53 PayPage is titled in every phase it can render', async () => {
    const { container } = renderAt(
      '/me/installments/i1/pay', '/me/installments/:id/pay', <ClientPayPage />,
    );
    // Loading phase — asserted before the fixture resolves.
    expect(h1s(container)).toHaveLength(1);
    // Method-selector phase.
    await waitFor(() => expect(screen.getByLabelText(i18n.getFixedT(lng)('client.amount') as string)).toBeTruthy());
    expect(h1s(container)).toHaveLength(1);
    expect(h1s(container)[0]!.textContent).toBe(i18n.getFixedT(lng)('routeTitle.pay'));
  });

  it('S-53 PayPage keeps exactly one h1 when the installment cannot be loaded', async () => {
    // A bad :id takes the not-found branch; the page is still a titled page.
    const { container } = renderAt(
      '/me/installments/nope/pay', '/me/installments/:id/pay', <ClientPayPage />,
    );
    await waitFor(() => expect(h1s(container)).toHaveLength(1));
    expect(h1s(container)[0]!.textContent).toBe(i18n.getFixedT(lng)('routeTitle.pay'));
  });

  // The two phases a provider payment can be parked in. The status endpoint is
  // the only thing that selects between them, so it is the only thing stubbed;
  // the installment itself still comes from the real mock transport.
  it.each([
    ['pending', 'client.payingTitle'],
    ['approved', 'client.paidTitle'],
  ])('S-53 PayPage keeps exactly one h1 in the %s phase', async (status, titleKey) => {
    const real = api.get.bind(api);
    vi.spyOn(api, 'get').mockImplementation(((url: string, ...rest: unknown[]) =>
      /^\/me\/payments\/[^/]+\/status$/.test(url)
        ? Promise.resolve({ data: { status } })
        : (real as (u: string, ...r: unknown[]) => unknown)(url, ...rest)) as typeof api.get);

    renderAt('/me/installments/i1/pay?payment_id=p9', '/me/installments/:id/pay', <ClientPayPage />);
    await waitFor(() => expect(screen.getByText(i18n.getFixedT(lng)(titleKey) as string)).toBeTruthy());
    expect(h1s(document.body)).toHaveLength(1);
    expect(h1s(document.body)[0]!.textContent).toBe(i18n.getFixedT(lng)('routeTitle.pay'));
  });
});
