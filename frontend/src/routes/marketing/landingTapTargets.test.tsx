// @vitest-environment jsdom
/**
 * P9-E-004 — the landing CTAs must stay finger-sized on touch devices.
 *
 * WHAT WAS ACTUALLY WRONG. An audit reported "22 of 43 landing controls under
 * 40 px at 360 px". Re-measured under an emulated **coarse pointer** (the only
 * condition under which the project's rule applies — a merely narrowed desktop
 * window is still `pointer: fine`, so `.tap-target` does nothing there), and
 * scored against WCAG 2.5.8 Target Size (Minimum, AA) including its spacing and
 * inline exceptions, the landing page had **zero** hard failures: the 17 px
 * footer links sit on a measured 29–33 px centre-to-centre pitch, which is what
 * the spacing exception asks for.
 *
 * One real gap survived that analysis. `buttonClasses` size `md` is `h-10`
 * (40 px) and its own comment says the coarse-pointer bump to 44 px "is handled
 * per-call" — and the plan-selection CTA never handled it. That CTA is the
 * page's conversion action, so it now carries the shared `tap-target` utility,
 * which applies `min-height: 2.75rem` **only** under `pointer: coarse`.
 *
 * WHY THESE ASSERTIONS ARE CLASS-BASED. jsdom has no layout engine —
 * `getBoundingClientRect()` returns zeroes and media queries never match — so a
 * height assertion here would pass against any markup and prove nothing. The
 * honest unit-level guard is that the control still opts into the shared rule;
 * the actual pixel measurement was done in a real browser and is recorded above.
 */
import { describe, expect, it, beforeEach, afterEach, vi } from 'vitest';
import { render, cleanup } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import i18n from '@/i18n';

vi.mock('@/components/layout/Logo', () => ({ Logo: () => <span>Muddato</span> }));

/** Two plans: an ordinary one (renders the "try" CTA) and the contact-sales
 *  one (renders the "contact" CTA). Both come from the same call site, so both
 *  must carry the bump. */
const PLANS = [
  {
    code: 'start', is_popular: false, is_contact_sales: false,
    price_monthly: 199000, max_active_installments: 50, max_staff: 3, overage: null,
  },
  {
    code: 'enterprise', is_popular: true, is_contact_sales: true,
    price_monthly: null, max_active_installments: null, max_staff: null, overage: null,
  },
];

vi.mock('@/lib/queries', async (orig) => ({
  ...(await orig<Record<string, unknown>>()),
  usePublicPlans: () => ({ data: PLANS, isLoading: false, isError: false, error: null, refetch: vi.fn() }),
}));

import LandingPage from './LandingPage';

const LOCALES = ['uz', 'ru'] as const;
type Lng = typeof LOCALES[number];

function renderLanding(lng: Lng) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={[`/${lng}`]}>
        <Routes><Route path={`/${lng}`} element={<LandingPage />} /></Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

/**
 * The plan-card CTAs only.
 *
 * Scoping by href alone is not enough: `/contact` is also the hero CTA, the
 * closing-band CTA and a footer link, and those are a different (already
 * compliant) size. Each pricing plan renders as an `<article>`, so the card
 * boundary is what distinguishes them.
 */
function planCtas(): HTMLAnchorElement[] {
  return ([...document.querySelectorAll('article a[href]')] as HTMLAnchorElement[])
    .filter((a) => {
      const href = a.getAttribute('href') ?? '';
      return /\?plan=/.test(href) || href.endsWith('/contact');
    });
}

/** Every CTA-shaped link on the page, card or not. */
function allCtas(): HTMLAnchorElement[] {
  return [...document.querySelectorAll('a[href*="/demo"], a[href*="/contact"]')] as HTMLAnchorElement[];
}

afterEach(cleanup);

describe.each(LOCALES)('landing plan CTAs (%s)', (lng) => {
  beforeEach(async () => { await i18n.changeLanguage(lng); });

  it('the plan-selection CTA opts into the coarse-pointer tap target', () => {
    renderLanding(lng);
    const ctas = planCtas();
    // Both fixture plans must produce a CTA — the ordinary one and the
    // contact-sales one — or this guard is only covering half the call site.
    expect(ctas.length, 'no plan CTA rendered — the plan fixture or the card markup moved').toBe(2);

    const withoutBump = ctas
      .filter((a) => !a.className.includes('tap-target'))
      .map((a) => `${a.getAttribute('href')} :: ${a.className}`);
    expect(
      withoutBump,
      'a plan CTA lost `tap-target`. buttonClasses size `md` is h-10 (40px) — the ' +
        'compact DESKTOP height. Without this utility the control stays 40px under a ' +
        'finger too, below the 44px the project standard asks for on coarse pointers.',
    ).toEqual([]);
  });
});

describe('the hero and closing CTAs stay on the large size', () => {
  beforeEach(async () => { await i18n.changeLanguage('uz'); });

  it('uses h-12, so they clear 44px without needing the utility', () => {
    renderLanding('uz');
    // These were measured at 48px in a real browser and are compliant as-is.
    // Pinning the size stops a future "make it consistent" edit from quietly
    // demoting them to `md` (40px) and reintroducing the defect page-wide.
    const large = allCtas().filter((a) => a.className.includes('h-12'));
    expect(large.length, 'the hero/closing CTAs are no longer size `lg`').toBeGreaterThan(0);
  });
});
