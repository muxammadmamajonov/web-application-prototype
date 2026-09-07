/**
 * Logo URL validation (#14).
 *
 * The field is optional, but when it is filled it ends up in an `<img src>` on
 * every contract PDF and shop page, so an unusable value is worse than a blank
 * one: it renders as a broken image the owner never sees on their own screen.
 *
 * HTTPS ONLY, on purpose. The app is served over HTTPS, so a `http://` logo is
 * blocked as mixed content by every browser this runs in — accepting it would
 * store a value guaranteed not to render. Rejecting it here says so, in the
 * field, instead of leaving the owner to wonder. Other schemes (`javascript:`,
 * `data:`, `file:`) are refused outright rather than filtered later.
 *
 * Mirrors `validate_logo_url` in backend/app/core/validators.py; the bounds are
 * pinned across the two by `logoUrl.contract.test.ts`.
 */

/** Below this a value cannot be a real URL — `https://a.b` is already 11. */
export const LOGO_URL_MIN = 11;
/** Comfortably past any real CDN URL, and short of what would bloat a PDF. */
export const LOGO_URL_MAX = 512;

export type LogoUrlError = 'too_short' | 'too_long' | 'bad_scheme' | 'malformed';

/**
 * `null` when acceptable (including empty — the field is optional), otherwise
 * the reason, for the caller to render inline. Never throws: this runs on every
 * keystroke.
 */
export function validateLogoUrl(raw: string | null | undefined): LogoUrlError | null {
  const v = (raw ?? '').trim();
  if (!v) return null;                       // optional
  if (v.length < LOGO_URL_MIN) return 'too_short';
  if (v.length > LOGO_URL_MAX) return 'too_long';

  let parsed: URL;
  try {
    parsed = new URL(v);
  } catch {
    return 'malformed';
  }
  if (parsed.protocol !== 'https:') return 'bad_scheme';
  // `new URL('https://')` throws, but `https://?x` does not — a URL with no
  // host is still malformed for our purposes.
  if (!parsed.hostname || !parsed.hostname.includes('.')) return 'malformed';
  return null;
}

export const logoUrlIsValid = (raw: string | null | undefined): boolean =>
  validateLogoUrl(raw) === null;
