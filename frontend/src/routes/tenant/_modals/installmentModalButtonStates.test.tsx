// @vitest-environment jsdom
/**
 * MUDD-147 — `Button`'s `loading` prop drives THREE things at once: disabled,
 * `aria-busy`, and its own built-in spinner (see components/ui/Button.tsx).
 * Two call sites in this modal fed a plain validation flag into `loading`
 * instead of `disabled`, so a step that was merely incomplete announced
 * itself to screen readers as "busy" forever, with no mutation in flight.
 * A third fed a real pending flag into `loading` *and* rendered a second,
 * manual spinner in the children — a duplicate, not a fix.
 *
 * These pin the three call sites the review named:
 *   - the footer "Next" button (`loading={!stepValid || busy}` → line ~875)
 *   - the "invite to Telegram" button on the post-create screen
 *     (`loading={inviteCustomer.isPending || !customerId}` → line ~834)
 */
import { describe, expect, it, beforeEach, afterEach, vi } from 'vitest';
import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import '@testing-library/jest-dom/vitest';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router';
import i18n from '@/i18n';

const CUSTOMERS = [{ id: 'c-ali', full_name: 'Ali Valiyev', phone: '+998901110001', status: 'active' }];

vi.mock('@/lib/queries', async (orig) => ({
  ...(await orig<Record<string, unknown>>()),
  useTenantCustomers: () => ({ data: { items: CUSTOMERS, total: 1 }, isLoading: false, isError: false }),
  useTenantGuarantors: () => ({ data: { items: [], total: 0 }, isLoading: false, isError: false }),
  useTenantSettings: () => ({ data: undefined, isLoading: false, isError: false }),
  checkImei: vi.fn(async () => ({ serial: '', in_tenant: false, in_tenant_count: 0, cross_tenant_count: 0 })),
}));

const createInstallment = vi.fn(async () => ({ id: 'i1', contract_number: 'C-1' }));
let inviteIsPending = false;
const inviteMutateAsync = vi.fn(async () => ({ url: 'https://t.me/joinlink', already_linked: false }));

vi.mock('@/lib/mutations', async (orig) => ({
  ...(await orig<Record<string, unknown>>()),
  useCreateInstallment: () => ({ mutateAsync: createInstallment, isPending: false }),
  useReplaceInstallment: () => ({ mutateAsync: vi.fn(), isPending: false }),
  useCreateGuarantor: () => ({ mutateAsync: vi.fn(), isPending: false }),
  useCreateCustomer: () => ({ mutateAsync: vi.fn(async () => ({ id: 'c-new' })), isPending: false }),
  useResendCustomerInvite: () => ({ mutateAsync: inviteMutateAsync, isPending: inviteIsPending }),
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
      <MemoryRouter><InstallmentModal open onClose={vi.fn()} /></MemoryRouter>
    </QueryClientProvider>,
  );
}

const next = () => screen.getByRole('button', { name: /keyingisi|shartnoma yaratish/i }) as HTMLButtonElement;
const setV = (el: HTMLElement, v: string) => fireEvent.change(el, { target: { value: v } });
const editors = () => screen.queryAllByTestId('item-editor');

function priceInput(): HTMLInputElement {
  const numeric = within(editors()[0]).getAllByRole('textbox')
    .filter((i) => (i as HTMLInputElement).inputMode === 'numeric') as HTMLInputElement[];
  return numeric[numeric.length - 1];
}

/** Drives the wizard from a blank step 0 all the way to the post-create
 *  "invite to Telegram" screen: select the customer, fill one item, then
 *  click through the guarantors/terms/summary steps (all valid by default). */
async function driveToCreated() {
  fireEvent.click(screen.getByText('Ali Valiyev'));
  fireEvent.click(next());
  setV(within(editors()[0]).getByPlaceholderText(i18n.t('installments.itemNamePh')), 'Muzlatgich');
  setV(priceInput(), '6000000');
  fireEvent.click(next()); // items → guarantors
  fireEvent.click(next()); // guarantors → terms
  fireEvent.click(next()); // terms → summary
  await act(async () => { fireEvent.click(next()); }); // summary → submit
  await waitFor(() => expect(screen.getByRole('heading', { name: /shartnoma yaratildi/i })).toBeTruthy());
}

beforeEach(async () => {
  vi.clearAllMocks();
  inviteIsPending = false;
  await i18n.changeLanguage('uz');
  globalThis.sessionStorage?.clear();
  globalThis.localStorage?.clear();
  Element.prototype.scrollIntoView = () => {};
});
afterEach(cleanup);

describe('footer Next button — validation is disabled, not busy (line ~875)', () => {
  it('is disabled on an incomplete step but never announces aria-busy', () => {
    open();
    // Step 0: no customer picked yet — stepValid is false, no mutation is running.
    expect(next().disabled).toBe(true);
    expect(next()).not.toHaveAttribute('aria-busy', 'true');
  });

  it('becomes enabled and stays non-busy once the step is valid', () => {
    open();
    fireEvent.click(screen.getByText('Ali Valiyev'));
    expect(next().disabled).toBe(false);
    expect(next()).not.toHaveAttribute('aria-busy', 'true');
  });
});

describe('post-create "invite to Telegram" button (line ~834)', () => {
  const inviteBtn = () => screen.getByRole('button', { name: /telegramga taklif/i }) as HTMLButtonElement;

  it('is not busy while idle, once a customer is on the contract', async () => {
    open();
    await driveToCreated();
    // A contract cannot reach this screen without a selected customer, so the
    // button is never disabled here — and, with no mutation running, it must
    // not read as busy to a screen reader either.
    expect(inviteBtn().disabled).toBe(false);
    expect(inviteBtn()).not.toHaveAttribute('aria-busy', 'true');
  });

  it('shows exactly one spinner while the invite is pending — not two', async () => {
    // The mocked hook reads this module-level flag on every render, so it is
    // set before the mutation ever "starts" — mirroring the moment the real
    // `useResendCustomerInvite()` would report `isPending: true`.
    inviteIsPending = true;
    open();
    await driveToCreated();
    expect(inviteBtn()).toHaveAttribute('aria-busy', 'true');
    // Button's own `loading` spinner is the only one; a leftover manual
    // `{inviteCustomer.isPending ? <Loader2/> : ...}` in the children would
    // render a second `.animate-spin` element inside the same button.
    expect(inviteBtn().querySelectorAll('.animate-spin').length).toBe(1);
  });
});
