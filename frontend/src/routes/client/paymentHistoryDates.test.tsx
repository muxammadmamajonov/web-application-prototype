// @vitest-environment jsdom
/**
 * OBS-001 — S-52's payment history printed the backend's raw timestamp.
 *
 * `GET /me/installments/{id}` serializes `paid_at` with `.isoformat()`, so the
 * value on the wire is `2026-01-15T10:00:00Z`. Every peer surface runs it
 * through `shortDate` — the tenant contract detail, both payment lists, the
 * platform payment drawer — and this one row rendered `{p.paid_at ?? '—'}`
 * straight into the DOM, in a customer-facing Telegram screen, in a format that
 * is neither Uzbek nor Russian.
 *
 * The surface was unrenderable until QA-009 fixed the mock, which is why the
 * defect was only observed afterwards; it is not a regression from that fix.
 *
 * `shortDate` already owns null, invalid input and the two locales, so the fix
 * is to call it — not to hand-roll parsing here. These tests assert the
 * rendered text, so a re-hand-rolled formatter would have to reproduce
 * `shortDate` exactly to pass.
 */
import { describe, expect, it, beforeEach, afterEach, vi } from 'vitest';
import { cleanup, render, screen, waitFor, within } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import i18n from '@/i18n';
import { api } from '@/lib/api';
import { installDevMock } from '@/lib/devMock';
import { shortDate } from '@/lib/format';

const LOCALES = ['uz', 'ru'] as const;
const savedAdapter = api.defaults.adapter;

/** Set to override the installment the page receives; null ⇒ real devMock. */
let OVERRIDE: Record<string, unknown> | null = null;
vi.mock('@/lib/queries', async (orig) => {
  const real = await orig<Record<string, unknown>>();
  return {
    ...real,
    useMyInstallment: (id?: string) => {
      const live = (real.useMyInstallment as (i?: string) => { data?: unknown })(id);
      return OVERRIDE ? { ...live, data: OVERRIDE, isLoading: false, isError: false } : live;
    },
  };
});

import ClientInstallmentDetailPage from './InstallmentDetailPage';

function renderDetail() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={['/me/installments/i1']}>
        <Routes><Route path="/me/installments/:id" element={<ClientInstallmentDetailPage />} /></Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

/** The payment-history section, found by its own heading. */
async function history(): Promise<HTMLElement> {
  const heading = await screen.findByText(i18n.t('client.paymentsHistory') as string);
  const section = heading.closest('section');
  expect(section, 'the payment-history section rendered').toBeTruthy();
  return section!;
}

/** An ISO-8601 date-time, i.e. exactly what must never reach a customer. */
const RAW_ISO = /\d{4}-\d{2}-\d{2}T\d{2}:\d{2}/;

beforeEach(() => {
  OVERRIDE = null;
  vi.spyOn(console, 'info').mockImplementation(() => {});
  installDevMock();
});
afterEach(async () => {
  cleanup();
  vi.restoreAllMocks();
  api.defaults.adapter = savedAdapter;
  await i18n.changeLanguage('uz');
});

describe.each(LOCALES)('S-52 payment history in %s', (lng) => {
  beforeEach(async () => { await i18n.changeLanguage(lng); });

  it('shows no raw ISO timestamp anywhere in the list', async () => {
    renderDetail();
    const section = await history();
    await waitFor(() => expect(within(section).getAllByRole('listitem').length).toBeGreaterThan(0));
    expect(section.textContent ?? '', 'a raw timestamp reached the customer').not.toMatch(RAW_ISO);
  });

  it('formats each payment date through the shared locale-aware formatter', async () => {
    const { data } = await api.get<{ payments: Array<{ paid_at: string }> }>('/me/installments/i1');
    expect(data.payments.length, 'the fixture has payments to format').toBeGreaterThan(0);
    // The fixture must still be serving the backend's own format, or this
    // asserts nothing about the defect.
    expect(data.payments[0]!.paid_at).toMatch(RAW_ISO);

    renderDetail();
    const section = await history();
    for (const p of data.payments) {
      const formatted = shortDate(p.paid_at);
      expect(formatted).not.toMatch(RAW_ISO);
      expect(within(section).getAllByText(formatted).length,
        `${p.paid_at} rendered as ${formatted}`).toBeGreaterThan(0);
    }
  });
});

describe('a missing or unusable timestamp degrades the way the rest of the app does', () => {
  const base = {
    id: 'i1',
    tenant: { id: 't1', name: 'Shop', logo: null, slug: 'shop' },
    contract_number: 'C-1', reference: null, status: 'active',
    principal: '1000000', paid_amount: '0', remaining: '1000000',
    monthly_amount: '100000', total_amount: '1000000', down_payment: '0',
    months: 10, paid_count: 0, start_date: '2026-01-10', next_due_date: '2026-08-10',
    headline: null, items: [], schedule: [],
  };
  const payment = (paid_at: unknown) => ({
    id: 'p1', amount: '100000', method: 'cash', status: 'approved',
    paid_at, external_ref: null, receipt_image_url: null,
  });

  it.each([
    ['null', null],
    ['an empty string', ''],
    ['unparseable text', 'not-a-date'],
  ])('renders the em-dash placeholder for %s', async (_label, value) => {
    OVERRIDE = { ...base, payments: [payment(value)] };
    renderDetail();
    const section = await history();
    expect(within(section).getAllByText('—').length).toBeGreaterThan(0);
    expect(section.textContent ?? '').not.toContain('not-a-date');
  });
});
