import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { AxiosHeaders } from 'axios';
import { api, readCookie, setSessionToken, hasAuthFlag } from '@/lib/api';

/** Run the registered request interceptors the way axios does, returning the final config. */
function runRequestInterceptors(config: { method: string; headers: AxiosHeaders }) {
  const handlers = (api.interceptors.request as unknown as {
    handlers: Array<{ fulfilled?: (c: typeof config) => typeof config } | null>;
  }).handlers;
  let c = config;
  for (const h of handlers) if (h?.fulfilled) c = h.fulfilled(c);
  return c;
}

/** Stateful localStorage backed by a Map, with spies on the mutators so tests
 *  can assert exactly what was (and wasn't) written. */
function makeLocalStorage(initial: Record<string, string> = {}) {
  const store = new Map<string, string>(Object.entries(initial));
  return {
    getItem: vi.fn((k: string) => (store.has(k) ? store.get(k)! : null)),
    setItem: vi.fn((k: string, v: string) => { store.set(k, v); }),
    removeItem: vi.fn((k: string) => { store.delete(k); }),
    _store: store,
  };
}

/** A valid-looking `header.payload.signature` JWT (not a real token). */
const FAKE_JWT = 'eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjMifQ.c2lnbmF0dXJl';

describe('readCookie', () => {
  beforeEach(() => vi.stubGlobal('document', { cookie: 'a=1; muddato.csrf=abc.123; b=2' }));
  afterEach(() => vi.unstubAllGlobals());

  it('extracts a cookie value by name', () => {
    expect(readCookie('muddato.csrf')).toBe('abc.123');
  });
  it('returns null for a missing cookie', () => {
    expect(readCookie('nope')).toBeNull();
  });
});

describe('api request interceptor — double-submit CSRF (#257)', () => {
  beforeEach(() => {
    vi.stubGlobal('document', { cookie: 'muddato.csrf=tok-xyz' });
    vi.stubGlobal('localStorage', { getItem: () => null, removeItem: () => {}, setItem: () => {} });
  });
  afterEach(() => vi.unstubAllGlobals());

  it('attaches X-CSRF-Token from the cookie on a POST', () => {
    const out = runRequestInterceptors({ method: 'post', headers: new AxiosHeaders() });
    expect(out.headers.get('X-CSRF-Token')).toBe('tok-xyz');
  });

  it('does NOT attach X-CSRF-Token on a safe GET', () => {
    const out = runRequestInterceptors({ method: 'get', headers: new AxiosHeaders() });
    expect(out.headers.get('X-CSRF-Token')).toBeFalsy();
  });

  it('attaches nothing when no csrf cookie is present', () => {
    vi.stubGlobal('document', { cookie: '' });
    const out = runRequestInterceptors({ method: 'delete', headers: new AxiosHeaders() });
    expect(out.headers.get('X-CSRF-Token')).toBeFalsy();
  });
});

describe('api request interceptor — hybrid auth (cookie + Bearer fallback)', () => {
  afterEach(() => vi.unstubAllGlobals());

  it('attaches Bearer from the stored token (Telegram WebView, where the cookie fails)', () => {
    const ls = makeLocalStorage({ 'muddato.session': FAKE_JWT });
    vi.stubGlobal('document', { cookie: '' });
    vi.stubGlobal('localStorage', ls);

    const out = runRequestInterceptors({ method: 'get', headers: new AxiosHeaders() });

    expect(out.headers.get('Authorization')).toBe(`Bearer ${FAKE_JWT}`);
    // The token is NOT evicted — it is the WebView's credential.
    expect(ls._store.has('muddato.session')).toBe(true);
  });

  it('attaches no Authorization when no token is stored (cookie-only browser)', () => {
    vi.stubGlobal('document', { cookie: '' });
    vi.stubGlobal('localStorage', makeLocalStorage());

    const out = runRequestInterceptors({ method: 'get', headers: new AxiosHeaders() });

    expect(out.headers.get('Authorization')).toBeFalsy();
  });

  it('relies on withCredentials so the HttpOnly cookie also authenticates browsers', () => {
    expect(api.defaults.withCredentials).toBe(true);
  });
});

describe('setSessionToken / hasAuthFlag', () => {
  afterEach(() => vi.unstubAllGlobals());

  it('stores the session JWT (Bearer fallback) and the authed flag on login', () => {
    const ls = makeLocalStorage();
    vi.stubGlobal('localStorage', ls);

    setSessionToken(FAKE_JWT);

    expect(ls.setItem).toHaveBeenCalledWith('muddato.session', FAKE_JWT);
    expect(ls.setItem).toHaveBeenCalledWith('muddato.authed', '1');
    expect(hasAuthFlag()).toBe(true);
  });

  it('clears the flag, token, and persisted state on logout', () => {
    const ls = makeLocalStorage({
      'muddato.authed': '1',
      'muddato.session': FAKE_JWT,
      'muddato.persist': '{"state":{}}',
    });
    vi.stubGlobal('localStorage', ls);

    setSessionToken(null);

    expect(ls.removeItem).toHaveBeenCalledWith('muddato.authed');
    expect(ls.removeItem).toHaveBeenCalledWith('muddato.session');
    expect(ls.removeItem).toHaveBeenCalledWith('muddato.persist');
    expect(hasAuthFlag()).toBe(false);
  });
});
