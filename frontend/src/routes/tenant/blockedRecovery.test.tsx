// @vitest-environment jsdom
/**
 * P2-06 + C3-02 — the blocked gate, exercised against the real `TenantShell`.
 *
 * Two defects live here, and both are routing decisions rather than markup:
 *
 *  1. The shell replaced EVERY route with `BlockedGate` while a tenant was
 *     blocked — including `/tenant/billing`, which is exactly where the gate's
 *     own "top up" call to action points. The one stated recovery path was a
 *     dead link.
 *
 *  2. Blocked-ness was read from `me.tenants[0].status` rather than from the
 *     membership the user is actually acting on. For anyone belonging to more
 *     than one shop that is the wrong shop: a healthy selected tenant was gated
 *     because some other membership was blocked, and a selected blocked tenant
 *     was let through whenever it was not first in the list.
 *
 * These tests import the production `TenantShell` and drive it through a real
 * router; nothing here re-implements its branching.
 */
import { describe, expect, it, beforeEach, vi } from 'vitest';
import { render, screen, cleanup, within, waitFor } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import i18n from '@/i18n';
import type { MeResponse } from '@/types';

const HEALTHY = { id: 't-ok', slug: 'ok', name: 'Healthy Shop', role: 'owner', status: 'active', planCode: 'lite', trialEndsAt: null };
const BLOCKED = { id: 't-blk', slug: 'blk', name: 'Blocked Shop', role: 'owner', status: 'blocked', planCode: 'lite', trialEndsAt: null };

let ME: MeResponse;
let ACTIVE_TENANT_ID: string | null = null;
let ROLE = 'owner';

vi.mock('@/hooks/useMe', () => ({
  useMe: () => ({ data: ME, isLoading: false, isError: false, error: null }),
}));

vi.mock('@/store/session', () => ({
  useSession: (sel?: (s: unknown) => unknown) => {
    const state = {
      activeTenantId: ACTIVE_TENANT_ID,
      impersonating: false,
      impersonatingName: null,
      me: ME,
      activeRole: () => ROLE,
      exitTenant: vi.fn(),
    };
    return sel ? sel(state) : state;
  },
}));

vi.mock('@/lib/queries', async (orig) => ({
  ...(await orig<Record<string, unknown>>()),
  useTenantBilling: () => ({ data: { plan_code: 'lite', status: 'blocked', active_installments: 0 }, isLoading: false, isError: false, error: null, refetch: vi.fn() }),
  useTenantInvoices: () => ({ data: [], isLoading: false, isError: false, error: null, refetch: vi.fn() }),
  useTenantTopupInfo: () => ({ data: { balance: 0, cards: [], can_topup: ROLE === 'owner' }, isLoading: false, isError: false, error: null, refetch: vi.fn() }),
  // BillingPage now subscribes to the top-up history too; without this the real
  // hook would issue a live request from jsdom (the factory spreads the module).
  useTenantTopups: () => ({ data: [], isLoading: false, isError: false, error: null, refetch: vi.fn() }),
  usePublicPlans: () => ({ data: [], isLoading: false, isError: false, error: null, refetch: vi.fn() }),
}));

import { TenantShell } from '@/routes/router';
import { isActiveTenantBlocked, activeTenantFor, personaFor } from '@/store/persona';

