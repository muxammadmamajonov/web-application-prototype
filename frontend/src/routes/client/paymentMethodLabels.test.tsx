// @vitest-environment jsdom
/**
 * OBS-003 — S-52's payment history printed `p.method.toUpperCase()`, so an
 * Uzbek or Russian customer read "CASH" / "CARD" / "TRANSFER" in a Telegram
 * screen that is otherwise entirely in their own language.
 *
 * Every peer surface already localizes this through the `tenantSettings.pm_*`
 * bundle — the tenant payment ledger, the tenant contract detail, the platform
 * payment drawer. The mapping was duplicated verbatim in two of them, so the
 * fix is one shared helper (`paymentMethodLabel` in `@/lib/format`) that all
 * three call, not a third copy on the customer page.
 *
 * These tests assert RENDERED text in both locales, so a re-hand-rolled mapping
 * would have to reproduce the bundle exactly to pass. The last test is the
 * source guard that keeps the number of implementations at one.
 */
import { describe, expect, it, beforeEach, afterEach, vi } from 'vitest';
import { cleanup, render, screen, within } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import i18n from '@/i18n';
import { api } from '@/lib/api';
import { installDevMock } from '@/lib/devMock';
import { paymentMethodLabel } from '@/lib/format';

const LOCALES = ['uz', 'ru'] as const;
/** Every method the product actually issues (cf. `METHODS` in the tenant ledger). */
const METHODS = ['cash', 'card', 'transfer', 'click', 'payme'] as const;
const savedAdapter = api.defaults.adapter;

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

const base = {
  id: 'i1',
  tenant: { id: 't1', name: 'Shop', logo: null, slug: 'shop' },
  contract_number: 'C-1', reference: null, status: 'active',
  principal: '1000000', paid_amount: '0', remaining: '1000000',
  monthly_amount: '100000', total_amount: '1000000', down_payment: '0',
  months: 10, paid_count: 0, start_date: '2026-01-10', next_due_date: '2026-08-10',
  headline: null, items: [], schedule: [],
};
const payment = (method: unknown, n = 1) => ({
  id: `p${n}`, amount: '100000', method, status: 'approved',
  paid_at: '2026-01-15T10:00:00Z', external_ref: null, receipt_image_url: null,
});

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
  return heading.closest('section')!;
}

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

describe.each(LOCALES)('S-52 payment methods in %s', (lng) => {
  beforeEach(async () => { await i18n.changeLanguage(lng); });

  it.each(METHODS)('renders %s with its localized name', async (method) => {
    OVERRIDE = { ...base, payments: [payment(method)] };
    renderDetail();
    const section = await history();
    const expected = i18n.getFixedT(lng)(`tenantSettings.pm_${method}`) as string;
    expect(expected, 'the bundle actually has this method').toBeTruthy();
    expect(within(section).getAllByText(expected).length,
      `${method} rendered as "${expected}"`).toBeGreaterThan(0);
  });

  it('leaks no raw enum token for any method it can render', async () => {
    OVERRIDE = { ...base, payments: METHODS.map((m, i) => payment(m, i)) };
    renderDetail();
    const section = await history();
    const text = section.textContent ?? '';
    // `CLICK`/`PAYME` are the upper-cased ENUM, not the brand names Click/Payme.
    for (const raw of ['CASH', 'CARD', 'TRANSFER', 'CLICK', 'PAYME']) {
      expect(text, `the raw enum ${raw} reached the customer`).not.toContain(raw);
    }
  });

  it('never renders an empty method cell for an unusable value', async () => {
    OVERRIDE = { ...base, payments: [payment(''), payment(null, 2), payment(undefined, 3)] };
    renderDetail();
    const section = await history();
    // The em-dash placeholder the rest of the app uses for "no value".
    expect(within(section).getAllByText('—').length).toBe(3);
  });

  it('degrades an unknown method to a readable token rather than blank', async () => {
    OVERRIDE = { ...base, payments: [payment('crypto')] };
    renderDetail();
    const section = await history();
    expect(within(section).getAllByText('CRYPTO').length).toBe(1);
  });
});

describe('the mapping has exactly one implementation', () => {
  const SRC = resolve(__dirname, '../..');

  it('is defined once, in @/lib/format', () => {
    const helper = readFileSync(resolve(SRC, 'lib/format.ts'), 'utf8');
    expect(helper).toContain('export function paymentMethodLabel');
  });

  it.each([
    'routes/tenant/PaymentsPage.tsx',
    'routes/tenant/InstallmentsPage.tsx',
    'routes/client/InstallmentDetailPage.tsx',
    // H21 — these three still showed the raw enum after the tenant/ surfaces
    // above were fixed; a neighbouring folder being clean doesn't mean these were.
    'routes/staff/PaymentsPage.tsx',
    'routes/staff/TodayPage.tsx',
    'routes/tenant/OverviewPage.tsx',
  ])('%s calls the shared helper instead of re-declaring one', (rel) => {
    const src = readFileSync(resolve(SRC, rel), 'utf8');
    expect(src, `${rel} still declares a local methodLabel`)
      .not.toMatch(/function\s+methodLabel\s*\(/);
    expect(src, `${rel} still upper-cases a raw method enum`)
      .not.toMatch(/\.method\.toUpperCase\(\)/);
  });
});

describe('paymentMethodLabel itself', () => {
  const t = i18n.getFixedT('uz');

  it.each(METHODS)('maps %s to the bundle entry', (m) => {
    expect(paymentMethodLabel(t as never, m)).toBe(t(`tenantSettings.pm_${m}`));
  });

  it('normalises casing so a shouted enum still resolves', () => {
    expect(paymentMethodLabel(t as never, 'CASH')).toBe(t('tenantSettings.pm_cash'));
  });

  it.each([null, undefined, '', '   '])('returns the placeholder for %p', (v) => {
    expect(paymentMethodLabel(t as never, v as never)).toBe('—');
  });

  it('falls back to the upper-cased token for an unmapped method', () => {
    expect(paymentMethodLabel(t as never, 'crypto')).toBe('CRYPTO');
  });
});
