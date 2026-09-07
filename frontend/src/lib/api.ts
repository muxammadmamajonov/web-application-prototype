/** Axios client preconfigured for the Muddato backend. */
import axios, { AxiosError, type AxiosInstance } from 'axios';
import { loginRedirectUrl } from './loginRedirect';
import { activeTenantIdForRequest, readImpersonation, writeImpersonation } from '@/store/tenantContext';

const BASE = import.meta.env.VITE_API_BASE ?? '/api';

/**
 * Request timeout — sized against a COLD START, not a warm response (#4).
 *
 * This was 20s. The API runs on Fly with `min_machines_running = 0` so the
 * product costs nothing while idle, and a stopped machine takes ~20.4s to
 * accept its first request (measured 2026-08-06: cold 20.36s, warm 1.34s).
 * The client therefore aborted the first request after any idle period by a
 * margin of about a third of a second, and the user saw "Temporary server
 * error — please try again in a few moments". Retrying worked, because the
 * machine was now warm — which is exactly why it read as intermittent and
 * why the generic copy sounded plausible.
 *
 * 45s is deliberately far past the measured cold start rather than just over
 * it: a cold start competes with image pull, migrations and region latency,
 * and a bound that is merely "usually enough" is the bug we are fixing. Warm
 * requests are unaffected — they finish in ~1.3s, so nothing waits longer
 * than it did. `warmApi()` below means a human should rarely meet this at all.
 */
export const REQUEST_TIMEOUT_MS = 45_000;

/**
 * Start the API machine while the human is still reading the first screen (#4).
 *
 * Raising the timeout stops the cold start from FAILING; it does not stop it
 * from being felt. A 20-second wait on "Create contract" is a broken product
 * even when it eventually succeeds. So the moment the app boots we send one
 * unauthenticated, side-effect-free request, and Fly starts the machine while
 * the user is still logging in or navigating. By the time they submit anything
 * the machine is warm and responds in about a second.
 *
 * Deliberately:
 *   - `/api/ping`, which touches no database and requires no session, so it
 *     cannot fail in a way that matters and cannot leak anything. It has to be
 *     under `/api`: that is the only prefix the Cloudflare Pages proxy forwards
 *     to the backend, so `/ready` at the app root is unreachable from this
 *     origin. Verified live — the first call was returning 404;
 *   - fire-and-forget with the error swallowed — this is an optimisation, and a
 *     user offline at boot must not see an error for a request they never made;
 *   - `keepalive` so it survives an immediate navigation;
 *   - NOT retried and NOT periodic: one ping wakes the machine, and a heartbeat
 *     would defeat scale-to-zero and put the idle bill back on.
 */
export function warmApi(): void {
  try {
    void fetch(`${BASE}/ping`, { method: 'GET', credentials: 'omit', keepalive: true })
      .catch(() => { /* best effort — the real request will still succeed */ });
  } catch { /* fetch unavailable (very old WebView) — nothing to do */ }
}

// Hybrid auth. Browsers authenticate by the HttpOnly `muddato.session` cookie
// (Secure; SameSite=Strict) sent via `withCredentials`, with the readable
// `muddato.csrf` double-submit cookie echoed as X-CSRF-Token. The Telegram Mini-App
// WebView, however, does NOT reliably send that cookie (embedded WKWebView / ITP),
// so cookie-only auth (F-02) silently logged WebApp users out — login POST 200,
// then /auth/me 401, retry, rate_limited. We therefore ALSO store the session JWT
// and attach it as a Bearer token (the backend is cookie-first with a Bearer
// fallback). Trade-off: this re-introduces a JS-readable token (the F-02 concern),
// accepted because (a) WebApp login is the primary path and must work, and (b) the
// app has no raw-HTML injection sinks (a build-enforced security test bans them).
export const api: AxiosInstance = axios.create({
  baseURL: BASE,
  timeout: REQUEST_TIMEOUT_MS,
  withCredentials: true, // Send HttpOnly cookie automatically
});

// ── Non-sensitive auth flag ────────────────────────────────────────────────
// We can't read the HttpOnly session cookie from JS, so to know whether to
// *attempt* an authenticated bootstrap (gate `useMe`, the RequireAuth guard)
// we keep a tiny boolean breadcrumb in localStorage. It is NOT a credential —
// it carries no token material, so an XSS reading it gains nothing. The cookie
// remains the sole proof of identity; a stale flag just means we try /auth/me
// once and a 401 cleans it up.
const AUTHED_FLAG = 'muddato.authed';
const TOKEN_KEY = 'muddato.session';

/** Read the stored session JWT, used as a Bearer fallback for clients where the
 *  HttpOnly cookie is unreliable — notably the Telegram Mini-App WebView (see the
 *  request interceptor below). Returns null in a normal browser that authenticates
 *  by cookie alone. */
function getStoredToken(): string | null {
  try { return localStorage.getItem(TOKEN_KEY); } catch { return null; }
}

const CSRF_COOKIE_NAME = 'muddato.csrf';
const CSRF_METHODS = new Set(['post', 'put', 'delete', 'patch']);

