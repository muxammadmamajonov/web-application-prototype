// @vitest-environment jsdom
/**
 * COR-01 — exactly one `h1` per rendered document, inside Telegram as well as
 * outside it.
 *
 * `MiniAppHeader` is application chrome: the strip that fills the empty centre
 * of Telegram's own nav bar with the current page title. It is mounted by BOTH
 * shells (`AppShell.tsx:98`, `ClientShell.tsx:37`) and it used to render that
 * title as an `<h1>`. Every routed page also titles itself — `PageHeader`
 * emits `<h1 class="h-page truncate">`, and the customer pages emit their own —
 * so inside the Mini-App every screen in the product served two competing
 * top-level headings for one page.
 *
 * The routed page owns the semantic heading; the chrome does not. So the fix is
 * at the shell, not per-page: `MiniAppHeader`'s title is demoted to a plain
 * element. It is deliberately NOT `role="heading" aria-level=…`, which would
 * keep the duplicate heading semantics under a different tag and fix nothing.
 *
 * This suite covers the operator side (`AppShell` + a real `PageHeader`); the
 * customer side is covered against the real `ClientShell` in
 * `routes/client/installmentDetailHeading.test.tsx`.
 */
import { describe, expect, it, beforeEach, vi } from 'vitest';
import { render, screen, cleanup } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import i18n from '@/i18n';

let INSIDE_TG = false;
vi.mock('@/lib/telegram', async (orig) => ({
  ...(await orig<Record<string, unknown>>()),
  isInsideTelegramClient: () => INSIDE_TG,
}));
vi.mock('@/hooks/useMe', () => ({
  useMe: () => ({ data: { tenants: [] }, isLoading: false, isError: false, error: null }),
}));

import { AppShell } from './AppShell';
import { MiniAppHeader } from './MiniAppHeader';
import { PageHeader } from '@/components/ui/PageHeader';
import { sidebarFor, bottomNavFor, menuPathFor } from '@/routes/_navConfig';

const LOCALES = ['uz', 'ru'] as const;
const h1s = (root: ParentNode) => Array.from(root.querySelectorAll('h1'));

beforeEach(async () => {
  cleanup();
  INSIDE_TG = false;
  await i18n.changeLanguage('uz');
  if (!Element.prototype.scrollIntoView) Element.prototype.scrollIntoView = () => {};
});

/** A representative operator surface: the real shell wrapping a real PageHeader. */
function renderOperatorPage(lng: (typeof LOCALES)[number], title: string) {
  const t = i18n.getFixedT(lng) as never;
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={[`/${lng}/tenant/overview`]}>
        <Routes>
          <Route
            path="/:locale/tenant/*"
            element={
              <AppShell
                sidebarItems={sidebarFor('tenant', lng, t)}
                bottomNavItems={bottomNavFor('tenant', lng, t)}
                menuPath={menuPathFor('tenant', lng)}
              >
                <PageHeader title={title} />
              </AppShell>
            }
          />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe.each(LOCALES)('an operator page inside AppShell in %s', (lng) => {
  beforeEach(async () => { await i18n.changeLanguage(lng); });

  it.each([['outside Telegram', false], ['inside Telegram', true]] as const)(
    'has exactly one h1 in the whole document %s', (_label, tg) => {
      INSIDE_TG = tg;
      const title = i18n.getFixedT(lng)('nav.overview') as string;
      const { container } = renderOperatorPage(lng, title);
      const found = h1s(container);
      expect(found, found.map((e) => `<h1>${e.textContent}</h1>`).join(' + ')).toHaveLength(1);
      // The surviving h1 is the ROUTED page's, not the chrome strip's.
      expect(found[0]!.textContent).toBe(title);
      expect(found[0]!.closest('main'), 'the single h1 must live inside <main>').toBeTruthy();
    });
});

describe('MiniAppHeader is chrome, not a heading', () => {
  const renderHeader = (title = 'Boshqaruv') => {
    INSIDE_TG = true;
    return render(
      <MemoryRouter initialEntries={['/uz/tenant/overview']}>
        <MiniAppHeader navRoots={['/uz/tenant/overview']} title={title} />
      </MemoryRouter>,
    );
  };

  it('exposes no heading role at all', () => {
    renderHeader();
    expect(screen.queryAllByRole('heading')).toHaveLength(0);
  });

  it('does not smuggle the heading back in via role/aria-level', () => {
    // A `role="heading"` swap would leave the duplicate semantics untouched.
    const { container } = renderHeader();
    expect(container.querySelector('[role="heading"]')).toBeNull();
    expect(container.querySelector('[aria-level]')).toBeNull();
  });

  it('still renders the title visibly, with the same type token', () => {
    const { container } = renderHeader();
    const el = Array.from(container.querySelectorAll('*'))
      .find((e) => e.textContent === 'Boshqaruv' && e.children.length === 0);
    expect(el, 'the chrome title is still rendered').toBeTruthy();
    expect(el!.className).toContain('text-section');
    expect(el!.tagName).not.toMatch(/^H[1-6]$/);
  });

  it('is hidden from assistive tech, because it repeats the page h1 verbatim', () => {
    // Both strings come from `routeTitleKeyFor()`, so exposing the chrome copy
    // would announce the same page title twice. The routed <h1> inside <main>
    // stays the single semantic source.
    const { container } = renderHeader();
    expect(container.querySelector('header')!.getAttribute('aria-hidden')).toBe('true');
  });

  it('renders nothing at all outside Telegram', () => {
    INSIDE_TG = false;
    const { container } = render(
      <MemoryRouter initialEntries={['/uz/tenant/overview']}>
        <MiniAppHeader navRoots={['/uz/tenant/overview']} title="Boshqaruv" />
      </MemoryRouter>,
    );
    expect(container.querySelector('header')).toBeNull();
  });
});
