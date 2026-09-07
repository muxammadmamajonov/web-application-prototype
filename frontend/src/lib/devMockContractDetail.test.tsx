// @vitest-environment jsdom
/**
 * The dev mock must serve the contract-detail surfaces, not crash them.
 *
 * `devMock.ts` is the transport the UI phases used to review every screen at
 * every breakpoint. Two KEEP surfaces could not render through it at all:
 *
 *   * `MY_INSTALLMENT_DETAIL` omitted `payments`, which the real backend always
 *     returns as a list, so `ClientInstallmentDetailPage` threw
 *     `Cannot read properties of undefined (reading 'filter')`;
 *   * there was no `/tenant/installments/{id}` handler, so the tenant detail
 *     modal received the hybrid-empty fallback (an array carrying .items/.total)
 *     instead of a contract.
 *
 * Both surfaces therefore had no visual, interaction, focus-restoration or
 * destructive-confirmation coverage. This file drives the REAL pages through the
 * REAL mock adapter inside the REAL route error boundary — mocking any of the
 * three would assert nothing about the harness the audits actually used.
 *
 * The fixtures are checked against the shipped TypeScript contracts, so a mock
 * that drifts from `MyInstallmentDetail` / `InstallmentDetail` fails here rather
 * than at render time in someone's browser.
 */
import { describe, expect, it, beforeEach, afterEach, vi } from 'vitest';
import { cleanup, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import i18n from '@/i18n';
import { api } from '@/lib/api';
import { installDevMock } from '@/lib/devMock';
import { RouteErrorBoundary } from '@/components/RouteErrorBoundary';
import ClientInstallmentDetailPage from '@/routes/client/InstallmentDetailPage';
import { InstallmentDetailModal } from '@/routes/tenant/InstallmentsPage';

/** The heading the route error boundary renders when a page throws. */
const BOUNDARY = 'Bu sahifa yuklanmadi';

const savedAdapter = api.defaults.adapter;

function wrap(path: string, element: React.ReactNode, routePath: string) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={[path]}>
        <RouteErrorBoundary>
          <Routes><Route path={routePath} element={element} /></Routes>
        </RouteErrorBoundary>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

beforeEach(async () => {
  vi.spyOn(console, 'error').mockImplementation(() => {});
  vi.spyOn(console, 'info').mockImplementation(() => {});
  await i18n.changeLanguage('uz');
  installDevMock();
});
afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  api.defaults.adapter = savedAdapter;
});

// ── the fixtures must match the shipped contracts ───────────────────────────

async function mockGet(url: string): Promise<Record<string, unknown>> {
  const r = await api.get<Record<string, unknown>>(url);
  return r.data;
}

describe('devMock fixtures conform to the backend contract', () => {
  it('GET /me/installments/{id} returns payments as a list', async () => {
    const d = await mockGet('/me/installments/i1');
    expect(Array.isArray(d.payments), 'payments must be an array, not undefined').toBe(true);
    for (const key of ['schedule', 'items']) expect(Array.isArray(d[key]), key).toBe(true);
    expect(typeof d.monthly_amount).toBe('string');
  });

  it('GET /tenant/installments/{id} returns a contract, not the empty fallback', async () => {
    const d = await mockGet('/tenant/installments/c1');
    // The old fallback was `Object.assign([], { items: [], total: 0 })`.
    expect(Array.isArray(d), 'a contract detail must not be an array').toBe(false);
    expect(d.id).toBe('c1');
    for (const key of ['guarantors', 'items', 'payments']) {
      expect(Array.isArray(d[key]), `${key} must be an array`).toBe(true);
    }
    // InstallmentRow numerics — the modal does arithmetic on these.
    for (const key of ['principal', 'paid_amount', 'months', 'monthly_amount', 'paid_count']) {
      expect(typeof d[key], key).toBe('number');
    }
    expect(['draft', 'active', 'overdue', 'completed', 'cancelled']).toContain(d.status);
  });

  it('an unknown contract id still resolves to a contract shape', async () => {
    const d = await mockGet('/tenant/installments/does-not-exist');
    expect(Array.isArray(d)).toBe(false);
    expect(Array.isArray(d.payments)).toBe(true);
  });
});

// ── the real surfaces must render ───────────────────────────────────────────

describe('S-52 ClientInstallmentDetailPage renders through the mock', () => {
  it('does not reach the route error boundary', async () => {
    wrap('/me/installments/i1', <ClientInstallmentDetailPage />, '/me/installments/:id');
    await waitFor(() => expect(screen.queryByText('Demo Electronics')).toBeTruthy());
    expect(document.body.textContent).not.toContain(BOUNDARY);
  });

  it('renders the KEEP content: shop, progress and an action', async () => {
    wrap('/me/installments/i1', <ClientInstallmentDetailPage />, '/me/installments/:id');
    await waitFor(() => expect(screen.queryByText('Demo Electronics')).toBeTruthy());
    // 6 of 12 paid — derived from the contract the mock now serves.
    expect(document.body.textContent).toMatch(/6\s*\/\s*12/);
    expect(screen.getAllByRole('button').length).toBeGreaterThan(0);
  });

  it('reads `payments` rather than throwing on it', async () => {
    // The original crash was `inst.payments.filter(...)` on undefined. Proving
    // the derived value exists proves the field is really being consumed.
    wrap('/me/installments/i1', <ClientInstallmentDetailPage />, '/me/installments/:id');
    await waitFor(() => expect(screen.queryByText('Demo Electronics')).toBeTruthy());
    // All six fixture payments are approved, so no pending banner is shown —
    // and, critically, no boundary either.
    expect(document.body.textContent).not.toContain(BOUNDARY);
  });
});

describe('M-13 tenant InstallmentDetailModal renders through the mock', () => {
  it('does not reach the route error boundary', async () => {
    wrap('/tenant/installments',
      <InstallmentDetailModal id="c1" onClose={() => {}} />, '/tenant/installments');
    await waitFor(() => expect(screen.queryByText(/MDD-2026-0001/)).toBeTruthy());
    expect(document.body.textContent).not.toContain(BOUNDARY);
  });

  it('renders the KEEP actions and data: totals, payment history, contract PDF', async () => {
    // c5 has paid_count 4, so the history section has rows to render.
    wrap('/tenant/installments',
      <InstallmentDetailModal id="c5" onClose={() => {}} />, '/tenant/installments');
    await waitFor(() => expect(screen.queryByText(/MDD-2026-0005/)).toBeTruthy());
    const text = document.body.textContent ?? '';
    expect(text).toContain('Nodira Yusupova');
    expect(text).toContain(i18n.t('tenantPayments.historyTitle'));
    expect(text).not.toContain(i18n.t('tenantPayments.noPayments'));
    // The Modal renders its footer in both a mobile and a desktop slot.
    expect(screen.getAllByRole('button', {
      name: new RegExp(i18n.t('installments.contractPdf'), 'i'),
    }).length).toBeGreaterThan(0);
  });
});