/** Read a non-HttpOnly cookie by name — used for the double-submit CSRF token (#257). */
export function readCookie(name: string): string | null {
  const escaped = name.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
  const m = document.cookie.match(new RegExp('(?:^|;\\s*)' + escaped + '=([^;]*)'));
  return m ? decodeURIComponent(m[1]) : null;
}

api.interceptors.request.use((config) => {
  // (login-fix) Attach the session JWT as a Bearer token when one is stored. The
  // Telegram Mini-App WebView does not reliably send our HttpOnly SameSite=Strict
  // cookie, so cookie-only auth (F-02) silently logged WebApp users out (login POST
  // returned 200, then /auth/me 401 → endless re-login → rate_limited). Bearer works
  // in the WebView; regular browsers also send the cookie via withCredentials. The
  // backend accepts either (cookie-first, bearer fallback — security.py:
  // extract_token_from_request). Trade-off vs F-02 noted in the commit.
  const token = getStoredToken();
  if (token) config.headers.set?.('Authorization', `Bearer ${token}`);

  // (#257) Double-submit CSRF: echo the readable `muddato.csrf` cookie back in the
  // X-CSRF-Token header on state-changing requests, so a cookie-authenticated mutation
  // passes the middleware. It's a no-op until the cookie is set, so it's safe to
  // always attach.
  const method = (config.method ?? 'get').toLowerCase();
  if (CSRF_METHODS.has(method)) {
    const csrf = readCookie(CSRF_COOKIE_NAME);
    if (csrf) config.headers.set?.('X-CSRF-Token', csrf);
  }

  // Active tenant context (X-Tenant-Id) — lets owners with multiple shops, and
  // platform super-admins entering a tenant's dashboard, target the right one.
  //
  // Resolved by `store/tenantContext`, not by reading localStorage here. This
  // interceptor used to parse the persisted store itself, which meant the ONE
  // ambient input to every request in the app was taken from a slot nothing
  // validated: a leftover impersonation from a previous session put a possibly
  // deleted tenant's id on every call, and the resulting 404 painted
  // "Topilmadi — o‘chirilgan yoki ko‘chirilgan bo‘lishi mumkin" across every
  // page of the panel at once.
  const tid = activeTenantIdForRequest();
  if (tid) config.headers.set?.('X-Tenant-Id', tid);
  return config;
});

/** Endpoints that authenticate (or de-authenticate) and must never trigger the
 *  re-validation dance below — a 401 from them is already conclusive. */
function isAuthEndpoint(url: string): boolean {
  return url.includes('/auth/me') || url.includes('/auth/telegram') || url.includes('/auth/login');
}

/** Single in-flight `/auth/me` re-validation, shared across every endpoint that
 *  401s at the same time. Prevents a burst of 401s (one per query on a
 *  dashboard) from firing N parallel /me checks or N redirects. */
let revalidating: Promise<boolean> | null = null;

/** Re-check the session against /auth/me exactly once. Resolves `true` if the
 *  session cookie is still valid, `false` if it is now rejected (→ caller logs
 *  out). Any non-401 failure (network blip, 5xx) is treated as "still valid" so
 *  a flaky connection never logs the user out. */
function revalidateSession(): Promise<boolean> {
  if (!revalidating) {
    // Bare axios GET with withCredentials (cookie) + Bearer fallback (WebView).
    const token = getStoredToken();
    revalidating = axios
      .get(`${BASE}/auth/me`, {
        withCredentials: true,
        timeout: REQUEST_TIMEOUT_MS,
        headers: token ? { Authorization: `Bearer ${token}` } : undefined,
      })
      .then(() => true)
      .catch((e: AxiosError) => (e.response?.status === 401 ? false : true))
      .finally(() => { revalidating = null; });
  }
  return revalidating;
}

/** Drop every JS-readable trace of the session — the non-sensitive authed flag
 *  AND the persisted tenant/impersonation state (`muddato.persist`) — so an
 *  expired session never leaks impersonation/active-tenant into the next login
 *  on a shared device (#158). Explicit logout already does this via the store
 *  reset(); this covers the EXPIRY paths (forceLogout, /auth/me 401).
 *  Note: the HttpOnly cookie is cleared by the backend /auth/logout endpoint. */
function dropStoredSession(): void {
  try {
    localStorage.removeItem(AUTHED_FLAG);
    localStorage.removeItem(TOKEN_KEY);
    localStorage.removeItem('muddato.persist');
  } catch { /* storage disabled */ }
}

/** Hard sign-out from the interceptor. Mirrors lib/auth#logout but kept local to
 *  avoid an import cycle (auth.ts already imports from api.ts). Clears the authed
 *  flag + persisted state and bounces to login — unless we're already on an
 *  /auth/ page. The HttpOnly cookie itself is cleared by the backend on /auth/logout. */
function forceLogout(): void {
  dropStoredSession();
  if (typeof window === 'undefined') return;
  // Inside Telegram the Mini App's signed initData lives in the URL fragment,
  // and this is a FULL page load — so a bare assign('/auth/login') threw the
  // credential away and the reboot reported "Telegram sent no data" about a
  // payload Telegram had just sent. loginRedirectUrl carries it across (and
  // returns null when we are already on an /auth/ page).
  const to = loginRedirectUrl(window.location.pathname, window.location.hash);
  if (to) window.location.assign(to);
}

