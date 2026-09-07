// @vitest-environment jsdom
/**
 * OBS-004 + OBS-005 — the blocked gate, exercised against the real `TenantShell`.
 *
 * OBS-004: sign-out lives only on `ProfilePage`, and `/tenant/profile` renders
 * the gate while a tenant is blocked. A blocked owner who cannot pay — a former
 * owner, a wrong account on a shared phone, someone who simply wants out — had
 * no way to leave their own session. The route gate is correct and stays narrow
 * (billing remains the only ordinary page routable while blocked); the escape
 * belongs ON the gate, not in a widened route table.
 *
 * OBS-005: the gate replaces the entire routed page inside `<main>`, so its
 * title is that surface's page heading. It was an `<h2>`, leaving the blocked
 * screen — the one screen a stuck user must be able to orient on — with no h1.
 *
 * The logout assertion deliberately does NOT mock `@/lib/auth`. Proving
 * "logout() was called" would pass even if the primitive stopped clearing
 * anything. The real store, the real app query client and the real localStorage
 * keys are used, and the four observable effects of a full sign-out are
 * asserted; only `window.location.assign` is stubbed, because jsdom cannot
 * navigate.
 */
import { describe, expect, it, beforeEach, afterEach, vi } from 'vitest';

/**
 * Stateful localStorage backed by a Map — this repo's jsdom environment ships
 * no storage at all (cf. the same shim in `lib/api.test.ts`). It is installed
 * in `vi.hoisted` because zustand's `persist` binds its storage the moment
 * `@/store/session` is imported, which is before any `beforeEach` can run.
 * Real keys, real reads and writes, so `setSessionToken(null)` runs for real.
 */
const LS = vi.hoisted(() => {
  const store = new Map<string, string>();
  const shim = {
    getItem: (k: string) => (store.has(k) ? store.get(k)! : null),
    setItem: (k: string, v: string) => { store.set(k, v); },
    removeItem: (k: string) => { store.delete(k); },
    clear: () => { store.clear(); },
    key: () => null,
    length: 0,
  };
  Object.defineProperty(globalThis, 'localStorage', {
    value: shim, configurable: true, writable: true,
  });
  return shim;
});

import { render, screen, cleanup, fireEvent, waitFor, within } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import i18n from '@/i18n';
import { useSession } from '@/store/session';
import { queryClient as appQueryClient } from '@/lib/queryClient';
import type { MeResponse } from '@/types';

const BLOCKED = { id: 't-blk', slug: 'blk', name: 'Blocked Shop', role: 'owner', status: 'blocked', planCode: 'lite', trialEndsAt: null };
const HEALTHY = { id: 't-ok', slug: 'ok', name: 'Healthy Shop', role: 'owner', status: 'active', planCode: 'lite', trialEndsAt: null };

let ME: MeResponse;

/** Flips the Mini-App detection the gate's sign-out policy keys off. */
const TG = vi.hoisted(() => ({ inside: false }));
vi.mock('@/lib/telegram', async (orig) => ({
  ...(await orig<Record<string, unknown>>()),
  isInsideTelegramClient: () => TG.inside,
}));

vi.mock('@/hooks/useMe', () => ({
  useMe: () => ({ data: ME, isLoading: false, isError: false, error: null }),
}));
// The real session store is used on purpose (see header), so only the network
// hooks BillingPage would otherwise fire from jsdom are stubbed.
vi.mock('@/lib/queries', async (orig) => ({
  ...(await orig<Record<string, unknown>>()),
  useTenantBilling: () => ({ data: { plan_code: 'lite', status: 'blocked', active_installments: 0 }, isLoading: false, isError: false, error: null, refetch: vi.fn() }),
  useTenantInvoices: () => ({ data: [], isLoading: false, isError: false, error: null, refetch: vi.fn() }),
  useTenantTopupInfo: () => ({ data: { balance: 0, cards: [] }, isLoading: false, isError: false, error: null, refetch: vi.fn() }),
  useTenantTopups: () => ({ data: [], isLoading: false, isError: false, error: null, refetch: vi.fn() }),
  usePublicPlans: () => ({ data: [], isLoading: false, isError: false, error: null, refetch: vi.fn() }),
}));

import { TenantShell } from '@/routes/router';

const LOCALES = ['uz', 'ru'] as const;
const ROLES = ['owner', 'manager'] as const;

function renderShell(at: string) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={[at]}>
        <Routes><Route path="/:locale/tenant/*" element={<TenantShell />} /></Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

