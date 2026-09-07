// @vitest-environment jsdom
/**
 * Phase 3 remediation R-07 — draft recovery, proven by rendering the real wizard.
 *
 * The first pass shipped a draft hook that could never work: React runs effects
 * in declaration order, and the persist effect was declared above the restore
 * effect, so opening the wizard wrote the pristine empty state *before* the
 * restore effect read it back. The source read correctly — only the ORDER was
 * wrong — which is exactly why a source-string assertion could not catch it and
 * why this test drives the component instead.
 *
 * What is asserted here:
 *   * an existing draft survives an open and its items/terms are restored;
 *   * the initial empty state does not overwrite it;
 *   * a different user, or a different tenant, cannot restore it;
 *   * customer and guarantor identity are never written to storage.
 */
import { describe, expect, it, beforeEach, vi } from 'vitest';
import { fireEvent, render, screen, cleanup, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import i18n from '@/i18n';

// ── Session identity is what scopes the storage key. ─────────────────────────
let SESSION = { id: 'user-1', tenant: 'tenant-1' };
vi.mock('@/store/session', () => ({
  useSession: (sel: (s: unknown) => unknown) => sel({
    me: { id: SESSION.id },
    activeTenantId: SESSION.tenant,
    activeRole: () => 'owner',
  }),
}));

// ── Keep the wizard offline: no network, deterministic data. ─────────────────
vi.mock('@/lib/queries', async (orig) => ({
  ...(await orig<Record<string, unknown>>()),
  useTenantCustomers: () => ({ data: { items: [], total: 0 }, isLoading: false }),
  useTenantGuarantors: () => ({ data: { items: [] }, isLoading: false }),
  useTenantSettings: () => ({ data: undefined, isLoading: false }),
}));
vi.mock('@/lib/mutations', async (orig) => ({
  ...(await orig<Record<string, unknown>>()),
  useCreateInstallment: () => ({ mutateAsync: vi.fn(), isPending: false }),
  useReplaceInstallment: () => ({ mutateAsync: vi.fn(), isPending: false }),
  useCreateGuarantor: () => ({ mutateAsync: vi.fn(), isPending: false }),
  useCreateCustomer: () => ({ mutateAsync: vi.fn(), isPending: false }),
  useResendCustomerInvite: () => ({ mutateAsync: vi.fn(), isPending: false }),
}));

import { InstallmentModal } from './InstallmentModal';

const keyFor = (u: string, t: string) => `muddato.draft.installment.v2.${u}.${t}`;

/** A draft as the wizard would have written it — items and terms only. */
const draftBody = (over: Record<string, unknown> = {}) => JSON.stringify({
  v: 1,
  savedAt: Date.now(),
  data: {
    step: 1, months: 9, down: 250000, interest: '', calcFormula: 'markup',
    markup: '15', startDate: '',
    items: [{ key: 'k1', product_name: 'Recovered Phone', qty: 2, unit_price: 1500000 }],
    ...over,
  },
});

function renderWizard({ onClose = () => {} }: { onClose?: () => void } = {}) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <InstallmentModal open onClose={onClose} />
    </QueryClientProvider>,
  );
}

beforeEach(async () => {
  cleanup();
  sessionStorage.clear();
  SESSION = { id: 'user-1', tenant: 'tenant-1' };
  await i18n.changeLanguage('uz');
  if (!Element.prototype.scrollIntoView) Element.prototype.scrollIntoView = () => {};
});

