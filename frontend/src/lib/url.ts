/** Sanitize a post-login redirect target to a root-relative in-app path,
 *  blocking open-redirects (#156). Anything that could leave the origin —
 *  protocol-relative (`//host`), backslash tricks (`/\host`, which browsers
 *  normalise to `//host`), absolute URLs, or `scheme:` injections — falls back
 *  to a safe default. */
export function safeInternalPath(next: string | null | undefined, fallback = '/app'): string {
  if (typeof next !== 'string' || next.length === 0) return fallback;
  // Must be a single-slash root-relative path. Reject '//' and '/\\'.
  if (next[0] !== '/' || next[1] === '/' || next[1] === '\\') return fallback;
  return next;
}

/** Origins we are willing to send the browser to via a full-page redirect or
 *  window.open. The backend returns absolute URLs for the Telegram auth flow
 *  (`authorize_url` → oauth.telegram.org) and the demo/bot deep-link
 *  (`telegram_url` → t.me); we additionally trust the app's own origin. We do
 *  NOT trust arbitrary server-supplied URLs (#F-26): if the backend ever had an
 *  open redirect, an off-allowlist origin must be refused client-side. */
const REDIRECT_ORIGIN_ALLOWLIST: ReadonlySet<string> = new Set([
  'https://oauth.telegram.org',
  'https://t.me',
  'https://telegram.me',
]);

/** True if `url` is an absolute http(s) URL whose origin is on the redirect
 *  allowlist (or the app's own origin). Returns false for relative URLs,
 *  unparseable strings, non-http(s) schemes (`javascript:`, `data:`, `tg:`…),
 *  and any other origin. Use before `window.location.href = url` /
 *  `window.open(url)` on a server-supplied destination. */
export function isAllowedRedirectUrl(url: string | null | undefined): boolean {
  if (typeof url !== 'string' || url.length === 0) return false;
  let parsed: URL;
  try {
    parsed = new URL(url);
  } catch {
    return false; // relative or malformed — not an absolute redirect target
  }
  if (parsed.protocol !== 'https:' && parsed.protocol !== 'http:') return false;
  if (REDIRECT_ORIGIN_ALLOWLIST.has(parsed.origin)) return true;
  // Own origin (covers same-origin absolute URLs the backend may return).
  if (typeof window !== 'undefined' && parsed.origin === window.location.origin) return true;
  return false;
}
