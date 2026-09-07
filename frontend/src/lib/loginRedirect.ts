/** Where an involuntary sign-out should send the browser.
 *
 *  Split out of `api.ts` so it can be tested without an axios interceptor and a
 *  fake `window` — the bug it exists to prevent was a one-line redirect nobody
 *  could reach from a test.
 */

/** Telegram puts the Mini App's signed payload in these fragment keys. */
const TG_FRAGMENT = /(^|&)tgWebApp(Data|Version|Platform|ThemeParams)=/;

/**
 * The URL to bounce to on a 401, or `null` when we should not bounce at all.
 *
 * Carries the URL FRAGMENT across when it is Telegram's initData. Telegram
 * hands a Mini App its signed payload in `#tgWebAppData=…` and
 * telegram-web-app.js parses it once, at script load — so a full page
 * navigation that drops the fragment destroys the only credential the app has.
 * That is exactly what happened in production: a stale `authed` flag triggered
 * a /auth/me call, the 401 bounced the page, the fragment went with it, and the
 * user was told "Telegram sent no data" about a payload Telegram had sent
 * perfectly well.
 *
 * Only a Telegram fragment is carried. An ordinary anchor (`#pricing`) is not a
 * credential and has no business being reattached to the login URL.
 */
export function loginRedirectUrl(pathname: string, hash: string): string | null {
  // Already on an auth page — bouncing again is a reload loop.
  if (pathname.startsWith('/auth/')) return null;
  const fragment = hash.startsWith('#') ? hash.slice(1) : hash;
  const keep = fragment && TG_FRAGMENT.test(`&${fragment}`);
  return keep ? `/auth/login#${fragment}` : '/auth/login';
}
