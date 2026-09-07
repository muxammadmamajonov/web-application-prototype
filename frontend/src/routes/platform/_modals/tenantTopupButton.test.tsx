// @vitest-environment jsdom
/**
 * MUDD-146 — the platform tenant top-up button spun forever on an empty field.
 *
 * The call site passed `loading={topup.isPending || !topupAmt}`, so "no amount
 * entered yet" — a validation state — rendered as "a write is in flight". On top
 * of that, `children` hand-rolled a second `<Loader2/>` next to the one `Button`
 * already draws for `loading`, so a real top-up showed two spinners.
 *
 * These tests pin the two axes apart: a spinner means a pending mutation and
 * nothing else, and there is never more than one of it.
 */
import { describe, expect, it, beforeEach, vi } from 'vitest';
import { fireEvent, render, screen, cleanup, waitFor } from '@testing-library/react';
import i18n from '@/i18n';

/* Stable spy identities. A `vi.mock` factory runs once, so anything it reads
   eagerly (`confirmDialog: h.confirmDialog`) captures the value it had then —
   reassigning the field later would leave the component calling the old spy.
   These are configured per test with `mockReset`, never rebound. */
const h = vi.hoisted(() => ({
  pending: false,
  mutateAsync: vi.fn(),
  confirmDialog: vi.fn(),
  toastSuccess: vi.fn(),
  toastError: vi.fn(),
}));

vi.mock('@/store/session', () => ({
  useSession: (sel: (s: unknown) => unknown) =>
    sel({ canManagePlatform: () => true, enterTenant: vi.fn(), activeRole: () => 'owner' }),
}));
vi.mock('react-router', () => ({ useNavigate: () => vi.fn() }));
vi.mock('@/lib/queries', async (orig) => ({
  ...(await orig<Record<string, unknown>>()),
  useTenantDetail: () => ({
    data: { status: 'active', plan_code: 'lite', balance: 500, stats: {}, created_at: '2026-01-01T00:00:00Z' },
    isLoading: false,
  }),
  usePlatformPlans: () => ({ data: [{ code: 'lite' }, { code: 'pro' }] }),
}));
vi.mock('@/lib/mutations', async (orig) => ({
  ...(await orig<Record<string, unknown>>()),
  useSetTenantStatus: () => ({ mutateAsync: vi.fn(), isPending: false }),
  useDeleteTenant: () => ({ mutateAsync: vi.fn(), isPending: false }),
  useTenantOwnerInvite: () => ({ mutateAsync: vi.fn(), isPending: false }),
  useTopupBalance: () => ({
    mutateAsync: (...a: unknown[]) => h.mutateAsync(...a),
    isPending: h.pending,
  }),
  useSetTenantPlan: () => ({ mutateAsync: vi.fn(), isPending: false }),
}));
vi.mock('@/lib/feedback', async (orig) => ({
  ...(await orig<Record<string, unknown>>()),
  confirmDialog: (...a: unknown[]) => h.confirmDialog(...a),
  toast: {
    success: (...a: unknown[]) => h.toastSuccess(...a),
    error: (...a: unknown[]) => h.toastError(...a),
    warning: vi.fn(), info: vi.fn(),
  },
}));

import { TenantDetailDrawer } from './TenantDetailDrawer';

const TENANT = { id: 't1', name: 'Shop', slug: 'shop', status: 'active', plan_code: 'lite' } as never;

function open() {
  render(<TenantDetailDrawer tenant={TENANT} onClose={() => {}} />);
}

/** The top-up submit button — named by the same key the component renders. */
const topupBtn = () => screen.getByRole('button', { name: i18n.t('platformTenants.topup') });
const amountField = () => screen.getByPlaceholderText(i18n.t('platformTenants.topupPh'));

/** Every spinner inside the top-up button. The design system marks them all
 *  with `animate-spin`, so this counts the component's and any hand-rolled one. */
const spinners = () => topupBtn().querySelectorAll('.animate-spin');
/** `@testing-library/jest-dom` is not wired into this suite — read the DOM. */
const isDisabled = () => (topupBtn() as HTMLButtonElement).disabled;

