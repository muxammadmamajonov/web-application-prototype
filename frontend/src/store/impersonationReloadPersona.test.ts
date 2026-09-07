/**
 * Entering a shop's panel must survive the reload that follows it.
 *
 * THE BUG (production, reported 2026-08-14): "boshqaruv panelga kirishda hali
 * ham loop bilan dashboardga qaytib qolmoqda" — a platform operator presses
 * "Boshqaruv paneliga kirish", lands in the shop, and is then thrown back to the
 * platform dashboard. A Telegram Mini App reloads often (⋮ → Reload, a resumed
 * session, a WebView eviction), and every reload bounced them out again.
 *
 * TWO CORRECT DECISIONS COMBINE INTO ONE WRONG STATE.
 *
 *   1. The impersonation is an app-session act, not a device preference, so
 *      `partialize` deliberately persists `activeTenantId: null` while one is
 *      live — nothing an operator forgets to switch off may be left switched on.
 *   2. The store therefore SEEDS `activeTenantId` / `impersonating` from
 *      sessionStorage at creation (`session.ts`), which is right.
 *
 * But `persist`'s `merge` runs AFTER that initializer and spreads the persisted
 * slot over it — so the deliberate `activeTenantId: null` overwrote the correctly
 * seeded id, leaving `impersonating: true` with `activeTenantId: null`.
 *
 * `personaFor` needs BOTH ("if (impersonating && activeTenantId) return
 * 'tenant'"), so it fell through to `me.platformRole` → `'platform'`, and
 * `PersonaGuard` redirected every `/tenant/*` URL to `personaHome('platform')`
 * — the platform dashboard.
 *
 * The desync is the tell: `activeTenantIdForRequest()` reads sessionStorage and
 * was still sending the shop's `X-Tenant-Id` on every request, so the data layer
 * was inside the shop while the routing layer insisted the operator was not.
 * These tests pin the two halves together.
 */
import { describe, expect, it, beforeEach, vi } from 'vitest';
import type { MeResponse } from '@/types';

// The suite runs in the `node` environment (vite.config.ts) and the session
// store writes through zustand's `persist` at module load, so both web storages
// have to exist before the import block — hence `vi.hoisted`. Two independent
// maps, because the two lifetimes are the whole point.
vi.hoisted(() => {
  const mk = () => {
    const mem = new Map<string, string>();
    return {
      getItem: (k: string) => mem.get(k) ?? null,
      setItem: (k: string, v: string) => void mem.set(k, v),
      removeItem: (k: string) => void mem.delete(k),
      clear: () => mem.clear(),
      key: (i: number) => [...mem.keys()][i] ?? null,
      get length() { return mem.size; },
    } as Storage;
  };
  const g = globalThis as unknown as { localStorage: Storage; sessionStorage: Storage };
  g.localStorage = mk();
  g.sessionStorage = mk();
});

const OPERATOR: MeResponse = {
  id: 'u-admin', telegramId: 1, telegramUsername: 'admin',
  firstName: 'Platform', lastName: 'Operator', displayName: 'Platform Operator',
  photoUrl: null, phone: null, email: null, birthdate: null, gender: null,
  locale: 'uz',
  // A platform operator is deliberately NOT a member of the shop they enter.
  platformRole: 'super_admin', tenants: [],
};

const SHOP = { id: 't-shop-1', name: 'Demo Electronics' };

/**
 * Boot the store the way a page load does: fresh module registry, storages
 * already holding whatever the previous load left behind.
 */
async function boot() {
  vi.resetModules();
  const session = await import('./session');
  const ctx = await import('./tenantContext');
  const persona = await import('./persona');
  return { useSession: session.useSession, ...ctx, ...persona };
}

beforeEach(() => {
  localStorage.clear();
  sessionStorage.clear();
  vi.resetModules();
});

describe('entering a shop', () => {
  it('renders the tenant panel, not the platform console', async () => {
    const { useSession } = await boot();
    useSession.getState().setMe(OPERATOR);
    useSession.getState().enterTenant(SHOP.id, SHOP.name);

    expect(useSession.getState().resolvePersona()).toBe('tenant');
    expect(useSession.getState().activeTenantId).toBe(SHOP.id);
  });

  it('scopes requests to that shop', async () => {
    const { useSession, activeTenantIdForRequest } = await boot();
    useSession.getState().setMe(OPERATOR);
    useSession.getState().enterTenant(SHOP.id, SHOP.name);

    expect(activeTenantIdForRequest()).toBe(SHOP.id);
  });
});