/** Seed the REAL store the way a signed-in blocked user's session looks. */
function signInBlocked(role: string) {
  ME = { tenants: [{ ...BLOCKED, role }] } as unknown as MeResponse;
  useSession.setState({ me: ME, activeTenantId: BLOCKED.id, impersonating: false, impersonatingName: null });
  localStorage.setItem('muddato.authed', '1');
  localStorage.setItem('muddato.persist', JSON.stringify({ state: { activeTenantId: BLOCKED.id }, version: 0 }));
}

const T = (lng: string, k: string) => i18n.getFixedT(lng)(k) as string;
const gate = () => screen.findByRole('alert');
const mainEl = () => document.querySelector('main')!;

let assign: ReturnType<typeof vi.fn>;
let realLocation: Location;

beforeEach(async () => {
  cleanup();
  LS.clear();
  signInBlocked('owner');
  document.title = '';
  await i18n.changeLanguage('uz');
  if (!Element.prototype.scrollIntoView) Element.prototype.scrollIntoView = () => {};
  assign = vi.fn();
  realLocation = window.location;
  Object.defineProperty(window, 'location', {
    configurable: true, writable: true,
    value: { ...realLocation, assign, pathname: '/uz/tenant/overview' },
  });
});
afterEach(async () => {
  Object.defineProperty(window, 'location', { configurable: true, writable: true, value: realLocation });
  useSession.setState({ me: null, activeTenantId: null, impersonating: false, impersonatingName: null });
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
  await i18n.changeLanguage('uz');
});

// ── OBS-005: the gate is the page, so it carries the page heading ───────────

describe.each(LOCALES)('the blocked screen is a titled page in %s', (lng) => {
  beforeEach(async () => { await i18n.changeLanguage(lng); });

  it('has exactly one h1 inside <main>', async () => {
    renderShell(`/${lng}/tenant/overview`);
    await gate();
    const h1s = Array.from(mainEl().querySelectorAll('h1'));
    expect(h1s).toHaveLength(1);
    expect(h1s[0]!.textContent).toBe(T(lng, 'trial.blockedTitle'));
  });

  it('does not leave the gate title sitting in an h2', async () => {
    renderShell(`/${lng}/tenant/overview`);
    await gate();
    for (const h2 of Array.from(mainEl().querySelectorAll('h2'))) {
      expect(h2.textContent, 'the page title is masquerading as a section heading')
        .not.toBe(T(lng, 'trial.blockedTitle'));
    }
  });

  it('still titles the document after the gate', async () => {
    renderShell(`/${lng}/tenant/customers`);
    await gate();
    await waitFor(() =>
      expect(document.title).toBe(`${T(lng, 'trial.blockedTitle')} · Muddato`));
  });
});

// ── OBS-004: a blocked user can always leave ────────────────────────────────

describe.each(LOCALES)('a blocked user in %s', (lng) => {
  beforeEach(async () => { await i18n.changeLanguage(lng); });

  it.each(ROLES)('is offered recovery, support AND sign-out as %s', async (role) => {
    signInBlocked(role);
    renderShell(`/${lng}/tenant/overview`);
    const g = await gate();
    // The two existing ways forward must survive the new one.
    expect(within(g).getByRole('link', { name: T(lng, 'topup.title') })
      .getAttribute('href')).toBe(`/${lng}/tenant/billing`);
    expect(within(g).getByRole('link', { name: T(lng, 'billing.contactSupport') })).toBeTruthy();
    // The way out.
    expect(within(g).getByRole('button', { name: T(lng, 'common.logout') })).toBeTruthy();
  });

  /**
   * COR-03 / OBS-008 — the gate's three controls are the recovery path for a
   * user who is almost certainly on a phone, so they must carry the shared
   * coarse-pointer tap-target policy. `.tap-target` lives behind
   * `@media (pointer: coarse)` (globals.css), so a fine-pointer browser still
   * computes the compact 40px height and only a touch device gets the 44px
   * minimum — the class is the policy, and the class is what is asserted.
   * `buttonClasses({size:'md'})` does NOT add it; it is applied per call site.
   */
  it('carries the shared tap-target policy on every recovery control', async () => {
    renderShell(`/${lng}/tenant/overview`);
    const g = await gate();
    const controls = [
      within(g).getByRole('link', { name: T(lng, 'topup.title') }),
      within(g).getByRole('link', { name: T(lng, 'billing.contactSupport') }),
      within(g).getByRole('button', { name: T(lng, 'common.logout') }),
    ];
    expect(controls).toHaveLength(3);
    for (const el of controls) {
      expect(el.className.split(/\s+/), `${el.textContent?.trim()} lacks .tap-target`)
        .toContain('tap-target');
      // No hand-rolled second 44px utility alongside the shared one.
      expect(el.className, `${el.textContent?.trim()} hard-codes a raw 44px size`)
        .not.toMatch(/min-h-\[44px\]|h-\[44px\]|min-h-11\b/);
    }
  });

  it('can reach sign-out by keyboard', async () => {
    renderShell(`/${lng}/tenant/overview`);
    const g = await gate();
    const btn = within(g).getByRole('button', { name: T(lng, 'common.logout') });
    btn.focus();
    expect(document.activeElement).toBe(btn);
    expect(btn.hasAttribute('disabled')).toBe(false);
  });
});

