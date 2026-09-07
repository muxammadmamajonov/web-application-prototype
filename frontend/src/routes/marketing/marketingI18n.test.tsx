// @vitest-environment jsdom
/**
 * F4-04 (rendered half) — the migrated marketing copy must actually come from
 * the bundle, in both locales.
 *
 * The source guard in `i18nBypass.test.ts` proves the private catalogs are gone.
 * This proves the replacement works: overriding a key moves the rendered text,
 * which a re-introduced literal could not do even if it read identically.
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

import { Hero } from './LandingPage';
import DemoPage from './DemoPage';

const LOCALES = ['uz', 'ru'] as const;
type Lng = typeof LOCALES[number];

function renderAt(lng: Lng, node: React.ReactNode) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={[`/${lng}`]}>
        <Routes><Route path="/:locale" element={node} /></Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

/** Replace one nested key in a live bundle; returns a restore function. */
function overrideKey(lng: Lng, path: string, value: string): () => void {
  const original = i18n.getFixedT(lng)(path);
  const set = (v: string) => {
    const parts = path.split('.');
    const obj: Record<string, unknown> = {};
    let node = obj;
    parts.slice(0, -1).forEach((p) => { node[p] = {}; node = node[p] as Record<string, unknown>; });
    node[parts[parts.length - 1]!] = v;
    i18n.addResourceBundle(lng, 'translation', obj, true, true);
  };
  set(value);
  return () => set(original);
}

beforeEach(async () => { cleanup(); await i18n.changeLanguage('uz'); });
afterEach(() => cleanup());

describe.each(LOCALES)('the %s landing hero renders from the bundle', (lng) => {
  const t = (k: string) => i18n.getFixedT(lng)(k);

  it('shows the hero copy for that locale', () => {
    renderAt(lng, <Hero lng={lng} />);
    expect(screen.getByText(t('landing.hero.sub'))).toBeTruthy();
    expect(screen.getAllByText(t('landing.cta.tryDemo')).length).toBeGreaterThan(0);
  });

  it('labels the hero illustration from the bundle', () => {
    renderAt(lng, <Hero lng={lng} />);
    expect(screen.getByAltText(t('landing.ui.heroAlt'))).toBeTruthy();
  });

  it('follows the resource when it changes', () => {
    const restore = overrideKey(lng, 'landing.hero.sub', `__HERO_${lng}__`);
    try {
      renderAt(lng, <Hero lng={lng} />);
      expect(screen.getByText(`__HERO_${lng}__`),
        'the hero still reads a literal, not the bundle').toBeTruthy();
    } finally { restore(); }
  });
});

describe.each(LOCALES)('the %s demo page renders from the bundle', (lng) => {
  const t = (k: string) => i18n.getFixedT(lng)(k);

  it('shows its title and submit label', () => {
    renderAt(lng, <DemoPage />);
    expect(screen.getByText(t('demo.title'))).toBeTruthy();
    expect(screen.getAllByText(t('demo.submit')).length).toBeGreaterThan(0);
  });

  it('interpolates the disclaimer rather than string-replacing it', () => {
    renderAt(lng, <DemoPage />);
    const expected = i18n.getFixedT(lng)('demo.disclaimer', { days: 14 });
    expect(expected).not.toContain('{{days}}');
    expect(screen.getByText(expected)).toBeTruthy();
  });

  it('follows the resource when it changes', () => {
    const restore = overrideKey(lng, 'demo.title', `__DEMO_${lng}__`);
    try {
      renderAt(lng, <DemoPage />);
      expect(screen.getByText(`__DEMO_${lng}__`)).toBeTruthy();
    } finally { restore(); }
  });
});

describe('the two locales genuinely differ', () => {
  it.each(['landing.hero.sub', 'landing.ui.retry', 'demo.title', 'demo.submit'])(
    '%s is not the same string in uz and ru', (key) => {
      expect(i18n.getFixedT('uz')(key)).not.toBe(i18n.getFixedT('ru')(key));
    });

  it('renders the ru text when the route says ru, whatever the active language', async () => {
    await i18n.changeLanguage('uz');
    renderAt('ru', <Hero lng="ru" />);
    expect(screen.getByText(i18n.getFixedT('ru')('landing.hero.sub'))).toBeTruthy();
  });
});
