// @vitest-environment jsdom
/**
 * H-2 — a late customer must never be told their payments are done.
 *
 * THE DEFECT (2026-08-08 QA pass). `/api/me/summary` returned the next due row
 * filtered by `next_due_date >= today`, so a date already in the PAST matched
 * nothing and came back null. The hero then computed
 * `isAllClear = due_today === '0' && !next_due_date` and rendered:
 *
 *     "Hozir to'lov yo'q"  /  "To'lovlaringiz hamma qilib bo'ldi"
 *
 * to a customer 10 days late with 10,000,000 outstanding — while the shop's own
 * aging report counted them as overdue.
 *
 * Two independent things had to be true for that to happen, so both are pinned
 * here: the server now sends the past-due date plus an explicit
 * `next_due_overdue`, and the hero refuses to show the all-clear state while
 * any balance is outstanding — belt and braces, because either one alone would
 * have prevented it and neither was present.
 */
import { describe, expect, it, beforeEach, afterEach, vi } from 'vitest';
import { cleanup, render, waitFor } from '@testing-library/react';
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

/** Serve one fixed /me/summary payload; everything else answers empty. */
function serve(summary: Record<string, unknown>) {
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  api.defaults.adapter = (async (config: any) => {
    const url: string = config.url ?? '';
    const data = url.includes('/me/summary') ? summary
      : url.includes('/me/installments') ? []
        : url.includes('/me/notifications') ? { items: [], unread: 0 }
          : {};
    return { data, status: 200, statusText: 'OK', headers: {}, config };
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

const OVERDUE = {
  total_outstanding: '10000000.00', due_this_month: '0', due_today: '0',
  overdue_count: 0, installments_count: 1,
  next_due_date: '2026-07-29', next_due_amount: '1666667', next_due_overdue: true,
};

beforeEach(async () => {
  vi.spyOn(console, 'info').mockImplementation(() => {});
  // Pin the language. i18n is module-global and other suites change it, so
  // without this the assertions compare Uzbek strings against Russian output
  // and pass no matter what the component does — the exact way a guard test
  // becomes decorative.
  await i18n.changeLanguage('uz');
});
afterEach(async () => {
  cleanup();
  vi.restoreAllMocks();
  api.defaults.adapter = savedAdapter;
  await i18n.changeLanguage('uz');
});

describe('the client home hero when a payment is overdue', () => {
  it('never claims the customer is all paid up', async () => {
    serve(OVERDUE);
    const { container } = renderHome();
    await waitFor(() => expect(container.textContent).toContain('1 666 667'));
    const allPaidUp = i18n.getFixedT('uz')('client.allPaidUp');
    expect(container.textContent, 'a late customer was told their payments are done')
      .not.toContain(allPaidUp);
  });

  it('shows the amount owed rather than a dash', async () => {
    serve(OVERDUE);
    const { container } = renderHome();
    await waitFor(() => expect(container.textContent).toContain('1 666 667'));
  });

  it('labels the hero as overdue, not as an upcoming payment', async () => {
    serve(OVERDUE);
    const { container } = renderHome();
    const t = i18n.getFixedT('uz');
    await waitFor(() => expect(container.textContent).toContain(t('client.overduePayment')));
    expect(container.textContent).not.toContain(t('client.nextPayment'));
  });

  it('still refuses the all-clear state if the server omits the flag', async () => {
    // Defence in depth: an older/cached response without `next_due_overdue`
    // must not resurrect the bug, because a balance is still outstanding.
    serve({ ...OVERDUE, next_due_overdue: undefined, next_due_date: null, next_due_amount: '0' });
    const { container } = renderHome();
    const t = i18n.getFixedT('uz');
    // Wait for the HERO to settle, not merely for the page to have any text —
    // the header renders instantly, so asserting on that races the query and
    // the check passes before the component has decided anything.
    await waitFor(() => expect(container.textContent).toContain(t('client.noPaymentNow')));
    expect(container.textContent, 'all-clear shown while a balance is outstanding')
      .not.toContain(t('client.allPaidUp'));
  });
});

describe('the states that must keep working', () => {
  it('an upcoming payment still reads as the next payment', async () => {
    serve({ ...OVERDUE, next_due_date: '2099-01-15', next_due_overdue: false });
    const { container } = renderHome();
    const t = i18n.getFixedT('uz');
    await waitFor(() => expect(container.textContent).toContain(t('client.nextPayment')));
    expect(container.textContent).not.toContain(t('client.overduePayment'));
  });

  it('a customer who owes nothing still gets the all-clear', async () => {
    serve({
      total_outstanding: '0', due_this_month: '0', due_today: '0', overdue_count: 0,
      installments_count: 0, next_due_date: null, next_due_amount: '0',
      next_due_overdue: false,
    });
    const { container } = renderHome();
    await waitFor(() =>
      expect(container.textContent).toContain(i18n.getFixedT('uz')('client.allPaidUp')));
  });
});