describe('signing out from the gate really ends the session', () => {
  it('clears the session, the persisted state and the cache, then goes to login', async () => {
    appQueryClient.setQueryData(['probe-me'], { leftover: true });
    expect(appQueryClient.getQueryData(['probe-me'])).toBeTruthy();

    renderShell('/uz/tenant/overview');
    const g = await gate();
    fireEvent.click(within(g).getByRole('button', { name: i18n.t('common.logout') }));

    // Every observable effect of the real logout primitive.
    // 1. The breadcrumb `RequireAuth` gates on is gone.
    expect(localStorage.getItem('muddato.authed'), 'the auth breadcrumb survived').toBeNull();
    // 2. (COR-02) The persisted tenant/impersonation slot is REMOVED, not
    //    re-created empty. `logout()` used to clear it and then immediately let
    //    zustand's persist middleware write it back, because `reset()` ran
    //    after `setSessionToken(null)`. Requiring absence — not merely
    //    "identity-free" — is what pins the ordering.
    expect(localStorage.getItem('muddato.persist'),
      'muddato.persist was re-created after being cleared').toBeNull();
    // 3. The in-memory session is empty.
    expect(useSession.getState().me).toBeNull();
    expect(useSession.getState().activeTenantId).toBeNull();
    // 4. The cached `me` / installments / payments are gone.
    expect(appQueryClient.getQueryData(['probe-me']), 'the query cache survived').toBeUndefined();
    // 5. And the user actually lands on login.
    expect(assign).toHaveBeenCalledWith('/auth/login');
  });
});

describe('inside Telegram the escape is the one that works there', () => {
  beforeEach(() => { TG.inside = true; });
  afterEach(() => { TG.inside = false; });

  it('offers support instead of a sign-out that would bounce straight back', async () => {
    // `/auth/login` auto-logs-in from initData inside the Mini-App, so a
    // browser-style sign-out is a loop, not an exit. This is the SAME policy
    // ProfilePage applies — pinned here so the two cannot diverge.
    renderShell('/uz/tenant/overview');
    const g = await gate();
    expect(within(g).queryByRole('button', { name: i18n.t('common.logout') })).toBeNull();
    expect(within(g).getByRole('link', { name: i18n.t('billing.contactSupport') })).toBeTruthy();
    expect(within(g).getByRole('link', { name: i18n.t('topup.title') })).toBeTruthy();
  });

  it('still titles the blocked screen with a single h1', async () => {
    renderShell('/uz/tenant/overview');
    await gate();
    expect(Array.from(mainEl().querySelectorAll('h1'))).toHaveLength(1);
  });
});

// ── the gate must not become a back door ────────────────────────────────────

describe('the route gate stays exactly as narrow as it was', () => {
  it.each(['overview', 'customers', 'installments', 'payments', 'staff', 'reports', 'settings', 'profile', 'menu'])(
    'still shows the gate at /%s', async (path) => {
      renderShell(`/uz/tenant/${path}`);
      await waitFor(() => expect(screen.queryByText(i18n.t('trial.blockedTitle'))).not.toBeNull());
    });

  it('still lets billing through', async () => {
    renderShell('/uz/tenant/billing');
    await waitFor(() => expect(screen.getAllByText(i18n.t('billing.balance')).length).toBeGreaterThan(0));
    expect(screen.queryByText(i18n.t('trial.blockedTitle'))).toBeNull();
  });

  it('leaves a healthy tenant untouched', async () => {
    ME = { tenants: [HEALTHY] } as unknown as MeResponse;
    useSession.setState({ me: ME, activeTenantId: HEALTHY.id });
    renderShell('/uz/tenant/overview');
    await waitFor(() => expect(document.querySelector('main')).toBeTruthy());
    expect(screen.queryByText(i18n.t('trial.blockedTitle'))).toBeNull();
    // No stray sign-out control leaked onto ordinary tenant pages.
    expect(screen.queryByRole('button', { name: i18n.t('common.logout') })).toBeNull();
  });
});
