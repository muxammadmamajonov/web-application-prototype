// @vitest-environment jsdom
/**
 * FB-003 — a search box is not a selected customer.
 *
 * REPRODUCED ON A REAL DEVICE (founder beta, 2026-08-09). The contract wizard's
 * first step held a text query and a `customerId` that nothing kept in step with
 * it. Two ways that went wrong:
 *
 *   - the step's Enter handler advanced the wizard, so a shop could type a name,
 *     press Enter out of habit, and walk forward;
 *   - selecting "Ali", then searching "Vali", left `customerId` pointing at Ali
 *     while the screen showed Vali — and the contract was issued against Ali.
 *
 * A contract is a binding obligation against a named person, so the selection
 * has to be a concrete, tenant-scoped id the shop actually pointed at. These
 * tests pin the client half; `create_installment` re-checks the id against the
 * caller's own tenant and the customer's status, which is the half that holds
 * when the request does not come from this screen at all.
 */
import { describe, expect, it, beforeEach, afterEach, vi } from 'vitest';
import { cleanup, render, screen, fireEvent, waitFor, within } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router';
import i18n from '@/i18n';

const CUSTOMERS = [
  { id: 'c-ali', full_name: 'Ali Valiyev', phone: '+998901110001', status: 'active' },
  { id: 'c-vali', full_name: 'Vali Aliyev', phone: '+998901110002', status: 'active' },
  { id: 'c-hasan', full_name: 'Hasan Karimov', phone: '+998901110003', status: 'active' },
];

let QUERY: string | undefined;

vi.mock('@/lib/queries', async (importOriginal) => ({
  ...(await importOriginal<Record<string, unknown>>()),
  useTenantCustomers: (p?: { q?: string }) => {
    QUERY = p?.q;
    const q = (p?.q ?? '').toLowerCase();
    const items = q ? CUSTOMERS.filter((c) => c.full_name.toLowerCase().includes(q)) : CUSTOMERS;
    return { data: { items, total: items.length }, isLoading: false, isError: false };
  },
  useTenantGuarantors: () => ({ data: { items: [], total: 0 }, isLoading: false, isError: false }),
  useTenantSettings: () => ({ data: undefined, isLoading: false, isError: false }),
}));

const createInstallment = vi.fn(async () => ({ id: 'i1', contract_number: 'C-1' }));

vi.mock('@/lib/mutations', async (importOriginal) => ({
  ...(await importOriginal<Record<string, unknown>>()),
  useCreateInstallment: () => ({ mutateAsync: createInstallment, isPending: false }),
  useReplaceInstallment: () => ({ mutateAsync: vi.fn(), isPending: false }),
  useCreateGuarantor: () => ({ mutateAsync: vi.fn(), isPending: false }),
  useCreateCustomer: () => ({ mutateAsync: vi.fn(async () => ({ id: 'c-new' })), isPending: false }),
  useResendCustomerInvite: () => ({ mutateAsync: vi.fn(), isPending: false }),
}));

vi.mock('@/store/session', () => ({
  useSession: (sel?: (s: unknown) => unknown) => {
    const state = { activeTenantId: 't1', me: { id: 'u1' }, activeRole: () => 'owner' };
    return sel ? sel(state) : state;
  },
}));

const { InstallmentModal } = await import('@/routes/tenant/_modals/InstallmentModal');

function open() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter>
        <InstallmentModal open onClose={vi.fn()} />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

const search = () => screen.getByPlaceholderText(/qidir|поиск|ism/i) as HTMLInputElement;
const listbox = () => screen.getByRole('listbox');
const options = () => within(listbox()).getAllByRole('option');
const next = () => screen.getByRole('button', { name: /keyingisi/i }) as HTMLButtonElement;
const type = (v: string) => fireEvent.change(search(), { target: { value: v } });
const key = (k: string, init: Record<string, unknown> = {}) =>
  fireEvent.keyDown(search(), { key: k, ...init });

beforeEach(async () => { vi.clearAllMocks(); QUERY = undefined; await i18n.changeLanguage('uz'); });
afterEach(cleanup);