/** Type an amount through MoneyInput (which strips non-digits and emits a number). */
function typeAmount(text: string) {
  fireEvent.change(amountField(), { target: { value: text } });
}

beforeEach(async () => {
  cleanup();
  h.pending = false;
  h.mutateAsync.mockReset().mockResolvedValue({});
  h.confirmDialog.mockReset().mockResolvedValue(true);
  h.toastSuccess.mockReset();
  h.toastError.mockReset();
  await i18n.changeLanguage('uz');
  if (!Element.prototype.scrollIntoView) Element.prototype.scrollIntoView = () => {};
});

describe('an amount that is not yet valid disables the button without a spinner', () => {
  it('shows no spinner on the untouched, empty field', () => {
    open();
    expect(spinners(), 'an empty field is not a pending write').toHaveLength(0);
    expect(isDisabled()).toBe(true);
    expect(topupBtn().getAttribute('aria-busy'), 'aria-busy lies to a screen reader here').toBeNull();
  });

  it('shows no spinner for an explicit 0', () => {
    open();
    typeAmount('0');
    expect(spinners()).toHaveLength(0);
    expect(isDisabled()).toBe(true);
    expect(topupBtn().getAttribute('aria-busy')).toBeNull();
  });

  it('shows no spinner once the field is cleared again', () => {
    open();
    typeAmount('50000');
    expect(isDisabled()).toBe(false);
    typeAmount('');
    expect(spinners(), 'clearing the field must not start a spinner').toHaveLength(0);
    expect(isDisabled()).toBe(true);
  });

  it('enables the button with no spinner for a valid amount', () => {
    open();
    typeAmount('50000');
    expect(isDisabled()).toBe(false);
    expect(spinners(), 'an entered amount is not a pending write').toHaveLength(0);
    expect(topupBtn().getAttribute('aria-busy')).toBeNull();
  });
});

describe('a real pending mutation shows exactly one spinner', () => {
  it('renders a single spinner and marks the button busy', () => {
    h.pending = true;
    open();
    expect(spinners(), 'the component draws the spinner — children must not add a second')
      .toHaveLength(1);
    expect(isDisabled()).toBe(true);
    expect(topupBtn().getAttribute('aria-busy')).toBe('true');
  });
});

describe('the top-up write itself', () => {
  it('does not credit the tenant when the confirm is cancelled', async () => {
    h.confirmDialog.mockResolvedValue(false);
    open();
    typeAmount('50000');
    fireEvent.click(topupBtn());
    await waitFor(() => expect(h.confirmDialog).toHaveBeenCalled());
    expect(h.mutateAsync, 'a cancelled confirm must not move money').not.toHaveBeenCalled();
    expect(spinners()).toHaveLength(0);
  });

  it('credits the tenant, resets the field and leaves no spinner on success', async () => {
    open();
    typeAmount('50000');
    fireEvent.click(topupBtn());
    await waitFor(() => expect(h.mutateAsync).toHaveBeenCalledWith({ id: 't1', amount: 50000 }));
    await waitFor(() => expect(h.toastSuccess).toHaveBeenCalled());
    // The reset returns the button to the disabled-but-idle state, not a spinner.
    await waitFor(() => expect(isDisabled()).toBe(true));
    expect(spinners()).toHaveLength(0);
    expect((amountField() as HTMLInputElement).value).toBe('');
  });

  it('surfaces an API failure as an error toast and stops spinning', async () => {
    h.mutateAsync.mockRejectedValue(new Error('boom'));
    open();
    typeAmount('50000');
    fireEvent.click(topupBtn());
    await waitFor(() => expect(h.toastError).toHaveBeenCalled());
    expect(h.toastSuccess).not.toHaveBeenCalled();
    // The amount survives a failure so the operator can retry.
    expect((amountField() as HTMLInputElement).value).not.toBe('');
    expect(isDisabled()).toBe(false);
    expect(spinners(), 'a failed write must not leave the button spinning').toHaveLength(0);
  });
});
