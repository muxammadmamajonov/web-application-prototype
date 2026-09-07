/**
 * Contract for the Cloudflare Pages security headers (P9-E-003).
 *
 * `frontend/public/_headers` is shipped verbatim to the CDN and had no test at
 * all, so a policy typo could only ever be caught by reading a browser console
 * in production — which is exactly how the `tg://` defect survived.
 *
 * WHAT WENT WRONG. `frame-ancestors` listed `tg://`, intending "let the Telegram
 * client frame us". That is not a CSP source. The CSP3 grammar for a scheme is
 * `scheme-source = scheme-part ":"` — a bare `tg:`, no slashes — so `tg://`
 * parses as a malformed *host*-source and is rejected. Every page load logged
 * `The Content-Security-Policy directive 'frame-ancestors' does not support the
 * source expression 'tg://'`.
 *
 * WHY IT WAS SAFE TO DELETE RATHER THAN CORRECT. The token was inert in both
 * directions. A browser discards only the invalid token and keeps enforcing the
 * remaining sources (verified in a live browser: a cross-origin frame of a
 * document served `frame-ancestors 'self' tg://` is blocked, and Chrome reports
 * the effective policy as `frame-ancestors 'self'`). And it could never have
 * matched anything: a framing ancestor is always a document with an http(s)
 * origin, so no ancestor is ever `tg:`-schemed. Native Telegram clients render
 * a Mini App as a top-level WebView document, where no ancestor check runs.
 *
 * These tests pin the corrected policy and, more importantly, the *class* of
 * mistake, so `scheme://` cannot reappear in any directive.
 */
import { readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { describe, expect, it } from 'vitest';

const HEADERS_FILE = join(dirname(fileURLToPath(import.meta.url)), '..', 'public', '_headers');

/** Parse the Cloudflare `_headers` format into `{ pathPattern: { header: value } }`. */
function parseHeaders(): Record<string, Record<string, string>> {
  const rules: Record<string, Record<string, string>> = {};
  let current: string | null = null;
  for (const raw of readFileSync(HEADERS_FILE, 'utf8').split('\n')) {
    if (!raw.trim() || raw.trim().startsWith('#')) continue;
    if (/^\S/.test(raw)) {
      current = raw.trim();
      rules[current] ??= {};
      continue;
    }
    const idx = raw.indexOf(':');
    if (current === null || idx === -1) continue;
    rules[current][raw.slice(0, idx).trim()] = raw.slice(idx + 1).trim();
  }
  return rules;
}

/** `default-src 'self'; img-src https:` -> `{ 'default-src': ["'self'"], ... }` */
function parseCsp(policy: string): Record<string, string[]> {
  const out: Record<string, string[]> = {};
  for (const part of policy.split(';')) {
    const [name, ...sources] = part.trim().split(/\s+/).filter(Boolean);
    if (name) out[name] = sources;
  }
  return out;
}

const GLOBAL_RULE = '/*';

function globalHeaders(): Record<string, string> {
  const rules = parseHeaders();
  const headers = rules[GLOBAL_RULE];
  expect(headers, `_headers has no '${GLOBAL_RULE}' rule — re-point this test`).toBeDefined();
  return headers;
}

function csp(): Record<string, string[]> {
  const policy = globalHeaders()['Content-Security-Policy'];
  expect(policy, 'no Content-Security-Policy on the global rule').toBeTruthy();
  return parseCsp(policy);
}

describe('CSP source expressions are syntactically valid', () => {
  it('no directive declares a scheme with slashes and no host', () => {
    // The exact defect class: `tg://`, `intent://`, `capacitor://` — a scheme
    // source mistakenly written in URL form. A real scheme source is `tg:`;
    // a real host source has a host after the slashes. This shape is neither,
    // so browsers reject it and the author's intent is silently lost.
    const schemeWithEmptyHost = /^[a-zA-Z][a-zA-Z0-9+\-.]*:\/\/$/;
    const offenders: string[] = [];
    for (const [directive, sources] of Object.entries(csp())) {
      for (const source of sources) {
        if (schemeWithEmptyHost.test(source)) offenders.push(`${directive}: ${source}`);
      }
    }
    expect(
      offenders,
      'a CSP source is written as `scheme://` with no host. Browsers reject it ' +
        'and drop it from the directive, so it protects and permits nothing. ' +
        'Use `scheme:` for a scheme source, or `scheme://host` for a host source.',
    ).toEqual([]);
  });
});

describe('frame-ancestors — who may embed the Mini App', () => {
  it('allows exactly our own origin and the Telegram web clients', () => {
    expect(csp()['frame-ancestors']).toEqual([
      "'self'",
      'https://web.telegram.org',
      'https://*.telegram.org',
    ]);
  });

  it('is never opened to any origin', () => {
    // `*` here would let any site frame the app and run clickjacking against
    // the payment and instalment-approval flows.
    expect(csp()['frame-ancestors']).not.toContain('*');
  });
});

describe('X-Frame-Options is retained as the legacy fallback', () => {
  it('is still sent, and still SAMEORIGIN', () => {
    // This does NOT conflict with frame-ancestors and must not be removed on
    // the theory that it blocks Telegram. CSP2 §7.7.1: when a resource carries
    // both, `frame-ancestors` is enforced and `X-Frame-Options` is ignored —
    // confirmed against a live browser. It only still applies on clients too
    // old to implement frame-ancestors, where it is the one thing standing
    // between those users and a clickjacking frame.
    expect(globalHeaders()['X-Frame-Options']).toBe('SAMEORIGIN');
  });
});

describe('the rest of the hardened header set survives', () => {
  it('keeps the transport, sniffing and referrer protections', () => {
    const headers = globalHeaders();
    expect(headers['X-Content-Type-Options']).toBe('nosniff');
    expect(headers['Referrer-Policy']).toBe('strict-origin-when-cross-origin');
    expect(headers['Strict-Transport-Security']).toContain('max-age=31536000');
  });

  it('keeps object-src and base-uri locked down', () => {
    // `object-src 'none'` kills plugin-based script execution; `base-uri 'self'`
    // stops an injected <base> from re-pointing every relative URL on the page.
    expect(csp()['object-src']).toEqual(["'none'"]);
    expect(csp()['base-uri']).toEqual(["'self'"]);
  });

  it('does not reintroduce unsafe-inline into script-src', () => {
    // (F-28) The single inline bootstrap in index.html is allowed by hash.
    expect(csp()['script-src']).not.toContain("'unsafe-inline'");
  });
});