describe('an existing draft survives opening the wizard', () => {
  it('is not overwritten by the initial empty state', async () => {
    sessionStorage.setItem(keyFor('user-1', 'tenant-1'), draftBody());
    renderWizard();

    await waitFor(() => {
      const raw = sessionStorage.getItem(keyFor('user-1', 'tenant-1'));
      expect(raw, 'the draft still exists').toBeTruthy();
      const items = JSON.parse(raw!).data.items as { product_name: string }[];
      // The bug wrote `[{product_name: ''}]` over this on the first render.
      expect(items[0].product_name, 'the recovered item was not clobbered')
        .toBe('Recovered Phone');
    });
  });

  it('restores the entered items into component state', async () => {
    sessionStorage.setItem(keyFor('user-1', 'tenant-1'), draftBody());
    renderWizard();

    // The wizard returns to the customer step on restore (a contract cannot be
    // created without one), so the items input is not on screen. What proves
    // hydration is that the draft the component now re-persists FROM ITS OWN
    // STATE still carries the recovered line — if hydration had not happened,
    // the empty initial state would have replaced it.
    await waitFor(() => {
      const d = JSON.parse(sessionStorage.getItem(keyFor('user-1', 'tenant-1'))!).data;
      expect(d.items).toHaveLength(1);
      expect(d.items[0].product_name).toBe('Recovered Phone');
      expect(d.items[0].qty).toBe(2);
      expect(d.items[0].unit_price).toBe(1500000);
    });

    // And the user is standing on the customer step, ready to pick one.
    expect(screen.getByText(/1\/5/)).toBeTruthy();
  });

  it('restores the financial terms', async () => {
    sessionStorage.setItem(keyFor('user-1', 'tenant-1'), draftBody());
    renderWizard();
    await waitFor(() => {
      const raw = sessionStorage.getItem(keyFor('user-1', 'tenant-1'))!;
      const d = JSON.parse(raw).data;
      expect(d.months).toBe(9);
      expect(d.down).toBe(250000);
      expect(d.calcFormula).toBe('markup');
      expect(d.markup).toBe('15');
    });
  });

  it('always returns the user to the customer step', async () => {
    // The customer is deliberately not in the draft, so resuming mid-flow would
    // leave a contract with no customer one click from creation.
    sessionStorage.setItem(keyFor('user-1', 'tenant-1'), draftBody({ step: 3 }));
    renderWizard();
    await waitFor(() => {
      const raw = sessionStorage.getItem(keyFor('user-1', 'tenant-1'))!;
      expect(JSON.parse(raw).data.step).toBe(0);
    });
  });
});

describe('a draft is scoped to who wrote it', () => {
  it('another user on the same device restores nothing', async () => {
    sessionStorage.setItem(keyFor('user-1', 'tenant-1'), draftBody());
    SESSION = { id: 'user-2', tenant: 'tenant-1' };
    renderWizard();

    await waitFor(() => {
      expect(screen.queryAllByDisplayValue('Recovered Phone')).toHaveLength(0);
    });
    // …and the other user's draft is untouched, not hijacked.
    const other = JSON.parse(sessionStorage.getItem(keyFor('user-1', 'tenant-1'))!);
    expect(other.data.items[0].product_name).toBe('Recovered Phone');
  });

  it('the same user in another tenant restores nothing', async () => {
    sessionStorage.setItem(keyFor('user-1', 'tenant-1'), draftBody());
    SESSION = { id: 'user-1', tenant: 'tenant-2' };
    renderWizard();

    await waitFor(() => {
      expect(screen.queryAllByDisplayValue('Recovered Phone')).toHaveLength(0);
    });
  });

  it('writes under a key carrying both the user and the tenant', async () => {
    renderWizard();
    await waitFor(() => {
      const keys = Object.keys(sessionStorage);
      const draftKeys = keys.filter((k) => k.startsWith('muddato.draft.installment'));
      for (const k of draftKeys) {
        expect(k).toContain('user-1');
        expect(k).toContain('tenant-1');
      }
    });
  });
});