/**
 * Is this failure "the tenant this session is scoped to no longer exists"?
 *
 * DELIBERATELY NARROWER THAN IT LOOKS, because the first version of this check
 * caused a production outage: operators could not open any shop's panel. It also
 * accepted `no_tenant_membership`, which reads like a statement about the tenant
 * but is actually `resolve_tenant`'s catch-all — it is what you get when the
 * request carried NO `X-Tenant-Id` at all. So one scope-less request (a storage
 * failure, a race, a call from a screen that has no tenant) was read as "the
 * shop is gone", and the recovery below threw the operator out of a perfectly
 * healthy shop, every time, forever.
 *
 * Two conditions now, and both are necessary:
 *
 *   1. the code is `tenant_not_found` — the ONE answer that is unambiguously
 *      about the tenant. `resolve_tenant` raises it only after looking the row
 *      up by id and finding nothing.
 *   2. the request actually SENT the scope. If we did not ask about a tenant,
 *      a 404 cannot be an answer about that tenant.
 *
 * Anything else — including every ordinary deleted customer, contract or
 * payment — goes to the page that asked for it, as an error it can render.
 */
function isDeadTenantScope(err: AxiosError, url: string): boolean {
  if (err.response?.status !== 404) return false;
  if (!url.startsWith('/tenant') && !url.startsWith('/staff')) return false;
  const imp = readImpersonation();
  if (!imp) return false;
  // The header this very request carried — not what the store says now.
  const sent = (err.config?.headers as { get?: (k: string) => unknown } | undefined)
    ?.get?.('X-Tenant-Id');
  if (typeof sent !== 'string' || sent !== imp.tenantId) return false;
  const detail = (err.response?.data as { detail?: unknown } | undefined)?.detail;
  const code = typeof detail === 'string'
    ? detail
    : (detail as { error?: string } | undefined)?.error;
  return code === 'tenant_not_found';
}

/** Restart at the app root so the persona resolver re-lands the operator. */
function forceReloadHome(): void {
  if (typeof window === 'undefined') return;
  window.location.replace('/');
}

api.interceptors.response.use(
  (r) => r,
  async (err: AxiosError) => {
    const url = err.config?.url ?? '';
    const is401 = err.response?.status === 401;

    if (isDeadTenantScope(err, url)) {
      // The tenant this session is impersonating no longer resolves — deleted,
      // or the membership withdrawn. Every subsequent request would fail the
      // same way, on every page, because the scope is a header rather than any
      // page's own data. Drop the scope and start again from the operator's own
      // console instead of leaving a broken card on each screen in turn.
      //
      // Belt to the sessionStorage lifetime's braces: that stops the state from
      // OUTLIVING the app session, this recovers from a tenant deleted DURING
      // one, and together they mean no reachable state can pin a dead scope.
      writeImpersonation(null);
      forceReloadHome();
      return Promise.reject(err);
    }

    // A 401 from /auth/me itself is conclusive: the session is invalid, drop the
    // local flag. (We never re-validate the re-validation request — that loops.)
    if (is401 && url.includes('/auth/me')) {
      dropStoredSession();
      return Promise.reject(err);
    }

    // A 401 from any *other* endpoint usually means the session cookie just
    // expired. Re-validate once against /auth/me (the HttpOnly cookie rides
    // along via withCredentials): if it also 401s, the session is truly dead →
    // log out & redirect. If /auth/me still passes, this was an isolated 401
    // (role race, transient) and we surface it in-page as before. The
    // module-level in-flight promise guards against concurrent re-validation
    // when many queries 401 together.
    if (is401 && !isAuthEndpoint(url)) {
      const stillValid = await revalidateSession();
      if (!stillValid) forceLogout();
    }
    return Promise.reject(err);
  },
);

/** Mark/clear the non-sensitive authed breadcrumb. Login paths call this with a
 *  truthy value once the backend has set the HttpOnly session cookie; logout /
 *  expiry call it with `null`. It NEVER writes the JWT to storage — the cookie
 *  is the only credential. (Name kept for call-site stability; the legacy
 *  token argument value is intentionally ignored beyond truthiness.) */
export function setSessionToken(token: string | null): void {
  if (token) {
    try {
      localStorage.setItem(TOKEN_KEY, token);   // Bearer fallback for the WebView
      localStorage.setItem(AUTHED_FLAG, '1');
    } catch { /* storage disabled */ }
  } else {
    dropStoredSession();
  }
}

/** True if we have a local breadcrumb suggesting an authenticated session, so
 *  callers know whether to *attempt* an authenticated bootstrap. This is NOT a
 *  credential and not proof of auth — the server validates the HttpOnly cookie
 *  on /auth/me and a stale flag is cleaned up by the 401 path above. */
export function hasAuthFlag(): boolean {
  try { return localStorage.getItem(AUTHED_FLAG) === '1'; } catch { return false; }
}
