// @vitest-environment jsdom
/**
 * COR-02 — the documented cleanup contract, pinned for BOTH sign-out paths.
 *
 * `api.ts` states it as prose above `dropStoredSession()`: "Explicit logout
 * already does this via the store reset(); this covers the EXPIRY paths
 * (forceLogout, /auth/me 401)." That sentence was not true. `lib/auth#logout()`
 * called `setSessionToken(null)` FIRST and `useSession.reset()` second, and the
 * store is `persist`-backed — so the reset wrote `muddato.persist` straight back
 * after storage had been cleared. The key was cleared and then resurrected as an
 * empty blob, leaving the two paths with different end-states despite the
 * comment claiming they converge.
 *
 * `api.test.ts` covers `setSessionToken(null)` (the expiry-side primitive) in
 * isolation, and nothing covered `logout()` as a unit. This file pins the whole
 * contract: both paths must end with the same three keys absent.
 *
 * The localStorage shim is installed in `vi.hoisted` because zustand's `persist`
 * binds its storage the moment `@/store/session` is imported — this repo's jsdom
 * environment provides no storage of its own.
 */
import { describe, expect, it, beforeEach, afterEach, vi } from 'vitest';

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

import { logout } from '@/lib/auth';
import { setSessionToken } from '@/lib/api';
import { useSession } from '@/store/session';
import { queryClient } from '@/lib/queryClient';

/** Every JS-readable trace of a session. */
const KEYS = ['muddato.authed', 'muddato.session', 'muddato.persist'] as const;
const snapshot = () => Object.fromEntries(KEYS.map((k) => [k, localStorage.getItem(k)]));

let assign: ReturnType<typeof vi.fn>;
let realLocation: Location;

/** A fully signed-in session: breadcrumb, Bearer fallback, persisted tenant. */
function signIn() {
  LS.clear();
  useSession.setState({
    me: { tenants: [{ id: 't1', role: 'owner' }] } as never,
    activeTenantId: 't1', impersonating: true, impersonatingName: 'Shop',
  });
  localStorage.setItem('muddato.authed', '1');
  localStorage.setItem('muddato.session', 'header.payload.signature');
  // The store's own persist write has already happened via setState above.
  expect(localStorage.getItem('muddato.persist'), 'fixture really persisted').not.toBeNull();
}

beforeEach(() => {
  realLocation = window.location;
  assign = vi.fn();
  Object.defineProperty(window, 'location', {
    configurable: true, writable: true,
    value: { ...realLocation, assign, pathname: '/uz/tenant/overview' },
  });
  signIn();
});
afterEach(() => {
  Object.defineProperty(window, 'location', { configurable: true, writable: true, value: realLocation });
  useSession.setState({ me: null, activeTenantId: null, impersonating: false, impersonatingName: null });
  LS.clear();
  vi.restoreAllMocks();
});

describe('explicit logout', () => {
  it('leaves no JS-readable trace of the session', () => {
    logout();
    expect(snapshot()).toEqual({
      'muddato.authed': null,
      'muddato.session': null,
      'muddato.persist': null,
    });
  });

  it('does not resurrect muddato.persist via the store reset', () => {
    // The regression this exists for: reset() writes through `persist`, so if it
    // runs AFTER storage is cleared the key comes back as an empty blob.
    logout();
    expect(localStorage.getItem('muddato.persist')).toBeNull();
  });

  it('still empties the in-memory session', () => {
    logout();
    expect(useSession.getState().me).toBeNull();
    expect(useSession.getState().activeTenantId).toBeNull();
    expect(useSession.getState().impersonating).toBe(false);
    expect(useSession.getState().impersonatingName).toBeNull();
  });

  it('still clears the query cache', () => {
    queryClient.setQueryData(['probe-contract'], { leftover: true });
    logout();
    expect(queryClient.getQueryData(['probe-contract'])).toBeUndefined();
  });

  it('still navigates to login', () => {
    logout();
    expect(assign).toHaveBeenCalledWith('/auth/login');
  });
});

describe('the expiry path', () => {
  it('clears the same three keys', () => {
    // `dropStoredSession()` (forceLogout, /auth/me 401) removes exactly what
    // `setSessionToken(null)` does; this is the shared primitive.
    setSessionToken(null);
    expect(snapshot()).toEqual({
      'muddato.authed': null,
      'muddato.session': null,
      'muddato.persist': null,
    });
  });
});

describe('the two paths agree', () => {
  it('reach an identical storage end-state, as api.ts documents', () => {
    logout();
    const afterLogout = snapshot();

    signIn();
    setSessionToken(null);
    const afterExpiry = snapshot();

    expect(afterLogout).toEqual(afterExpiry);
  });
});
