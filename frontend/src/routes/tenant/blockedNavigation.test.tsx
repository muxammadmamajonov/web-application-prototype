// @vitest-environment jsdom
/**
 * QA-015 — while a tenant is blocked, navigation must not offer destinations
 * that only re-render the gate.
 *
 * `TenantShell` already routes correctly: `billing` stays live and everything
 * else falls to `BlockedGate`. The navigation did not know that, so the sidebar
 * kept listing all ten destinations and nine of them led straight back to the
 * gate — and the bottom bar was worse, because none of its four quick tabs is
 * billing, so the only tab a blocked owner could usefully press did not exist.
 *
 * The load-bearing assertion is not "the sidebar has one item". It is that
 * EVERY link any navigation surface offers resolves to a route that does not
 * render the gate — measured by mounting the real shell at each offered href.
 * That statement stays true if the routing changes, and it is exactly what the
 * audit found to be false.
 */
import { describe, expect, it, beforeEach, vi } from 'vitest';
import { render, screen, cleanup, waitFor, act } from '@testing-library/react';
import { MemoryRouter, Route, Routes, useNavigate } from 'react-router';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import i18n from '@/i18n';
import type { MeResponse } from '@/types';

const BLOCKED = { id: 't-blk', slug: 'blk', name: 'Blocked Shop', role: 'owner', status: 'blocked', planCode: 'lite', trialEndsAt: null };
const HEALTHY = { id: 't-ok', slug: 'ok', name: 'Healthy Shop', role: 'owner', status: 'active', planCode: 'lite', trialEndsAt: null };

let ME: MeResponse;
let ACTIVE_TENANT_ID: string | null = null;
let ROLE = 'owner';

vi.mock('@/hooks/useMe', () => ({
  useMe: () => ({ data: ME, isLoading: false, isError: false, error: null }),
}));
vi.mock('@/store/session', () => ({
  useSession: (sel?: (s: unknown) => unknown) => {
    const state = {
      activeTenantId: ACTIVE_TENANT_ID, impersonating: false, impersonatingName: null,
      me: ME, activeRole: () => ROLE, exitTenant: vi.fn(),
    };
    return sel ? sel(state) : state;
  },
}));
vi.mock('@/lib/queries', async (orig) => ({
  ...(await orig<Record<string, unknown>>()),
  useTenantBilling: () => ({ data: { plan_code: 'lite', status: 'blocked', active_installments: 0 }, isLoading: false, isError: false, error: null, refetch: vi.fn() }),
  useTenantInvoices: () => ({ data: [], isLoading: false, isError: false, error: null, refetch: vi.fn() }),
  useTenantTopupInfo: () => ({ data: { balance: 0, cards: [] }, isLoading: false, isError: false, error: null, refetch: vi.fn() }),
  useTenantTopups: () => ({ data: [], isLoading: false, isError: false, error: null, refetch: vi.fn() }),
  usePublicPlans: () => ({ data: [], isLoading: false, isError: false, error: null, refetch: vi.fn() }),
}));

import { TenantShell } from '@/routes/router';
import { sidebarFor, bottomNavFor, menuItemsFor } from '@/routes/_navConfig';

const LOCALES = ['uz', 'ru'] as const;
const ROLES = ['owner', 'manager'] as const;

/** Handle on the mounted router, so a test can navigate for real. */
let navigateTo: ((to: string) => void) | null = null;
function NavProbe() { navigateTo = useNavigate(); return null; }

function renderShell(at: string) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={[at]}>
        <NavProbe />
        <Routes><Route path="/:locale/tenant/*" element={<TenantShell />} /></Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

const gateShown = () => screen.queryByText(i18n.t('trial.blockedTitle')) !== null;

/** Every destination any navigation surface currently offers, deduplicated. */
const offeredHrefs = (): string[] => {
  const hrefs = Array.from(document.querySelectorAll<HTMLAnchorElement>('nav a[href]'))
    .map((a) => a.getAttribute('href') ?? '')
    .filter((h) => h.includes('/tenant/'));
  return Array.from(new Set(hrefs));
};

beforeEach(async () => {
  cleanup();
  ME = { tenants: [BLOCKED] } as unknown as MeResponse;
  ACTIVE_TENANT_ID = BLOCKED.id;
  ROLE = 'owner';
  document.title = '';
  await i18n.changeLanguage('uz');
  if (!Element.prototype.scrollIntoView) Element.prototype.scrollIntoView = () => {};
});

// ── the selector is one source of truth ─────────────────────────────────────

