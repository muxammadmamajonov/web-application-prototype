/**
 * A stale tenant scope must not survive, and must not contaminate every page.
 *
 * THE BUG (production, 2026-08-11). Entering the management panel showed
 * "Topilmadi — o‘chirilgan yoki ko‘chirilgan bo‘lishi mumkin" on page after
 * page. Nothing on those pages was broken: the ONE ambient input to every
 * request — the `X-Tenant-Id` header — pointed at a tenant that no longer
 * resolved, so `resolve_tenant` answered 404 to all of them, and a 404 with no
 * mapped code renders as exactly that sentence.
 *
 * Two decisions put it there:
 *
 *   1. `partialize` persisted `impersonating` to localStorage, so a platform
 *      operator who entered a shop for support and closed the Mini App without
 *      pressing "Chiqish" came back still inside it — days later, on a new app
 *      session, with no re-authorisation.
 *   2. `setMe` EXEMPTED impersonation from the "does this tenant still belong to
 *      you" check, unconditionally. A `DELETE /platform/tenants/{id}` is a hard
 *      delete, so the id could be pointing at nothing at all and nothing would
 *      notice.
 *
 * The tests are about STATE LIFETIME and REQUEST SCOPE — the two things that
 * were wrong — rather than about any page's rendering, because the defect was
 * never in a page.
 */
import { describe, expect, it, beforeEach, vi } from 'vitest';
import type { MeResponse } from '@/types';

// The suite runs in the `node` environment (vite.config.ts) and the session store
// writes through zustand's `persist` at module load, so BOTH web storages have to
// exist before the import block — hence `vi.hoisted`. Two independent maps,
// because the whole point of the fix is that these are two different lifetimes.
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

vi.mock('@/lib/queryClient', () => ({
  queryClient: { removeQueries: vi.fn(), invalidateQueries: vi.fn() },
}));

import { useSession } from './session';
import {
  activeTenantIdForRequest, readImpersonation, stripLegacyImpersonation, writeImpersonation,
} from './tenantContext';

const PERSIST = 'muddato.persist';
const DELETED = 'dead-tenant-0000';

const OPERATOR: MeResponse = {
  id: 'u-op', telegramId: 1, telegramUsername: 'op', firstName: 'Op', lastName: null,
  displayName: 'Op', photoUrl: null, phone: null, email: null, birthdate: null,
  gender: null, locale: 'uz', platformRole: 'super_admin', tenants: [],
};

const OWNER: MeResponse = {
  ...OPERATOR, id: 'u-owner', platformRole: null,
  tenants: [
    { id: 'shop-a', slug: 'a', name: 'A', role: 'owner', status: 'active', planCode: 'starter', trialEndsAt: null },
    { id: 'shop-b', slug: 'b', name: 'B', role: 'owner', status: 'active', planCode: 'starter', trialEndsAt: null },
  ],
};

function resetStore() {
  useSession.setState({
    me: null, activeTenantId: null, impersonating: false, impersonatingName: null,
  });
}

beforeEach(() => {
  localStorage.clear();
  sessionStorage.clear();
  // The impersonation's source of truth is a module-level variable (storage is
  // only a best-effort copy, because storage can be blocked in a Telegram
  // iframe). Clearing the storages alone would leave it set between tests.
  writeImpersonation(null);
  resetStore();
});

describe('impersonation lifetime', () => {
  it('is not written to the device store', () => {
    // localStorage outlives the app; an act of looking at another business's
    // real data must not.
    useSession.getState().enterTenant(DELETED, 'Deleted Shop');

    const persisted = JSON.parse(localStorage.getItem(PERSIST) ?? '{}');
    expect(persisted.state?.impersonating).toBeFalsy();
    expect(persisted.state?.activeTenantId ?? null).toBeNull();

    // …it lives in the app session instead, so a reload still works.
    expect(readImpersonation()).toEqual({ tenantId: DELETED, name: 'Deleted Shop' });
  });

  it('is cleared by exiting', () => {
    useSession.getState().enterTenant('shop-a', 'A');
    useSession.getState().exitTenant();
    expect(readImpersonation()).toBeNull();
    expect(activeTenantIdForRequest()).toBeNull();
  });

  it('is dropped from a slot an older build persisted', () => {
    // THE REPAIR for browsers currently affected. There is no server-side record
    // to migrate — the stale reference only ever existed in the browser — so the
    // fix has to remove it on the next launch or those users stay broken.
    const legacy = { activeTenantId: DELETED, impersonating: true, impersonatingName: 'Deleted Shop' };
    const cleaned = stripLegacyImpersonation(legacy);
    expect(cleaned.impersonating).toBeUndefined();
    expect(cleaned.activeTenantId).toBeUndefined();
  });

  it('leaves an ordinary owner selection alone', () => {
    // The repair must not log a multi-shop owner out of their chosen shop.
    const normal = { activeTenantId: 'shop-b' };
    expect(stripLegacyImpersonation(normal)).toEqual({ activeTenantId: 'shop-b' });
  });
});