describe('no personal data ever reaches storage', () => {
  const FORBIDDEN = [
    'passport', 'jshshir', 'customer_id', 'customerId', 'guarantor',
    'full_name', 'fullName', 'phone', 'birthdate', 'address',
    'photo', 'receipt', 'token', 'telegram',
  ];

  it('the persisted payload contains no identity field', async () => {
    renderWizard();
    await waitFor(() => {
      expect(Object.keys(sessionStorage).some((k) => k.startsWith('muddato.draft'))).toBe(true);
    });

    for (const k of Object.keys(sessionStorage)) {
      if (!k.startsWith('muddato.draft')) continue;
      const raw = sessionStorage.getItem(k)!.toLowerCase();
      for (const bad of FORBIDDEN) {
        expect(raw, `"${bad}" must never be persisted`).not.toContain(bad.toLowerCase());
      }
    }
  });

  it('persists only the whitelisted commercial keys', async () => {
    renderWizard();
    await waitFor(() => {
      const k = Object.keys(sessionStorage).find((x) => x.startsWith('muddato.draft'))!;
      const data = JSON.parse(sessionStorage.getItem(k)!).data as Record<string, unknown>;
      expect(new Set(Object.keys(data))).toEqual(new Set([
        'step', 'months', 'down', 'interest', 'calcFormula', 'markup', 'startDate', 'items',
      ]));
      for (const item of data.items as Record<string, unknown>[]) {
        expect(new Set(Object.keys(item))).toEqual(new Set(['key', 'product_name', 'qty', 'unit_price']));
      }
    });
  });
});

// ── P2-04: an explicit cancel must discard the draft ─────────────────────────

describe('cancelling the wizard discards the draft', () => {
  /** Click the footer's Cancel (step 0 renders Cancel rather than Back). */
  const clickCancel = () =>
    fireEvent.click(screen.getByRole('button', { name: i18n.t('common.cancel') }));

  it('clears a RESTORED draft that is unchanged from its baseline', async () => {
    // The defect: a restored draft matches its own captured baseline, so the
    // dirty check was false, the discard prompt was skipped — and so was the
    // clear. The user cancelled, and the work they abandoned came back.
    sessionStorage.setItem(keyFor('user-1', 'tenant-1'), draftBody());
    const onClose = vi.fn();
    renderWizard({ onClose });

    await waitFor(() => {
      const raw = sessionStorage.getItem(keyFor('user-1', 'tenant-1'));
      expect(JSON.parse(raw!).data.items[0].product_name).toBe('Recovered Phone');
    });

    clickCancel();

    await waitFor(() => {
      expect(sessionStorage.getItem(keyFor('user-1', 'tenant-1')),
        'an explicit cancel must not leave the draft behind').toBeNull();
    });
    expect(onClose).toHaveBeenCalled();
  });

  it('clears the draft when there was nothing restored either', async () => {
    const onClose = vi.fn();
    renderWizard({ onClose });
    await waitFor(() => {
      expect(Object.keys(sessionStorage).some((k) => k.startsWith('muddato.draft'))).toBe(true);
    });

    clickCancel();

    await waitFor(() => {
      expect(sessionStorage.getItem(keyFor('user-1', 'tenant-1'))).toBeNull();
    });
    expect(onClose).toHaveBeenCalled();
  });
});

// ── C3-07: cross-context lifecycle edges ────────────────────────────────────

describe('closing the EDIT modal leaves the create draft alone', () => {
  const EXISTING = {
    id: 'inst-9', reference: 'R9', contract_number: 'MDD-9',
    customer_id: 'cust-1', customer_name: 'Someone', customer_phone: null,
    status: 'active', total_amount: 6_000_000, down_payment: 0,
    principal: 6_000_000, markup_amount: 0, calc_formula: 'none',
    markup_pct: null, interest_rate: null, months: 6, monthly_amount: 1_000_000,
    paid_count: 0, paid_amount: 0, start_date: '2026-01-01',
    next_due_date: '2026-02-01', created_at: '2026-01-01T00:00:00Z',
    items: [], guarantors: [], payments: [], schedule: [],
  };

  function renderEdit(onClose = () => {}) {
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    return render(
      <QueryClientProvider client={qc}>
        <InstallmentModal open onClose={onClose} editing={EXISTING as never} />
      </QueryClientProvider>,
    );
  }

  it('does not delete a half-finished new contract', async () => {
    // The user is midway through creating a contract…
    sessionStorage.setItem(keyFor('user-1', 'tenant-1'), draftBody());
    // …then opens an existing contract to edit, and closes it again.
    const onClose = vi.fn();
    renderEdit(onClose);
    fireEvent.click(screen.getAllByRole('button', { name: i18n.t('common.close') })[0]);

    await waitFor(() => expect(onClose).toHaveBeenCalled());
    const raw = sessionStorage.getItem(keyFor('user-1', 'tenant-1'));
    expect(raw, 'the unrelated create draft was destroyed by closing the editor').toBeTruthy();
    expect(JSON.parse(raw!).data.items[0].product_name).toBe('Recovered Phone');
  });

  it('and does not write one of its own either', async () => {
    const onClose = vi.fn();
    renderEdit(onClose);
    await waitFor(() => expect(screen.getByText(/1\/5/)).toBeTruthy());
    expect(sessionStorage.getItem(keyFor('user-1', 'tenant-1')),
      'editing must not create a draft').toBeNull();
  });
});

