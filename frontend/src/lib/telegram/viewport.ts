/**
 * telegramViewport — publishes Telegram's safe-area, content-safe-area and
 * viewport metrics as CSS variables, and tames the WebView's swipe/expand
 * behaviour.
 *
 * Why this matters: `env(safe-area-inset-*)` only covers the OS notch. Telegram
 * draws its OWN floating header (Close / ⋮) over the WebView, and the room it
 * needs is `contentSafeAreaInset` (Bot API 8.0+) — invisible to CSS `env()`. We
 * expose both so layout can clear the device notch AND Telegram's chrome on
 * every platform. On clients < 8.0 the vars simply keep their `env()`-based
 * defaults from globals.css.
 *
 * Published vars (all px):
 *   --tg-safe-top/right/bottom/left        device safe area (notch)
 *   --tg-content-top/right/bottom/left     extra inset under Telegram's UI
 *   --tg-inset-top / --tg-inset-bottom     convenience sums (safe + content)
 *   --tg-viewport-height / --tg-viewport-stable-height
 */
import { tg, isVersionAtLeast, onTgEvent, type TgSafeAreaInset } from './adapter';

const px = (n: number | undefined) => `${Math.max(0, Math.round(n ?? 0))}px`;

function setVar(name: string, value: string): void {
  if (typeof document !== 'undefined') document.documentElement.style.setProperty(name, value);
}

function publishSafeAreas(): void {
  const wa = tg();
  if (!wa) return;
  const safe: TgSafeAreaInset | undefined = wa.safeAreaInset;
  const content: TgSafeAreaInset | undefined = wa.contentSafeAreaInset;
  // Only override the env()-based defaults when Telegram actually reports values
  // (8.0+). Older clients leave the CSS defaults intact.
  if (safe) {
    setVar('--tg-safe-top', px(safe.top));
    setVar('--tg-safe-right', px(safe.right));
    setVar('--tg-safe-bottom', px(safe.bottom));
    setVar('--tg-safe-left', px(safe.left));
  }
  if (content) {
    setVar('--tg-content-top', px(content.top));
    setVar('--tg-content-right', px(content.right));
    setVar('--tg-content-bottom', px(content.bottom));
    setVar('--tg-content-left', px(content.left));
  }
  if (safe || content) {
    setVar('--tg-inset-top', px((safe?.top ?? 0) + (content?.top ?? 0)));
    setVar('--tg-inset-bottom', px((safe?.bottom ?? 0) + (content?.bottom ?? 0)));
  }
}

function publishViewport(): void {
  const wa = tg();
  if (!wa) return;
  if (wa.viewportHeight) setVar('--tg-viewport-height', px(wa.viewportHeight));
  if (wa.viewportStableHeight) setVar('--tg-viewport-stable-height', px(wa.viewportStableHeight));
}

/** Boot the viewport: expand to full height, stop the swipe-down-to-close
 *  gesture from fighting in-app scroll, publish metrics, and keep them live.
 *  Returns an unsubscribe fn. No-op outside Telegram. */
export function initViewport(): () => void {
  const wa = tg();
  if (!wa) return () => {};

  try {
    wa.ready();
    wa.expand();
    // 7.7+: the vertical swipe-to-minimise gesture otherwise hijacks scrolls and
    // accidentally closes flows mid-payment. Disable it for a scroll-heavy app.
    if (isVersionAtLeast('7.7')) wa.disableVerticalSwipes?.();
  } catch {
    /* ignore older-client method gaps */
  }

  publishSafeAreas();
  publishViewport();

  const offs = [
    onTgEvent('viewportChanged', publishViewport),
    onTgEvent('safeAreaChanged', publishSafeAreas),
    onTgEvent('contentSafeAreaChanged', publishSafeAreas),
  ];
  return () => offs.forEach((off) => off());
}
