// @vitest-environment jsdom
/**
 * QA-003 — the public deferred/removed URL families must redirect to a
 * locale-prefixed KEEP page, not dead-end in a 404.
 *
 * The router declares each of these as a top-level route with the locale baked
 * into the pattern (`/:locale/features`), then redirects with a *relative*
 * target (`<Navigate to="../contact">`). React Router resolves `..` against the
 * ROUTE hierarchy, not the URL: these routes have no parent, so `..` climbs to
 * `/` and the locale segment is dropped. `/uz/features` landed on `/contact`,
 * which matches nothing and falls through to the `*` NotFound — the exact
 * opposite of the router's own comment ("redirect … rather than dead-ending").
 *
 * These are behaviour tests: they mount the real `AppRouter` and read the
 * resolved location, so they fail for the reported reason rather than asserting
 * on the shape of the source. The lazy page bodies are stubbed out — this is a
 * routing contract, and the destination pages have their own tests — which also
 * lets the 404 assertion be exact, because `NotFound` is the one non-lazy page
 * left in the tree.
 */
import { describe, expect, it, afterEach, vi } from 'vitest';
import { render, screen, cleanup, waitFor } from '@testing-library/react';
import { MemoryRouter, useLocation } from 'react-router';
import { useEffect } from 'react';

// Every routed page goes through lazyWithRetry; stubbing it keeps the test to
// the router and away from the pages' own data/chart dependencies.
vi.mock('@/lib/lazyWithRetry', () => ({
  lazyWithRetry: () => function StubPage() { return <div data-testid="page" />; },
}));

import { AppRouter } from './router';
import { V2_SURFACES_ENABLED } from './_scope';

const LOCALES = ['uz', 'ru'] as const;

/**
 * `from` → the KEEP surface the visitor must end on, written without the locale
 * (both are asserted per locale). Several hop twice on purpose: the historical
 * Uzbek slug redirects to its English sibling, which is itself deferred and
 * redirects on to the retained acquisition surface.
 */
const REDIRECTS: ReadonlyArray<readonly [from: string, to: string]> = [
  // Deferred / removed English surfaces (S-04, S-07, S-08, S-18).
  ['features', 'contact'],
  ['careers', 'contact'],
  ['roadmap', 'contact'],
  ['signup', 'demo'],
  // Historical Uzbek slugs (S-13) — all ten.
  ['tariflar', 'pricing'],
  ['imkoniyatlar', 'contact'],          // → features → contact
  ['mahsulot', 'contact'],              // → features → contact
  ['bog-lanish', 'contact'],
  ['vakansiyalar', 'contact'],          // → careers  → contact
  ['yo-l-xaritasi', 'contact'],         // → roadmap  → contact
  ['foydalanish-shartlari', 'terms'],
  ['maxfiylik', 'privacy'],
  ['pul-qaytarish', 'refund'],
  ['cookielar', 'cookies'],
];

/** The locale-less paths the broken relative redirects used to land on. */
const LOCALELESS_TRAPS = [
  '/contact', '/pricing', '/demo', '/features', '/careers', '/roadmap',
  '/terms', '/privacy', '/refund', '/cookies',
];

function Trace({ seen }: { seen: string[] }) {
  const { pathname } = useLocation();
  useEffect(() => {
    if (seen[seen.length - 1] !== pathname) seen.push(pathname);
  }, [pathname, seen]);
  return <span data-testid="here">{pathname}</span>;
}

function visit(path: string): string[] {
  const seen: string[] = [];
  render(
    <MemoryRouter initialEntries={[path]}>
      <AppRouter />
      <Trace seen={seen} />
    </MemoryRouter>,
  );
  return seen;
}

afterEach(cleanup);

describe('public redirect families preserve the locale', () => {
  it('runs with the deferred V2 surfaces disabled, as they ship', () => {
    expect(V2_SURFACES_ENABLED).toBe(false);
  });

  for (const locale of LOCALES) {
    for (const [from, to] of REDIRECTS) {
      it(`/${locale}/${from} → /${locale}/${to}`, async () => {
        const seen = visit(`/${locale}/${from}`);

        await waitFor(() =>
          expect(screen.getByTestId('here').textContent).toBe(`/${locale}/${to}`),
        );

        // Never a dead end: NotFound is the only page left un-stubbed.
        expect(screen.queryByText('404')).toBeNull();

        // Never the locale-less trap the relative `..` used to produce.
        for (const trap of LOCALELESS_TRAPS) {
          expect(seen).not.toContain(trap);
        }

        // Never a loop: at most two hops (alias → English slug → KEEP surface),
        // and the destination is not itself a redirect.
        expect(seen.length).toBeLessThanOrEqual(3);
        expect(seen[seen.length - 1]).toBe(`/${locale}/${to}`);
        expect(new Set(seen).size).toBe(seen.length);
      });
    }
  }

  it('leaves the retained surfaces alone', async () => {
    for (const locale of LOCALES) {
      for (const keep of ['pricing', 'demo', 'contact', 'terms', 'privacy', 'refund', 'cookies']) {
        cleanup();
        visit(`/${locale}/${keep}`);
        await waitFor(() =>
          expect(screen.getByTestId('here').textContent).toBe(`/${locale}/${keep}`),
        );
        expect(screen.queryByText('404')).toBeNull();
      }
    }
  });

  it('still 404s a path that really has no page', async () => {
    visit('/uz/definitely-not-a-page');
    await waitFor(() => expect(screen.getByText('404')).toBeTruthy());
  });
});
