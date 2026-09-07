/**
 * Phase 9 M-1 scope guard — live public copy may not promise a deferred trial.
 *
 * Self-serve tenant/trial provisioning is LATER (Phase 2 ONB-001/-002). The
 * backend half of this guard lives in
 * `backend/tests/test_p9m1_self_serve_scope.py`; this half watches the site,
 * because a truthful backend behind a landing page that still advertises "14
 * kunlik bepul demo. Karta shart emas" is the same broken promise to a Beta
 * participant.
 *
 * Two structural checks, deliberately not a snapshot:
 *
 *   1. Which pages are *live* is read out of the router, so adding a new public
 *      route fails this test until the route is classified here. That is the
 *      part that keeps the guard honest as the site grows.
 *   2. The namespaces those pages render are matched against the forbidden
 *      claims, in both locales. Deferred surfaces (`signup.*`, `welcome.*`,
 *      `trial.*`) are exempt on purpose — that copy is retained for V2 behind
 *      the route scope flag and is unreachable.
 */
import { describe, expect, it } from 'vitest';
import { readFileSync } from 'node:fs';
import uz from '@/i18n/locales/uz.json';
import ru from '@/i18n/locales/ru.json';

const ROUTER = readFileSync('src/routes/router.tsx', 'utf-8');

/** i18n namespaces rendered by the publicly reachable marketing pages. */
const LIVE_NAMESPACES = ['landing', 'demo', 'public'] as const;

/**
 * Public content routes that ship reachable, and where their copy lives.
 *
 * The legal pages take their text as inline props rather than through the
 * bundle, so the bundle sweep alone would miss them — and it did: Terms §3 and
 * Refund §1 both stated a 14-day free demo. They are swept from source instead.
 */
const PUBLIC_PAGES: ReadonlyArray<readonly [route: string, source: string | null]> = [
  ['pricing', null],   // re-renders landing.pricing.*
  ['demo', null],
  ['contact', null],
  ['terms', 'src/routes/marketing/legal/TermsPage.tsx'],
  ['privacy', 'src/routes/marketing/legal/PrivacyPage.tsx'],
  ['refund', 'src/routes/marketing/legal/RefundPage.tsx'],
  ['cookies', 'src/routes/marketing/legal/CookiesPage.tsx'],
];

const FORBIDDEN: ReadonlyArray<readonly [string, RegExp]> = [
  ['a fixed free-trial length', /\b14[\s-]*(kun|kunlik|дн(ей|я|евн\w*))/iu],
  ['card not required', /(kart[ai][^.]{0,24}(shart emas|kerak emas|talab qilinmaydi))|((без|не\s+нужн\w*)\s+карт)/iu],
  ['automatic tenant creation', /(avtomatik[^.]{0,24}(tayyor|ochiladi|ishga tush))|(автоматическ\w*[^.]{0,24}(активир|готов|созда))/iu],
  ['instant self-serve setup', /(3\s*daqiqada)|(за\s*3\s*минут)/iu],
];

/** Flatten a bundle subtree to `key → string` so a match can be reported. */
function strings(node: unknown, prefix: string): Array<[string, string]> {
  if (typeof node === 'string') return [[prefix, node]];
  if (node && typeof node === 'object') {
    return Object.entries(node).flatMap(([k, v]) => strings(v, prefix ? `${prefix}.${k}` : k));
  }
  return [];
}

describe('the set of live public pages is known', () => {
  it('registers exactly the classified public content routes', () => {
    // Ungated `/:locale/<page>` registrations that render a real page. A gated
    // one carries the scope flag on the same line; redirects and the persona
    // shells carry no public copy of their own.
    const live = [...ROUTER.matchAll(/^\s*(.*?)<Route path="\/:locale\/(\w+)"\s*element=\{<(\w+)/gm)]
      .filter(([, before]) => !before.includes('V2_SURFACES_ENABLED'))
      .filter(([, , , element]) => !/^(LocaleRedirect|PageLoading|PersonaGuard)/.test(element))
      .map(([, , page]) => page);
    expect(
      [...new Set(live)].sort(),
      'a new public page shipped — classify it in PUBLIC_PAGES before shipping copy',
    ).toEqual(PUBLIC_PAGES.map(([r]) => r).sort());
  });

  it('keeps the self-service signup page behind the scope flag', () => {
    const line = ROUTER.split('\n').find((l) => l.includes('/:locale/signup')) ?? '';
    expect(line, 'the signup route vanished — re-check M-1').toBeTruthy();
    expect(line, 'self-service signup became publicly reachable').toContain('V2_SURFACES_ENABLED');
  });
});

describe.each(['uz', 'ru'] as const)('live public copy in %s', (lng) => {
  const bundle = lng === 'uz' ? uz : ru;

  it.each(FORBIDDEN)('promises no %s', (_label, pattern) => {
    const offenders = LIVE_NAMESPACES.flatMap((ns) =>
      strings((bundle as Record<string, unknown>)[ns], ns),
    ).filter(([, value]) => pattern.test(value));
    expect(offenders.map(([k, v]) => `${k}: ${v}`)).toEqual([]);
  });
});

describe('public pages that carry their copy inline', () => {
  const inline = PUBLIC_PAGES.filter(([, src]) => src !== null) as Array<[string, string]>;

  it.each(inline)('%s states no forbidden claim', (_route, src) => {
    const text = readFileSync(src, 'utf-8');
    const hits = FORBIDDEN.filter(([, pattern]) => pattern.test(text)).map(([label]) => label);
    expect(hits).toEqual([]);
  });
});

describe('the landing page hard-codes no trial figure', () => {
  it('states no trial length in the stats band', () => {
    // The stat tiles pair a literal number in JSX with a translated label, so a
    // promise can live in the component even when both bundles are clean.
    const src = readFileSync('src/routes/marketing/LandingPage.tsx', 'utf-8');
    const stats = src.slice(src.indexOf('function Stats'), src.indexOf('// ── Pricing'));
    expect(stats).not.toMatch(/freeDays|setupMin/);
  });
});