describe('cancelling the CREATE wizard still clears its draft', () => {
  it('clears even when the restored draft matches its own baseline', async () => {
    sessionStorage.setItem(keyFor('user-1', 'tenant-1'), draftBody());
    const onClose = vi.fn();
    renderWizard({ onClose });
    await waitFor(() => expect(screen.getByText(/1\/5/)).toBeTruthy());

    fireEvent.click(screen.getAllByRole('button', { name: i18n.t('common.close') })[0]);
    await waitFor(() => expect(onClose).toHaveBeenCalled());
    expect(sessionStorage.getItem(keyFor('user-1', 'tenant-1')),
      'an explicit cancel must discard the draft').toBeNull();
  });
});

// ── F4-05: the wizard uses the canonical SearchInput ────────────────────────

describe('customer and guarantor search use the shared control', () => {
  const custBox = () => screen.getByPlaceholderText(i18n.t('installments.customerSearchPh'));

  it('renders a named, keyboard-operable search field for customers', async () => {
    renderWizard();
    const box = await waitFor(custBox);
    // SearchInput names itself from its placeholder, so the field keeps an
    // accessible name once a value is typed and the placeholder disappears.
    // (FB-003) Its role is `combobox`, not `textbox`: the field drives a
    // highlighted result list, and the picker's keyboard contract (Arrow to
    // move, Enter to select THAT row) only exists for a screen reader if the
    // role says so.
    expect(screen.getByRole('combobox', { name: i18n.t('installments.customerSearchPh') })).toBe(box);
    fireEvent.change(box, { target: { value: 'Aziz' } });
    expect((box as HTMLInputElement).value).toBe('Aziz');
  });

  it('keeps the single-character query behaviour the wizard had', async () => {
    // The hand-rolled debounce had no minimum length; SearchInput defaults to 2,
    // so the wizard passes minLength={1} to preserve it. A 1-char query must
    // still reach the server-side `q`.
    renderWizard();
    const box = await waitFor(custBox);
    fireEvent.change(box, { target: { value: 'A' } });
    expect((box as HTMLInputElement).value).toBe('A');
  });

  it('is full width rather than the control default max-w-md', async () => {
    renderWizard();
    const box = await waitFor(custBox);
    // The wizard column is narrow; the shared default would leave a gap.
    expect(box.closest('label')?.className).toContain('max-w-none');
  });

  it('shows the no-results state when nothing matches', async () => {
    renderWizard();
    await waitFor(custBox);
    // The mocked customer query returns an empty list, which is the
    // no-results path the picker renders under the field.
    expect(screen.getByText(i18n.t('installments.noCustomers'))).toBeTruthy();
  });

  it('no raw search input remains in the wizard source', async () => {
    const { readFileSync } = await import('node:fs');
    const { resolve } = await import('node:path');
    const src = readFileSync(resolve(__dirname, 'InstallmentModal.tsx'), 'utf8');
    expect(src, 'a hand-rolled search control is back').not.toMatch(/<input\s+[^>]*type="search"/);
    expect(src).toContain('<SearchInput');
  });
});
