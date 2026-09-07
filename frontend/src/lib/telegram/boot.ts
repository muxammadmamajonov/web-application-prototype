/**
 * telegramBoot — the single entry point that initialises the Mini App once at
 * app start: theme (native chrome + CSS vars) and viewport (expand, safe-area
 * vars, swipe taming). Mounted from <App> via `useTelegramBoot()`. Inert outside
 * Telegram, so it's safe to mount unconditionally.
 *
 * Auth, navigation (BackButton), bottom buttons, dialogs and deep links are
 * per-screen concerns and live in their own modules — this only does the global,
 * boot-once setup.
 */
import { useEffect } from 'react';
import { isInsideTelegram } from './adapter';
import { initTheme } from './theme';
import { initViewport } from './viewport';

function initTelegram(): () => void {
  if (!isInsideTelegram()) return () => {};
  const offTheme = initTheme();
  const offViewport = initViewport();
  return () => {
    offTheme();
    offViewport();
  };
}

/** React hook: run the global Telegram boot once for the app's lifetime. */
export function useTelegramBoot(): void {
  useEffect(() => initTelegram(), []);
}