describe('request scope', () => {
  it('sends nothing for an operator with no shop of their own', () => {
    useSession.getState().setMe(OPERATOR);
    expect(activeTenantIdForRequest()).toBeNull();
  });

  it('sends the impersonated shop while the operator is inside it', () => {
    useSession.getState().setMe(OPERATOR);
    useSession.getState().enterTenant('shop-x', 'X');
    expect(activeTenantIdForRequest()).toBe('shop-x');
  });

  it('still sends the scope when BOTH web storages are blocked', () => {
    // THE OUTAGE (production, 2026-08-12): operators could not open any shop's
    // panel. The impersonation lived only in sessionStorage, and the Mini App
    // runs inside a web.telegram.org iframe where partitioned storage makes
    // sessionStorage throw. The write was caught and swallowed, so every
    // /tenant/* request went out with no scope and the API answered 404
    // no_tenant_membership to all of them.
    //
    // The scope of the requests THIS TAB is making cannot depend on a storage
    // API succeeding.
    const g = globalThis as unknown as { localStorage: Storage; sessionStorage: Storage };
    const realLocal = g.localStorage;
    const realSession = g.sessionStorage;
    const blocked = { get: () => { throw new Error('SecurityError: storage is blocked'); } };
    Object.defineProperty(g, 'sessionStorage', blocked);
    Object.defineProperty(g, 'localStorage', blocked);
    try {
      useSession.getState().enterTenant('shop-x', 'X');
      expect(activeTenantIdForRequest()).toBe('shop-x');
      expect(readImpersonation()).toEqual({ tenantId: 'shop-x', name: 'X' });
    } finally {
      Object.defineProperty(g, 'sessionStorage', { value: realSession, configurable: true, writable: true });
      Object.defineProperty(g, 'localStorage', { value: realLocal, configurable: true, writable: true });
    }
  });

  it('sends the owner-selected shop for an ordinary owner', () => {
    useSession.getState().setActiveTenant('shop-b');
    useSession.getState().setMe(OWNER);
    expect(activeTenantIdForRequest()).toBe('shop-b');
  });
});

describe('setMe validates the scope it keeps', () => {
  it('refuses to keep an impersonated id for a non-operator', () => {
    // The exemption was unconditional, so ANY stale flag — a different account
    // on the same device, a half-finished exit — pinned an unvalidated tenant id
    // onto every request. The exemption now requires the platform role that
    // makes impersonation legitimate in the first place.
    writeImpersonation({ tenantId: DELETED, name: 'Deleted Shop' });
    useSession.setState({ impersonating: true, impersonatingName: 'Deleted Shop', activeTenantId: DELETED });

    useSession.getState().setMe(OWNER);

    expect(useSession.getState().activeTenantId).toBe('shop-a');
    expect(readImpersonation()).toBeNull();
  });

  it('clears a foreign shop id for an operator who is not impersonating', () => {
    useSession.setState({ activeTenantId: DELETED, impersonating: false });
    useSession.getState().setMe(OPERATOR);
    expect(useSession.getState().activeTenantId).toBeNull();
    expect(activeTenantIdForRequest()).toBeNull();
  });

  it('keeps a live impersonation for a real operator', () => {
    // The legitimate case must keep working: support IS entering a shop you do
    // not belong to.
    useSession.getState().setMe(OPERATOR);
    useSession.getState().enterTenant('shop-x', 'X');
    useSession.getState().setMe(OPERATOR);
    expect(useSession.getState().activeTenantId).toBe('shop-x');
    expect(useSession.getState().resolvePersona()).toBe('tenant');
  });
});

describe('the operator lands in their own console on a fresh app session', () => {
  it('does not resolve to the tenant persona from a device-persisted flag', () => {
    // Simulate the exact production state: the previous build's localStorage.
    localStorage.setItem(PERSIST, JSON.stringify({
      state: { activeTenantId: DELETED, impersonating: true, impersonatingName: 'Deleted Shop' },
      version: 0,
    }));
    // A fresh app session has no sessionStorage entry…
    expect(readImpersonation()).toBeNull();
    // …so nothing pins the dead scope onto the requests this launch makes.
    const persisted = stripLegacyImpersonation(
      JSON.parse(localStorage.getItem(PERSIST)!).state as Record<string, unknown>,
    );
    expect(persisted.activeTenantId).toBeUndefined();

    resetStore();
    useSession.getState().setMe(OPERATOR);
    expect(useSession.getState().resolvePersona()).toBe('platform');
  });
});
