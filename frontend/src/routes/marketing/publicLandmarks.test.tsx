// @vitest-environment jsdom
/**
 * QA-010 / QA-011 (public half) — every public page must expose one `<main
 * id="main">` landmark, a skip link that resolves to it, and exactly one `h1`.
 *
 * Two mechanisms depend on the landmark and neither fails loudly when it is
 * missing: `RouteFocusManager` moves focus with `document.querySelector('main')`
 * on every pathname change, and `SkipLink` targets `#main`. On a page that
 * renders its own shell instead of `PublicShell`, both silently no-op.
 *
 * Each page is mounted at its REAL route pattern. That matters: the landing is
 * routed as the literal `/uz` and `/ru`, so it has no `:locale` param and a
 * shell that reads the locale from `useParams` alone would render Uzbek chrome
 * on the Russian page. The six already-correct surfaces are the regression net
 * for a fix that duplicates or nests a landmark.
 */
import { describe, expect, it, beforeEach, afterEach, vi } from 'vitest';
import { render, screen, cleanup } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import i18n from '@/i18n';

vi.mock('@/components/layout/Logo', () => ({ Logo: () => <span>Muddato</span> }));
vi.mock('@/lib/queries', async (orig) => ({
  ...(await orig<Record<string, unknown>>()),
  usePublicPlans: () => ({ data: [], isLoading: false, isError: false, error: null, refetch: vi.fn() }),
}));

import LandingPage from './LandingPage';
import PricingPage from './PricingPage';
import DemoPage from './DemoPage';
import ContactPage from './ContactPage';
import TermsPage from './legal/TermsPage';
import PrivacyPage from './legal/PrivacyPage';
import RefundPage from './legal/RefundPage';
import CookiesPage from './legal/CookiesPage';

const LOCALES = ['uz', 'ru'] as const;
type Lng = typeof LOCALES[number];

/**
 * The eight public surfaces (S-02…S-18). `slug: null` marks the landing, whose
 * route pattern is the bare locale literal rather than `/:locale/<slug>`.
 */
interface PublicPage { name: string; node: React.ReactNode; slug: string | null }
const PAGES: readonly PublicPage[] = [
  { name: 'LandingPage', node: <LandingPage />, slug: null },
  { name: 'PricingPage', node: <PricingPage />, slug: 'pricing' },
  { name: 'DemoPage', node: <DemoPage />, slug: 'demo' },
  { name: 'ContactPage', node: <ContactPage />, slug: 'contact' },
  { name: 'TermsPage', node: <TermsPage />, slug: 'terms' },
  { name: 'PrivacyPage', node: <PrivacyPage />, slug: 'privacy' },
  { name: 'RefundPage', node: <RefundPage />, slug: 'refund' },
  { name: 'CookiesPage', node: <CookiesPage />, slug: 'cookies' },
];
const CASES = PAGES.map((p) => [p.name, p] as const);

function renderPage(lng: Lng, page: PublicPage) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const pattern = page.slug === null ? `/${lng}` : `/:locale/${page.slug}`;
  const entry = page.slug === null ? `/${lng}` : `/${lng}/${page.slug}`;
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={[entry]}>
        <Routes><Route path={pattern} element={page.node} /></Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

/** Tab-order-relevant controls, in document order. */
const focusables = (root: ParentNode): HTMLElement[] =>
  Array.from(root.querySelectorAll<HTMLElement>(
    'a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])',
  ));

describe.each(LOCALES)('public landmarks in %s', (lng) => {
  beforeEach(async () => { cleanup(); await i18n.changeLanguage(lng); });
  afterEach(() => cleanup());

  it.each(CASES)('%s exposes exactly one <main id="main">', (_name, page) => {
    const { container } = renderPage(lng, page);
    const mains = container.querySelectorAll('main');
    expect(mains.length).toBe(1);
    expect(mains[0]!.getAttribute('id')).toBe('main');
  });

  it.each(CASES)('%s has a skip link that resolves to that landmark', (_name, page) => {
    const { container } = renderPage(lng, page);
    const skip = container.querySelector<HTMLAnchorElement>('a[href="#main"]');
    expect(skip, 'a skip link exists').toBeTruthy();
    // `#main` with no #main element is a dead control, not a skip link.
    expect(container.querySelector('#main')).toBe(container.querySelector('main'));
    expect(skip!.textContent).toBe(i18n.getFixedT(lng)('a11y.skip'));
  });

  it.each(CASES)('%s puts the skip link first in the tab order', (_name, page) => {
    const { container } = renderPage(lng, page);
    expect(focusables(container)[0]?.getAttribute('href')).toBe('#main');
  });

  it.each(CASES)('%s has exactly one h1', (_name, page) => {
    const { container } = renderPage(lng, page);
    expect(container.querySelectorAll('h1').length).toBe(1);
  });

  it.each(CASES)('%s renders its chrome in that locale', (_name, page) => {
    renderPage(lng, page);
    // The shared footer tagline proves the shell resolved the route locale —
    // the landing carries no :locale param, so a params-only shell would fall
    // back to Uzbek here.
    expect(screen.getAllByText(i18n.getFixedT(lng)('public.footerTagline')).length).toBeGreaterThan(0);
  });
});

describe('the pricing page titles itself', () => {
  const pricing = PAGES.find((p) => p.name === 'PricingPage')!;
  const landing = PAGES.find((p) => p.name === 'LandingPage')!;
  beforeEach(() => cleanup());
  afterEach(async () => { cleanup(); await i18n.changeLanguage('uz'); });

  it.each(LOCALES)('names its h1 from the %s bundle', async (lng) => {
    await i18n.changeLanguage(lng);
    const { container } = renderPage(lng, pricing);
    expect(container.querySelector('h1')?.textContent)
      .toBe(i18n.getFixedT(lng)('landing.pricing.title'));
  });

  it('keeps that title a level down when the block is a landing teaser', () => {
    renderPage('uz', landing);
    // The landing already owns its h1 (the hero); the reused pricing block
    // must not introduce a second one.
    expect(screen.getByRole('heading', { level: 2, name: i18n.getFixedT('uz')('landing.pricing.title') }))
      .toBeTruthy();
  });
});
