// @vitest-environment jsdom
/**
 * P2-07 — standards regressions in the public marketing shell.
 *
 * The accessible labels added for the touch/a11y pass were written as
 * `lng === 'ru' ? … : …` ternaries. That hardcodes user-visible copy into JSX:
 * it bypasses `uz.json` / `ru.json`, so the strings cannot be reviewed,
 * corrected or kept in parity like every other string in the product.
 *
 * These tests assert the *mechanism*, not just the current wording — each label
 * must follow its locale resource. A ternary that happens to produce the same
 * text today would still fail, because overriding the resource would not move
 * the rendered label.
 *
 * Also pinned: uz/ru key parity across the whole bundle.
 */
import { describe, expect, it, beforeEach, afterEach, vi } from 'vitest';
import { render, screen, cleanup, fireEvent, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router';

// (MUDD-71 H3) These components read the support handle through
// `useSupportUsername`, which resolves it from platform settings via TanStack
// Query — the handle is operator-configurable, so a build-time constant could
// not be the source. That makes a QueryClientProvider part of the environment
// they need, exactly as it is in the real tree (`main.tsx`). `retry: false` so a
// test never waits on a retry schedule for a request nothing here asserts on.
function withQuery(ui: ReactNode): ReactNode {
  return (
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      {ui}
    </QueryClientProvider>
  );
}

import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import type { ReactNode } from 'react';
import i18n from '@/i18n';
import uz from '@/i18n/locales/uz.json';
import ru from '@/i18n/locales/ru.json';

vi.mock('@/components/layout/Logo', () => ({ Logo: () => <span>Muddato</span> }));

import { PublicHeader } from './PublicLayout';

const LOCALES = ['uz', 'ru'] as const;
type Lng = typeof LOCALES[number];

function renderHeader(lng: Lng) {
  return render(withQuery(<MemoryRouter initialEntries={[`/${lng}`]}><PublicHeader lng={lng} /></MemoryRouter>));
}

/** Replace one key in a live bundle; returns a restore function. */
function overrideKey(lng: Lng, path: string, value: string): () => void {
  const original = i18n.getFixedT(lng)(path);
  const [group, leaf] = path.split('.');
  const set = (v: string) =>
    i18n.addResourceBundle(lng, 'translation', { [group]: { [leaf]: v } }, true, true);
  set(value);
  return () => set(original);
}

beforeEach(async () => { cleanup(); await i18n.changeLanguage('uz'); });
afterEach(() => cleanup());

describe.each(LOCALES)('the shell labels come from the %s bundle', (lng) => {
  const t = (k: string) => i18n.getFixedT(lng)(k);

  it('labels the home link with a11y.home', () => {
    renderHeader(lng);
    expect(screen.getByRole('link', { name: t('a11y.home') })).toBeTruthy();
  });

  it('labels the menu trigger with a11y.openMenu', () => {
    renderHeader(lng);
    expect(screen.getByRole('button', { name: t('a11y.openMenu') })).toBeTruthy();
  });

  it('labels the drawer close controls with a11y.closeMenu', () => {
    renderHeader(lng);
    fireEvent.click(screen.getByRole('button', { name: t('a11y.openMenu') }));
    // Both the backdrop and the header X dismiss the drawer; both are labelled.
    expect(screen.getAllByRole('button', { name: t('a11y.closeMenu') }).length).toBe(2);
  });

  it('follows the resource when it changes — not a hardcoded ternary', () => {
    const restore = overrideKey(lng, 'a11y.openMenu', `__OPEN_${lng}__`);
    try {
      renderHeader(lng);
      expect(screen.getByRole('button', { name: `__OPEN_${lng}__` }),
        'the trigger must read a11y.openMenu, not an inline literal').toBeTruthy();
    } finally { restore(); }
  });

  it('does the same for the close label', () => {
    const restore = overrideKey(lng, 'a11y.closeMenu', `__CLOSE_${lng}__`);
    try {
      renderHeader(lng);
      fireEvent.click(screen.getByRole('button', { name: i18n.getFixedT(lng)('a11y.openMenu') }));
      expect(screen.getAllByRole('button', { name: `__CLOSE_${lng}__` }).length).toBe(2);
    } finally { restore(); }
  });
});

describe('the two locales say different things', () => {
  it('uz and ru labels are not the same string', () => {
    for (const key of ['a11y.home', 'a11y.openMenu', 'a11y.closeMenu']) {
      expect(i18n.getFixedT('uz')(key)).not.toBe(i18n.getFixedT('ru')(key));
    }
  });

  it('renders the ru label when lng="ru", regardless of the active language', async () => {
    // The marketing shell is driven by the URL segment; i18n.language only
    // catches up after LocaleToggle's effect. The label must not lag a frame.
    await i18n.changeLanguage('uz');
    renderHeader('ru');
    expect(screen.getByRole('button', { name: i18n.getFixedT('ru')('a11y.openMenu') })).toBeTruthy();
  });
});

describe('locale key parity', () => {
  const flatten = (o: Record<string, unknown>, prefix = ''): string[] =>
    Object.entries(o).flatMap(([k, v]) => {
      const key = prefix ? `${prefix}.${k}` : k;
      return v && typeof v === 'object' && !Array.isArray(v)
        ? flatten(v as Record<string, unknown>, key) : [key];
    });

  const uzKeys = new Set(flatten(uz as Record<string, unknown>));
  const ruKeys = new Set(flatten(ru as Record<string, unknown>));

  it('every uz key exists in ru', () => {
    expect([...uzKeys].filter((k) => !ruKeys.has(k))).toEqual([]);
  });

  it('ru adds only Russian plural categories uz does not have', () => {
    // Uzbek has one plural form; Russian has few/many. Any OTHER ru-only key is
    // a string that was never translated into Uzbek.
    const extra = [...ruKeys].filter((k) => !uzKeys.has(k));
    expect(extra.every((k) => /_(few|many)$/.test(k)), `unexpected ru-only keys: ${extra}`).toBe(true);
  });

  it('has both new a11y labels on both sides', () => {
    for (const key of ['a11y.home', 'a11y.openMenu', 'a11y.closeMenu']) {
      expect(uzKeys.has(key), `uz.json is missing ${key}`).toBe(true);
      expect(ruKeys.has(key), `ru.json is missing ${key}`).toBe(true);
    }
  });
});

// ── C3-06: ALL shared public-shell copy comes from the locale resources ──────

import { PublicFooter } from './PublicLayout';

function renderFooter(lng: Lng) {
  return render(withQuery(<MemoryRouter initialEntries={[`/${lng}`]}><PublicFooter lng={lng} /></MemoryRouter>));
}

/** Every visible string the shared shell owns, by key. */
const SHELL_KEYS = [
  'public.groupProduct', 'public.groupCompany', 'public.groupLegal',
  'public.navPricing', 'public.navNews', 'public.navAbout', 'public.navContact',
  'public.navPartners', 'public.navTerms', 'public.navPrivacy', 'public.navRefund',
  'public.navCookies', 'public.login', 'public.startDemo',
  'public.footerTagline', 'public.rightsReserved', 'public.location',
];

describe.each(LOCALES)('the %s footer renders from the bundle', (lng) => {
  const t = (k: string) => i18n.getFixedT(lng)(k);

  it('shows the tagline, the rights line and the location', () => {
    renderFooter(lng);
    for (const key of ['public.footerTagline', 'public.rightsReserved', 'public.location']) {
      expect(screen.getByText((c) => c.includes(t(key))), `${key} is missing`).toBeTruthy();
    }
  });

  it('shows every link group heading and item label', () => {
    renderFooter(lng);
    for (const key of SHELL_KEYS.filter((k) => k.startsWith('public.group') || k.startsWith('public.nav'))) {
      expect(screen.getAllByText(t(key)).length, `${key} is missing`).toBeGreaterThan(0);
    }
  });

  it('locale-prefixes internal links and leaves external ones alone', () => {
    renderFooter(lng);
    const pricing = screen.getByRole('link', { name: t('public.navPricing') });
    expect(pricing.getAttribute('href')).toBe(`/${lng}/pricing`);
    const news = screen.getByRole('link', { name: t('public.navNews') });
    expect(news.getAttribute('href')).toMatch(/^https:\/\/t\.me\//);
    expect(news.getAttribute('rel')).toContain('noreferrer');
  });
});

describe.each(LOCALES)('the %s header CTAs render from the bundle', (lng) => {
  const t = (k: string) => i18n.getFixedT(lng)(k);

  it('labels sign-in and the demo CTA', () => {
    renderHeader(lng);
    expect(screen.getAllByText(t('public.login')).length).toBeGreaterThan(0);
    expect(screen.getAllByText(t('public.startDemo')).length).toBeGreaterThan(0);
  });

  it('the demo CTA points at the locale-prefixed demo route', () => {
    renderHeader(lng);
    const cta = screen.getAllByRole('link', { name: t('public.startDemo') })[0];
    expect(cta.getAttribute('href')).toBe(`/${lng}/demo`);
  });
});

describe('the shell copy follows the resource, not a hardcoded literal', () => {
  it.each(['public.login', 'public.startDemo'])('%s tracks its key', (key) => {
    const restore = overrideKey('uz', key, '__SENTINEL__');
    try {
      renderHeader('uz');
      expect(screen.getAllByText('__SENTINEL__').length,
        `${key} is not read from the bundle`).toBeGreaterThan(0);
    } finally { restore(); }
  });

  it.each(['public.rightsReserved', 'public.footerTagline'])('%s tracks its key', (key) => {
    const restore = overrideKey('uz', key, '__SENTINEL__');
    try {
      renderFooter('uz');
      expect(screen.getAllByText((c) => c.includes('__SENTINEL__')).length,
        `${key} is not read from the bundle`).toBeGreaterThan(0);
    } finally { restore(); }
  });

  it('the navigation labels track theirs too', () => {
    const restore = overrideKey('uz', 'public.navPricing', '__NAV__');
    try {
      renderFooter('uz');
      expect(screen.getByRole('link', { name: '__NAV__' })).toBeTruthy();
    } finally { restore(); }
  });
});

describe('both locales are complete and distinct', () => {
  it('every shell key exists in uz and ru', () => {
    for (const key of SHELL_KEYS) {
      for (const lng of LOCALES) {
        const v = i18n.getFixedT(lng)(key);
        expect(v, `${key} missing in ${lng}`).not.toBe(key);
        expect(String(v).trim().length, `${key} is empty in ${lng}`).toBeGreaterThan(0);
      }
    }
  });

  it('uz and ru actually differ (nothing was copied across)', () => {
    // "Cookies" would be identical, which is why it is translated in both.
    for (const key of SHELL_KEYS) {
      expect(i18n.getFixedT('uz')(key), `${key} is the same string in both locales`)
        .not.toBe(i18n.getFixedT('ru')(key));
    }
  });

  it('no shared-shell source line still branches on the locale for copy', async () => {
    const { readFileSync } = await import('node:fs');
    const { resolve } = await import('node:path');
    const src = readFileSync(resolve(__dirname, 'PublicLayout.tsx'), 'utf8');
    expect(src).not.toMatch(/lng === 'ru'\s*\?/);
  });
});

// ── F4-03: the mobile drawer's overlay lifecycle ────────────────────────────

const openDrawer = (lng: Lng = 'uz') => {
  renderHeader(lng);
  const trigger = screen.getByRole('button', { name: i18n.getFixedT(lng)('a11y.openMenu') });
  // A real click focuses the button before it fires; jsdom's synthetic event
  // does not, and the trap restores focus to whatever was focused on open. Model
  // the browser so the restoration path is the one users actually take.
  trigger.focus();
  fireEvent.click(trigger);
  return trigger;
};
const drawer = () => screen.getByRole('dialog');

describe('the public mobile drawer behaves like a dialog', () => {
  it('identifies itself as a modal dialog with a name', () => {
    openDrawer();
    const d = drawer();
    expect(d.getAttribute('aria-modal')).toBe('true');
    expect(d.getAttribute('aria-label')).toBe(i18n.t('public.menuTitle'));
  });

  it('moves initial focus into the panel', async () => {
    openDrawer();
    await waitFor(() => {
      expect(drawer().contains(document.activeElement),
        'focus must land inside the drawer, not stay on the page behind').toBe(true);
    });
  });

  it('traps Tab inside the panel', async () => {
    openDrawer();
    await waitFor(() => expect(drawer().contains(document.activeElement)).toBe(true));

    const focusables = Array.from(drawer().querySelectorAll<HTMLElement>(
      'a[href],button:not([disabled]),[tabindex]:not([tabindex="-1"])'));
    const last = focusables[focusables.length - 1];
    last.focus();
    fireEvent.keyDown(window, { key: 'Tab' });
    // Wrapped back to the first control rather than escaping to the document.
    expect(drawer().contains(document.activeElement)).toBe(true);
    expect(document.activeElement).toBe(focusables[0]);
  });

  it('wraps backwards on Shift+Tab too', async () => {
    openDrawer();
    await waitFor(() => expect(drawer().contains(document.activeElement)).toBe(true));
    const focusables = Array.from(drawer().querySelectorAll<HTMLElement>(
      'a[href],button:not([disabled]),[tabindex]:not([tabindex="-1"])'));
    focusables[0].focus();
    fireEvent.keyDown(window, { key: 'Tab', shiftKey: true });
    expect(document.activeElement).toBe(focusables[focusables.length - 1]);
  });

  it('Escape closes it and returns focus to the menu trigger', async () => {
    const trigger = openDrawer();
    await waitFor(() => expect(drawer().contains(document.activeElement)).toBe(true));

    fireEvent.keyDown(window, { key: 'Escape' });
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
    await waitFor(() => expect(document.activeElement).toBe(trigger));
  });

  it('the close button still closes it and restores focus', async () => {
    const trigger = openDrawer();
    fireEvent.click(screen.getAllByRole('button', { name: i18n.t('a11y.closeMenu') })[1]);
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
    await waitFor(() => expect(document.activeElement).toBe(trigger));
  });

  it('the backdrop still closes it', async () => {
    openDrawer();
    fireEvent.click(screen.getAllByRole('button', { name: i18n.t('a11y.closeMenu') })[0]);
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
  });

  it('locks body scroll while open and releases it after', async () => {
    openDrawer();
    expect(document.body.style.overflow).toBe('hidden');
    fireEvent.keyDown(window, { key: 'Escape' });
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
    expect(document.body.style.overflow).not.toBe('hidden');
  });

  it('keeps its route-bound localization', async () => {
    openDrawer('ru');
    expect(drawer().getAttribute('aria-label')).toBe(i18n.getFixedT('ru')('public.menuTitle'));
    expect(screen.getAllByText(i18n.getFixedT('ru')('public.navPricing')).length).toBeGreaterThan(0);
  });

  it('renders no back/close control that would duplicate Telegram chrome', () => {
    openDrawer();
    // The only dismissals are the backdrop and the drawer's own X — no extra
    // "back" chip competing with the native control.
    const closers = screen.getAllByRole('button', { name: i18n.t('a11y.closeMenu') });
    expect(closers).toHaveLength(2);
    expect(screen.queryByRole('button', { name: /back|orqaga|назад/i })).toBeNull();
  });
});