describe('search text is not a selection', () => {
  it('Next is disabled while nothing has been chosen', () => {
    open();
    expect(next().disabled).toBe(true);
  });

  it('typing a full, exactly-matching name still does not select anybody', () => {
    open();
    type('Ali Valiyev');
    expect(next().disabled).toBe(true);
    expect(options().every((o) => o.getAttribute('aria-selected') === 'false')).toBe(true);
  });

  it('clicking a row selects that row, and only then does Next unlock', () => {
    open();
    fireEvent.click(screen.getByText('Vali Aliyev'));
    expect(next().disabled).toBe(false);
    const selected = options().filter((o) => o.getAttribute('aria-selected') === 'true');
    expect(selected).toHaveLength(1);
    expect(selected[0].textContent).toContain('Vali Aliyev');
  });

  it('says why Next is disabled instead of leaving a dead button', () => {
    open();
    expect(screen.getByText(/qidiruv matni tanlov emas/i)).toBeTruthy();
  });
});

describe('Enter', () => {
  it('with an arbitrary query and no highlight, does nothing at all', () => {
    open();
    type('Ali');
    key('Enter');
    expect(next().disabled).toBe(true);
    expect(createInstallment).not.toHaveBeenCalled();
  });

  it('with a highlighted result, selects THAT result — and nothing else', () => {
    open();
    key('ArrowDown');                       // → Ali Valiyev
    key('ArrowDown');                       // → Vali Aliyev
    key('Enter');
    const selected = options().filter((o) => o.getAttribute('aria-selected') === 'true');
    expect(selected).toHaveLength(1);
    expect(selected[0].textContent).toContain('Vali Aliyev');
  });

  it('never advances the wizard, even once a customer IS selected', () => {
    open();
    key('ArrowDown');
    key('Enter');
    expect(next().disabled).toBe(false);
    key('Enter'); key('Enter');
    // Still on the customer step: its listbox is the proof.
    expect(screen.queryByRole('listbox')).not.toBeNull();
    expect(createInstallment).not.toHaveBeenCalled();
  });

  it('is inert while an IME is composing', () => {
    open();
    key('ArrowDown');
    key('Enter', { isComposing: true });
    key('Enter', { keyCode: 229 });
    expect(next().disabled).toBe(true);     // nothing was selected
  });
});

describe('the highlight', () => {
  it('wraps, so the keyboard cannot fall off either end', () => {
    open();
    key('ArrowUp');                         // from nothing → last
    key('Enter');
    expect(options()[CUSTOMERS.length - 1].getAttribute('aria-selected')).toBe('true');
  });

  it('is exposed to a screen reader as aria-activedescendant', () => {
    open();
    key('ArrowDown');
    expect(search().getAttribute('role')).toBe('combobox');
    expect(search().getAttribute('aria-activedescendant')).toBe(options()[0].id);
  });

  it('resets when a new query lands, so it cannot index the old results', () => {
    open();
    key('ArrowDown');
    type('Hasan');
    expect(search().getAttribute('aria-activedescendant')).toBeNull();
  });
});

describe('a stale selection cannot survive an edited query', () => {
  it('editing the query drops the customer the shop can no longer see', () => {
    open();
    fireEvent.click(screen.getByText('Ali Valiyev'));
    expect(next().disabled).toBe(false);
    type('Vali');
    expect(next().disabled).toBe(true);
    expect(options().every((o) => o.getAttribute('aria-selected') === 'false')).toBe(true);
  });

  it('and the query really does reach the server, scoped to this tenant', async () => {
    open();
    type('Hasan');
    // The picker searches SERVER-side (debounced), so the ids it can produce are
    // exactly the ones the server returned for this tenant — which is what makes
    // the eventual selection tenant-scoped rather than a client-side filter over
    // whatever happened to be cached.
    await waitFor(() => expect(QUERY).toBe('Hasan'));
    await waitFor(() => expect(screen.getAllByRole('option')).toHaveLength(1));
  });
});
