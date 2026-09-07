/**
 * telegramTheme — keep the native Telegram chrome (header / background / bottom
 * bar) seamless with our app, expose Telegram's themeParams as CSS variables,
 * and track the light/dark colour scheme.
 *
 * Product decision: Muddato has a fixed warm-cream brand palette, so we paint
 * the native chrome to match our surface (no jarring white/dark Telegram bar
 * around a cream app) rather than re-skinning the whole app to Telegram's theme.
 * The `--tg-*` vars + `tg-dark`/`tg-light` html class are still published so
 * individual surfaces can opt into theme-awareness later without more plumbing.
 */
import { tg, isVersionAtLeast, onTgEvent, colorScheme, type TgThemeParams } from './adapter';

/** Brand surface (matches --c-cream in globals.css). Telegram colour setters
 *  accept a #rrggbb string or a themeParams key; we use the literal so the
 *  native chrome matches our cream regardless of the user's Telegram theme. */
const BRAND_BG = '#f7f4ef';

const CSS_VAR: Record<keyof TgThemeParams, string> = {
  bg_color: '--tg-bg-color',
  text_color: '--tg-text-color',
  hint_color: '--tg-hint-color',
  link_color: '--tg-link-color',
  button_color: '--tg-button-color',
  button_text_color: '--tg-button-text-color',
  secondary_bg_color: '--tg-secondary-bg-color',
  header_bg_color: '--tg-header-bg-color',
  bottom_bar_bg_color: '--tg-bottom-bar-bg-color',
  accent_text_color: '--tg-accent-text-color',
  section_bg_color: '--tg-section-bg-color',
  section_header_text_color: '--tg-section-header-text-color',
  section_separator_color: '--tg-section-separator-color',
  subtitle_text_color: '--tg-subtitle-text-color',
  destructive_text_color: '--tg-destructive-text-color',
};

function publishThemeVars(): void {
  const wa = tg();
  if (!wa || typeof document === 'undefined') return;
  const root = document.documentElement;
  const params = wa.themeParams ?? {};
  for (const [key, cssVar] of Object.entries(CSS_VAR)) {
    const v = (params as Record<string, string | undefined>)[key];
    if (v) root.style.setProperty(cssVar, v);
  }
  root.classList.toggle('tg-dark', colorScheme() === 'dark');
  root.classList.toggle('tg-light', colorScheme() !== 'dark');
  root.dataset.tgPlatform = wa.platform || 'unknown';
}

function paintNativeChrome(): void {
  const wa = tg();
  if (!wa) return;
  try {
    if (isVersionAtLeast('6.1')) {
      wa.setHeaderColor?.(BRAND_BG);
      wa.setBackgroundColor?.(BRAND_BG);
    }
    if (isVersionAtLeast('7.10')) wa.setBottomBarColor?.(BRAND_BG);
  } catch {
    /* old client without colour setters — ignore */
  }
}

/** Apply once at boot and re-apply whenever Telegram's theme flips. Returns an
 *  unsubscribe fn. No-op (returns a no-op) outside Telegram. */
export function initTheme(): () => void {
  if (!tg()) return () => {};
  publishThemeVars();
  paintNativeChrome();
  return onTgEvent('themeChanged', () => {
    publishThemeVars();
    paintNativeChrome();
  });
}
