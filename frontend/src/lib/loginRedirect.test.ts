/**
 * The involuntary bounce to /auth/login must not throw away Telegram's initData.
 *
 * THE BUG THIS PINS (found on a real device, 2026-08-08). Pressing the bot's own
 * recommended button — "Boshlash uchun pastdagi tugmani bosing 👇" — produced:
 *
 *     Kirib bo'lmadi · Telegram'dan ma'lumot kelmadi. Botni qaytadan oching.
 *
 * Telegram hands a Mini App its signed payload in the URL FRAGMENT
 * (`#tgWebAppData=…`), which telegram-web-app.js parses once at script load.
 * The sequence was:
 *
 *   1. Telegram opens https://muddato.uz/#tgWebAppData=…
 *   2. A stale `authed` flag in localStorage makes the app call /auth/me
 *   3. That 401s (the cookie had expired) → forceLogout()
 *   4. forceLogout did `location.assign('/auth/login')` — a FULL page load
 *   5. The fragment is gone, so the reboot finds no initData and the user is
 *      told Telegram sent nothing — while Telegram had in fact sent a perfectly
 *      good signed payload moments earlier.
 *
 * The backend log proves step 5: `GET /api/auth/me` appears, and no
 * `POST /auth/telegram/webapp` ever follows. The client never had anything to send.
 *
 * It is invisible in a browser, where there is no fragment to lose, and
 * invisible once any session exists, because then the bounce never happens —
 * which is why it survived to production.
 */
import { describe, expect, it } from 'vitest';
import { loginRedirectUrl } from './loginRedirect';

describe('the login bounce preserves what Telegram sent', () => {
  it('carries the initData fragment across the redirect', () => {
    const url = loginRedirectUrl('/uz/tenant/overview', '#tgWebAppData=query_id%3DAAA&tgWebAppVersion=7.0');
    expect(url).toContain('#tgWebAppData=');
    expect(url.startsWith('/auth/login')).toBe(true);
  });

  it('keeps the whole fragment byte for byte', () => {
    // The payload is HMAC-signed: dropping or re-encoding any part of it makes
    // the signature fail, which is indistinguishable from a forgery.
    const hash = '#tgWebAppData=user%3D%257B%2522id%2522%253A1%257D%26hash%3Dabc&tgWebAppVersion=7.0';
    expect(loginRedirectUrl('/', hash)).toBe(`/auth/login${hash}`);
  });

  it('adds nothing when there is no fragment (ordinary web visitor)', () => {
    expect(loginRedirectUrl('/uz/tenant/overview', '')).toBe('/auth/login');
  });

  it('ignores a fragment that is not Telegram initData', () => {
    // A plain anchor is not a credential and must not be dragged onto the
    // login URL, where it would be a small open-redirect-ish surprise.
    expect(loginRedirectUrl('/', '#section-2')).toBe('/auth/login');
  });

  it('does not bounce when already on an /auth/ page', () => {
    // Guards the pre-existing behaviour: bouncing /auth/login → /auth/login is
    // a reload loop.
    expect(loginRedirectUrl('/auth/login', '#tgWebAppData=x')).toBeNull();
    expect(loginRedirectUrl('/auth/callback', '')).toBeNull();
  });
});
