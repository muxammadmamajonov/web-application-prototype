// @vitest-environment jsdom
/**
 * P2-01 — an operator must not be able to push a tenant into the demo lifecycle.
 *
 * `demo` is not a label: `expire_demos` selects on it and suspends or blocks
 * those tenants when their trial elapses, and `plan_limits` swaps a demo
 * tenant's caps for the `demo_plan` config. Phase 2 defers trials, so the MVP
 * offers no way in. A tenant already in demo keeps its own status visible and
 * can move out.
 */
import { describe, expect, it, beforeEach, vi } from 'vitest';
import { fireEvent, render, screen, cleanup, within } from '@testing-library/react';
import i18n from '@/i18n';

vi.mock('@/store/session', () => ({
  useSession: (sel: (s: unknown) => unknown) =>
    sel({ canManagePlatform: () => true, enterTenant: vi.fn(), activeRole: () => 'owner' }),
}));
vi.mock('react-router', () => ({ useNavigate: () => vi.fn() }));

let DETAIL: Record<string, unknown> | undefined;
vi.mock('@/lib/queries', async (orig) => ({
  ...(await orig<Record<string, unknown>>()),
  useTenantDetail: () => ({ data: DETAIL, isLoading: false }),
  usePlatformPlans: () => ({ data: [{ code: 'lite' }, { code: 'pro' }] }),
}));
vi.mock('@/lib/mutations', async (orig) => ({
  ...(await orig<Record<string, unknown>>()),
  useSetTenantStatus: () => ({ mutateAsync: vi.fn(), isPending: false }),
  useDeleteTenant: () => ({ mutateAsync: vi.fn(), isPending: false }),
  useTenantOwnerInvite: () => ({ mutateAsync: vi.fn(), isPending: false }),
  useTopupBalance: () => ({ mutateAsync: vi.fn(), isPending: false }),
  useSetTenantPlan: () => ({ mutateAsync: vi.fn(), isPending: false }),
}));

import { TenantDetailDrawer } from './TenantDetailDrawer';

const tenantRow = (status: string) => ({
  id: 't1', name: 'Shop', slug: 'shop', status, plan_code: 'lite',
} as never);

/** Open the status Select and read the options it actually offers. */
function statusOptions(status: string): string[] {
  DETAIL = { status, plan_code: 'lite', balance: 0, stats: {}, created_at: new Date().toISOString() };
  render(<TenantDetailDrawer tenant={tenantRow(status)} onClose={() => {}} />);

  const combos = screen.getAllByRole('combobox');
  // The status control is the one whose current value is the tenant's status.
  const statusCombo = combos.find((c) => c.textContent?.includes(i18n.t(`status.${status}`)))
    ?? combos[combos.length - 1];
  fireEvent.click(statusCombo);
  const listbox = screen.getByRole('listbox');
  return within(listbox).getAllByRole('option').map((o) => o.textContent?.trim() ?? '');
}

beforeEach(async () => {
  cleanup();
  DETAIL = undefined;
  await i18n.changeLanguage('uz');
  if (!Element.prototype.scrollIntoView) Element.prototype.scrollIntoView = () => {};
});

describe('a production tenant cannot be moved into demo', () => {
  it.each(['active', 'suspended', 'blocked'])('offers no demo option for a %s tenant', (status) => {
    const options = statusOptions(status);
    expect(options.length).toBeGreaterThan(0);
    expect(options, 'demo must not be offered').not.toContain(i18n.t('status.demo'));
  });

  it('still offers the real production statuses', () => {
    const options = statusOptions('active');
    for (const s of ['active', 'suspended', 'blocked']) {
      expect(options).toContain(i18n.t(`status.${s}`));
    }
  });
});

describe('a legacy demo tenant stays readable and can move out', () => {
  it('shows demo as its own current status', () => {
    const options = statusOptions('demo');
    expect(options, 'the row must not render a status it cannot display')
      .toContain(i18n.t('status.demo'));
  });

  it('also offers the statuses it can move to', () => {
    const options = statusOptions('demo');
    for (const s of ['active', 'suspended', 'blocked']) {
      expect(options).toContain(i18n.t(`status.${s}`));
    }
  });
});