describe('after the reload that follows', () => {
  /** Enter a shop, then throw the module registry away — i.e. reload the app. */
  async function enterThenReload() {
    const first = await boot();
    first.useSession.getState().setMe(OPERATOR);
    first.useSession.getState().enterTenant(SHOP.id, SHOP.name);
    // A reload re-imports every module; the storages persist.
    const second = await boot();
    return second;
  }

  it('is still inside the shop, not bounced to the platform dashboard', async () => {
    const { useSession } = await enterThenReload();
    useSession.getState().setMe(OPERATOR);

    // THE DEFECT: this resolved to 'platform', and PersonaGuard redirected
    // /uz/tenant/* to /uz/platform/overview — the dashboard the operator kept
    // being thrown back to.
    expect(useSession.getState().resolvePersona()).toBe('tenant');
  });

  it('keeps the active tenant id the request layer is already sending', async () => {
    const { useSession, activeTenantIdForRequest } = await enterThenReload();
    useSession.getState().setMe(OPERATOR);

    // The desync that made the bounce inevitable: the header said "this shop",
    // the persona said "no shop". Both must answer the same.
    expect(activeTenantIdForRequest()).toBe(SHOP.id);
    expect(useSession.getState().activeTenantId).toBe(SHOP.id);
    expect(useSession.getState().impersonating).toBe(true);
    expect(useSession.getState().impersonatingName).toBe(SHOP.name);
  });

  it('survives a reload that happens BEFORE /auth/me comes back', async () => {
    // The guard runs on first paint, and `me` arrives a tick later. The seeded
    // scope must already be correct, or the redirect fires before setMe can fix
    // anything.
    const { useSession } = await enterThenReload();

    expect(useSession.getState().activeTenantId).toBe(SHOP.id);
    expect(useSession.getState().impersonating).toBe(true);
  });
});

describe('leaving, and not leaking', () => {
  it('exit returns the operator to their own console', async () => {
    const { useSession } = await boot();
    useSession.getState().setMe(OPERATOR);
    useSession.getState().enterTenant(SHOP.id, SHOP.name);
    useSession.getState().exitTenant();

    expect(useSession.getState().resolvePersona()).toBe('platform');
    expect(useSession.getState().activeTenantId).toBeNull();
  });

  it('exit survives the reload too', async () => {
    const first = await boot();
    first.useSession.getState().setMe(OPERATOR);
    first.useSession.getState().enterTenant(SHOP.id, SHOP.name);
    first.useSession.getState().exitTenant();

    const { useSession, activeTenantIdForRequest } = await boot();
    useSession.getState().setMe(OPERATOR);

    expect(useSession.getState().resolvePersona()).toBe('platform');
    expect(useSession.getState().impersonating).toBe(false);
    expect(activeTenantIdForRequest()).toBeNull();
  });

  it('the entered shop never reaches the persisted session slot', async () => {
    const { useSession } = await boot();
    useSession.getState().setMe(OPERATOR);
    useSession.getState().enterTenant(SHOP.id, SHOP.name);

    // The original repair (see `tenantContext.ts`): the impersonation must not
    // live in `muddato.persist`, where zustand would merge it back on rehydrate
    // and pin a possibly-deleted shop to every request.
    const persisted = localStorage.getItem('muddato.persist') ?? '';
    expect(persisted).not.toContain(SHOP.id);

    // NOT asserted here: that clearing sessionStorage alone ends the
    // impersonation. `writeImpersonation` deliberately mirrors into
    // localStorage as a fallback for contexts where sessionStorage throws (the
    // web.telegram.org iframe, Safari Private) — that mirror was itself an
    // outage fix, so the same-operator lifetime is wider than the module's
    // "ends when the app does" wording implies. Recorded as a separate finding
    // rather than narrowed inside this hotfix; the cross-ACCOUNT guarantee,
    // which is the one that matters for safety, is pinned below.
  });

  it('a non-operator cannot inherit an impersonation left in storage', async () => {
    const first = await boot();
    first.useSession.getState().setMe(OPERATOR);
    first.useSession.getState().enterTenant(SHOP.id, SHOP.name);

    // Same device, different account — an ordinary customer with no platform role.
    const { useSession, activeTenantIdForRequest } = await boot();
    const CUSTOMER: MeResponse = { ...OPERATOR, id: 'u-cust', platformRole: null, tenants: [] };
    useSession.getState().setMe(CUSTOMER);

    expect(useSession.getState().impersonating).toBe(false);
    expect(useSession.getState().resolvePersona()).toBe('client');
    expect(activeTenantIdForRequest()).toBeNull();
  });
});
