import { describe, it, expect, vi, afterEach } from 'vitest';
import { safeInternalPath, isAllowedRedirectUrl } from './url';

describe('safeInternalPath (#156 — open-redirect guard)', () => {
  it('keeps safe root-relative paths', () => {
    expect(safeInternalPath('/app')).toBe('/app');
    expect(safeInternalPath('/uz/tenant/overview?x=1')).toBe('/uz/tenant/overview?x=1');
  });

  it('blocks protocol-relative + backslash tricks', () => {
    expect(safeInternalPath('//evil.com')).toBe('/app');
    expect(safeInternalPath('/\\evil.com')).toBe('/app');
  });

  it('blocks absolute URLs + scheme injections', () => {
    expect(safeInternalPath('https://evil.com')).toBe('/app');
    expect(safeInternalPath('javascript:alert(1)')).toBe('/app');
  });

  it('falls back on empty / missing / relative / leading-space', () => {
    expect(safeInternalPath(null)).toBe('/app');
    expect(safeInternalPath('')).toBe('/app');
    expect(safeInternalPath('app/x')).toBe('/app');
    expect(safeInternalPath(' //evil')).toBe('/app');
  });

  it('honours a custom fallback', () => {
    expect(safeInternalPath(null, '/uz')).toBe('/uz');
  });
});

describe('isAllowedRedirectUrl (#F-26 — server redirect origin allowlist)', () => {
  it('allows the Telegram OAuth + deep-link origins', () => {
    expect(isAllowedRedirectUrl('https://oauth.telegram.org/auth?bot_id=1')).toBe(true);
    expect(isAllowedRedirectUrl('https://t.me/MuddatoBot?start=demo')).toBe(true);
    expect(isAllowedRedirectUrl('https://telegram.me/MuddatoBot')).toBe(true);
  });

  it('rejects an off-allowlist origin (open-redirect target)', () => {
    expect(isAllowedRedirectUrl('https://evil.com/auth')).toBe(false);
    // Look-alike host must not pass — exact-origin match only.
    expect(isAllowedRedirectUrl('https://oauth.telegram.org.evil.com/auth')).toBe(false);
    expect(isAllowedRedirectUrl('https://nottelegram.org/auth')).toBe(false);
  });

  it('rejects dangerous / non-http(s) schemes and malformed input', () => {
    expect(isAllowedRedirectUrl('javascript:alert(1)')).toBe(false);
    expect(isAllowedRedirectUrl('data:text/html,<script>1</script>')).toBe(false);
    expect(isAllowedRedirectUrl('tg://resolve?domain=x')).toBe(false);
    expect(isAllowedRedirectUrl('/relative/path')).toBe(false);
    expect(isAllowedRedirectUrl('not a url')).toBe(false);
    expect(isAllowedRedirectUrl('')).toBe(false);
    expect(isAllowedRedirectUrl(null)).toBe(false);
    expect(isAllowedRedirectUrl(undefined)).toBe(false);
  });

  it('allows the app’s own origin (same-origin absolute URL)', () => {
    // The test runner uses the `node` environment (no real `window`), so stub
    // it the way the api tests stub globals. The function guards with
    // `typeof window !== 'undefined'`, so in the browser this works unstubbed.
    vi.stubGlobal('window', { location: { origin: 'https://muddato.uz' } });
    expect(isAllowedRedirectUrl('https://muddato.uz/uz/tenant/overview')).toBe(true);
    // A different origin is still rejected even with a window present.
    expect(isAllowedRedirectUrl('https://evil.com/uz/tenant/overview')).toBe(false);
  });

  afterEach(() => vi.unstubAllGlobals());
});