/** Mount the REAL shell at a tenant path. */
function renderShell(at: string) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={[at]}>
        <Routes>
          <Route path="/:locale/tenant/*" element={<TenantShell />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

const gateShown = () => screen.queryByText(i18n.t('trial.blockedTitle')) !== null;

beforeEach(async () => {
  cleanup();
  ME = { tenants: [HEALTHY, BLOCKED] } as unknown as MeResponse;
  ACTIVE_TENANT_ID = null;
  ROLE = 'owner';
  await i18n.changeLanguage('uz');
  if (!Element.prototype.scrollIntoView) Element.prototype.scrollIntoView = () => {};
});

describe('the gate follows the ACTIVE tenant, not the first membership', () => {
  it('gates ordinary routes when the selected tenant is the blocked one', async () => {
    ME = { tenants: [HEALTHY, BLOCKED] } as unknown as MeResponse;   // healthy is first
    ACTIVE_TENANT_ID = BLOCKED.id;
    renderShell('/uz/tenant/customers');
    await waitFor(() => expect(gateShown()).toBe(true));
  });

  it('still reaches billing recovery for that blocked selected tenant', async () => {
    ME = { tenants: [HEALTHY, BLOCKED] } as unknown as MeResponse;
    ACTIVE_TENANT_ID = BLOCKED.id;
    renderShell('/uz/tenant/billing');
    await waitFor(() => expect(screen.getAllByText(i18n.t('billing.balance')).length).toBeGreaterThan(0));
    expect(gateShown()).toBe(false);
  });

  it('does NOT gate a healthy selected tenant when another membership is blocked', async () => {
    ME = { tenants: [BLOCKED, HEALTHY] } as unknown as MeResponse;   // blocked is first
    ACTIVE_TENANT_ID = HEALTHY.id;
    renderShell('/uz/tenant/customers');
    await waitFor(() => expect(screen.queryByText(i18n.t('trial.blockedTitle'))).toBeNull());
  });

  it('gates when the sole membership is blocked', async () => {
    ME = { tenants: [BLOCKED] } as unknown as MeResponse;
    ACTIVE_TENANT_ID = BLOCKED.id;
    renderShell('/uz/tenant/overview');
    await waitFor(() => expect(gateShown()).toBe(true));
  });
});

describe('the documented fallback when the active id is missing or stale', () => {
  // Same rule persona resolution uses: an absent or foreign id falls back to the
  // first membership, so a fresh login (no id yet) still resolves to a shop.
  it('falls back to the first membership when no active id is set', () => {
    ME = { tenants: [BLOCKED, HEALTHY] } as unknown as MeResponse;
    expect(activeTenantFor(ME, null)?.id).toBe(BLOCKED.id);
    expect(isActiveTenantBlocked(ME, null)).toBe(true);
  });

  it('falls back to the first membership when the active id is foreign', () => {
    ME = { tenants: [HEALTHY, BLOCKED] } as unknown as MeResponse;
    expect(activeTenantFor(ME, 'id-from-another-account')?.id).toBe(HEALTHY.id);
    expect(isActiveTenantBlocked(ME, 'id-from-another-account')).toBe(false);
  });

  it('uses the same resolution the persona does — they cannot disagree', () => {
    ME = { tenants: [{ ...BLOCKED, role: 'sales' }, HEALTHY] } as unknown as MeResponse;
    // Stale id → both fall back to membership[0], so persona and gate agree.
    expect(personaFor(ME, 'stale', false)).toBe('staff');
    expect(activeTenantFor(ME, 'stale')?.role).toBe('sales');
  });

  it('reports not-blocked for a user with no memberships at all', () => {
    expect(activeTenantFor({ tenants: [] } as unknown as MeResponse, null)).toBeNull();
    expect(isActiveTenantBlocked({ tenants: [] } as unknown as MeResponse, null)).toBe(false);
    expect(isActiveTenantBlocked(null, null)).toBe(false);
  });
});

describe('while blocked, only the recovery surface is reachable', () => {
  beforeEach(() => {
    ME = { tenants: [BLOCKED] } as unknown as MeResponse;
    ACTIVE_TENANT_ID = BLOCKED.id;
  });

  it.each(['overview', 'customers', 'installments', 'payments', 'staff', 'reports', 'settings'])(
    'shows the gate at /%s', async (path) => {
      renderShell(`/uz/tenant/${path}`);
      await waitFor(() => expect(gateShown()).toBe(true));
      expect(screen.queryByText(i18n.t('billing.balance'))).toBeNull();
    },
  );

  it('an owner still gets the top-up control on billing', async () => {
    ROLE = 'owner';
    renderShell('/uz/tenant/billing');
    await waitFor(() =>
      expect(screen.getAllByRole('button', { name: i18n.t('topup.title') }).length).toBeGreaterThan(0));
  });

  it('a manager still does not — the role rule is unchanged', async () => {
    ROLE = 'manager';
    renderShell('/uz/tenant/billing');
    // (#9) No button and no explanatory notice — the notice was removed; the
    // backend rule it described is unchanged.
    await waitFor(() => expect(screen.getByRole('heading', { name: i18n.t('billing.topupHistory') })).toBeTruthy());
    expect(screen.queryByRole('button', { name: i18n.t('topup.title') })).toBeNull();
    expect(screen.queryByText(i18n.t('billing.topupOwnerOnly'))).toBeNull();
  });
});

describe('the gate states the cause and a working way out', () => {
  beforeEach(() => {
    ME = { tenants: [BLOCKED] } as unknown as MeResponse;
    ACTIVE_TENANT_ID = BLOCKED.id;
  });

  it('names the cause and the clearing action', async () => {
    renderShell('/uz/tenant/overview');
    const gate = await screen.findByRole('alert');
    expect(gate.textContent).toContain(i18n.t('trial.blockedCause'));
    expect(gate.textContent).toContain(i18n.t('trial.blockedFix'));
  });

  it('its top-up CTA points at the route that actually renders', async () => {
    renderShell('/uz/tenant/overview');
    const cta = await screen.findByRole('link', { name: i18n.t('topup.title') });
    expect(cta.getAttribute('href')).toBe('/uz/tenant/billing');
  });

  it('styles that CTA as a link — never a button inside one', async () => {
    renderShell('/uz/tenant/overview');
    await screen.findByRole('alert');
    for (const link of screen.getAllByRole('link')) {
      expect(within(link).queryByRole('button'),
        `a link must not contain a button: ${link.outerHTML.slice(0, 80)}`).toBeNull();
    }
  });
});
