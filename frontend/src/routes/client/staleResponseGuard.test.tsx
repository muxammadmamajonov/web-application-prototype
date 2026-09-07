// @vitest-environment jsdom
/**
 * H15 — stale-response race on the client Pay/Payoff screens.
 *
 * Both `PayPage.tsx` and `PayoffPage.tsx` fetched `/me/installments/:id/...`
 * inside a plain `useEffect(() => { api.get(...).then(setState) }, [id])` with
 * no cancellation flag. Switching contracts fast (id A -> id B, e.g. tapping
 * "pay again" for a different shop right after opening this one) let A's
 * response land AFTER B's effect had already fired, overwriting B's state
 * with A's data — the customer looking at contract B would briefly see
 * contract A's payment method list / card details / payoff quote.
 *
 * The correct pattern already lived in the same file: PayPage's poll effect
 * uses `let cancelled = false`. It was just missing from the two effects
 * fixed here.
 *
 * Each test resolves the OLDER contract's request only after navigating to
 * the NEWER contract, and asserts the stale payload never reaches the
 * screen — red on the pre-fix code (the stale value renders), green after.
 */
import type { ComponentType } from 'react';
import { describe, expect, it, beforeEach, afterEach, vi } from 'vitest';
import { cleanup, render, screen, act } from '@testing-library/react';
import { MemoryRouter, Route, Routes, useNavigate } from 'react-router';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import i18n from '@/i18n';

const detailFor = (id: string) => ({
  id,
  tenant: { id: 't1', name: `Shop-${id}`, logo: null, slug: 'shop' },
  contract_number: `C-${id}`, reference: null, status: 'active' as const,
  currency: 'UZS',
  principal: '7000000', paid_amount: '0', remaining: '7000000',
  monthly_amount: '700000', total_amount: '7000000', down_payment: '0',
  months: 10, paid_count: 0,
  start_date: '2026-01-10', next_due_date: '2026-08-10',
  headline: null, items: [], schedule: [], payments: [],
});

vi.mock('@/lib/queries', async (importOriginal) => ({
  ...(await importOriginal<Record<string, unknown>>()),
  useMyInstallment: (id: string | undefined) => ({
    data: id ? detailFor(id) : undefined, isLoading: false, isError: false, error: null,
  }),
}));

/** One deferred promise per (endpoint, id) key, so the test controls resolution order. */
const resolvers = new Map<string, (v: unknown) => void>();
function deferred(key: string) {
  return new Promise((resolve) => resolvers.set(key, resolve));
}

vi.mock('@/lib/api', () => ({
  api: {
    get: vi.fn((url: string) => {
      const id = /installments\/([^/]+)\//.exec(url)?.[1] ?? 'unknown';
      if (url.includes('payment-methods')) return deferred(`methods-${id}`);
      if (url.includes('payoff-quote')) return deferred(`quote-${id}`);
      return Promise.resolve({ data: {} });
    }),
    post: vi.fn(async () => ({ data: {} })),
  },
}));

const resolveMethods = (id: string, label: string) => act(async () => {
  resolvers.get(`methods-${id}`)?.({
    data: { methods: [{ method: 'cash', label, online: false }], card_instructions: `instr-${id}` },
  });
});

const resolveQuote = (id: string, monthsLeft: number) => act(async () => {
  resolvers.get(`quote-${id}`)?.({
    data: {
      installment_id: id, remaining_principal: '1000000', discount_pct: '0', discount_amount: '0',
      payoff_amount: '1000000', scheduled_total: '1000000', savings_vs_schedule: '0',
      months_left: monthsLeft,
    },
  });
});

function renderAt(path: string, Component: ComponentType) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  let navigateRef: ((to: string) => void) | null = null;
  function Harness() {
    navigateRef = useNavigate();
    return <Routes><Route path="/me/installments/:id/*" element={<Component />} /></Routes>;
  }
  render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={[path]}><Harness /></MemoryRouter>
    </QueryClientProvider>,
  );
  // Same routed element persists across a param-only navigation, exactly as it
  // does in the app when a customer moves from one contract's pay screen to
  // another's — this is what makes the effect's cleanup (or lack of it) matter.
  return { navigate: (to: string) => act(() => navigateRef!(to)) };
}

beforeEach(async () => {
  vi.clearAllMocks();
  resolvers.clear();
  await i18n.changeLanguage('uz');
});
afterEach(cleanup);

describe('PayPage — a late payment-methods response for the OLD contract is ignored', () => {
  it('never renders once a newer contract is already on screen', async () => {
    const PayPage = (await import('@/routes/client/PayPage')).default;
    const { navigate } = renderAt('/me/installments/i1/pay', PayPage);

    navigate('/me/installments/i2/pay');
    // i1's response arrives late, after we've already moved on to i2.
    await resolveMethods('i1', 'Naqd-i1');
    expect(screen.queryByText('Naqd-i1')).toBeNull();

    // i2's own (later) response still renders correctly.
    await resolveMethods('i2', 'Naqd-i2');
    expect(await screen.findByText('Naqd-i2')).toBeTruthy();
  });
});

describe('PayoffPage — a late payoff-quote response for the OLD contract is ignored', () => {
  it('never renders once a newer contract is already on screen', async () => {
    const PayoffPage = (await import('@/routes/client/PayoffPage')).default;
    const { navigate } = renderAt('/me/installments/i1/payoff', PayoffPage);

    navigate('/me/installments/i2/payoff');
    // i1's quote arrives late, after we've already moved on to i2.
    await resolveQuote('i1', 1);
    expect(screen.queryByText(/1 oy qoldi/)).toBeNull();

    // i2's own (later) quote still renders correctly.
    await resolveQuote('i2', 2);
    expect(await screen.findByText(/2 oy qoldi/)).toBeTruthy();
  });
});