describe('the nav selector knows about the block', () => {
  const t = (k: string) => k;

  it('offers only the recovery destination on every surface', () => {
    const opts = { blocked: true };
    expect(sidebarFor('tenant', 'uz', t, opts).map((i) => i.key)).toEqual(['billing']);
    expect(bottomNavFor('tenant', 'uz', t, opts).map((i) => i.key)).toEqual(['billing']);
    // Everything the sidebar offers is already a quick tab, so the hub is empty
    // — and the hub route itself renders the gate.
    expect(menuItemsFor('tenant', 'uz', t, opts)).toEqual([]);
  });

  it('leaves ordinary tenant navigation completely alone', () => {
    const normal = sidebarFor('tenant', 'uz', t).map((i) => i.key);
    expect(normal.length).toBeGreaterThan(1);
    expect(normal).toContain('overview');
    expect(normal).toContain('billing');
    expect(sidebarFor('tenant', 'uz', t, { blocked: false }).map((i) => i.key)).toEqual(normal);
    expect(bottomNavFor('tenant', 'uz', t, { blocked: false }).map((i) => i.key))
      .toEqual(bottomNavFor('tenant', 'uz', t).map((i) => i.key));
  });

  it('does not broaden or narrow any other persona', () => {
    for (const persona of ['platform', 'staff', 'client'] as const) {
      expect(sidebarFor(persona, 'uz', t, { blocked: true }).map((i) => i.key))
        .toEqual(sidebarFor(persona, 'uz', t).map((i) => i.key));
      expect(bottomNavFor(persona, 'uz', t, { blocked: true }).map((i) => i.key))
        .toEqual(bottomNavFor(persona, 'uz', t).map((i) => i.key));
    }
  });
});

// ── the shells agree with the routing ───────────────────────────────────────

describe.each(LOCALES)('a blocked tenant in %s', (lng) => {
  beforeEach(async () => { await i18n.changeLanguage(lng); });

  it.each(ROLES)('offers %s no destination that dead-ends in the gate', async (role) => {
    ROLE = role;
    renderShell(`/${lng}/tenant/overview`);
    await waitFor(() => expect(gateShown()).toBe(true));

    const hrefs = offeredHrefs();
    expect(hrefs.length, 'some way forward is still offered').toBeGreaterThan(0);

    for (const href of hrefs) {
      cleanup();
      renderShell(href);
      await waitFor(() => expect(document.querySelector('main')).toBeTruthy());
      expect(gateShown(), `${href} dead-ends in the gate`).toBe(false);
    }
  });

  it('keeps the recovery destination itself on offer', async () => {
    renderShell(`/${lng}/tenant/customers`);
    await waitFor(() => expect(gateShown()).toBe(true));
    expect(offeredHrefs()).toContain(`/${lng}/tenant/billing`);
    // Labelled from the bundle on both nav surfaces, not invented copy.
    expect(screen.getAllByRole('link', { name: i18n.getFixedT(lng)('nav.billing') as string }).length)
      .toBeGreaterThan(0);
  });

  it('titles the document after the gate, not after the hidden destination', async () => {
    renderShell(`/${lng}/tenant/customers`);
    await waitFor(() => expect(gateShown()).toBe(true));
    await waitFor(() => expect(document.title)
      .toBe(`${i18n.getFixedT(lng)('trial.blockedTitle')} · Muddato`));
    expect(document.title).not.toContain(i18n.getFixedT(lng)('nav.customers') as string);
  });

  it('keeps that title when the URL moves between gated paths', async () => {
    // The gate stays mounted across blocked navigations while the per-route
    // title manager re-runs, so the gate's title has to re-run too. A real
    // in-router navigation is the only way to reproduce that: re-mounting a
    // MemoryRouter at another entry would not exercise the same fiber.
    renderShell(`/${lng}/tenant/customers`);
    await waitFor(() => expect(gateShown()).toBe(true));
    document.title = 'stale · Muddato';

    await act(async () => { navigateTo!(`/${lng}/tenant/reports`); });
    await waitFor(() => expect(document.title)
      .toBe(`${i18n.getFixedT(lng)('trial.blockedTitle')} · Muddato`));
  });
});

describe('an unblocked tenant still gets the whole product', () => {
  beforeEach(() => {
    ME = { tenants: [HEALTHY] } as unknown as MeResponse;
    ACTIVE_TENANT_ID = HEALTHY.id;
  });

  it('keeps every ordinary destination on offer', async () => {
    renderShell('/uz/tenant/overview');
    await waitFor(() => expect(gateShown()).toBe(false));
    const hrefs = offeredHrefs();
    for (const slug of ['overview', 'customers', 'installments', 'payments', 'billing', 'settings']) {
      expect(hrefs, `/${slug} is still offered`).toContain(`/uz/tenant/${slug}`);
    }
  });
});
