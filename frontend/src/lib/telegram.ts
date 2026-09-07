/** Helpers for working with the Telegram Mini App SDK injected into our page. */

interface TelegramWebAppUser {
  id: number;
  first_name?: string;
  last_name?: string;
  username?: string;
  photo_url?: string;
  language_code?: string;
  is_premium?: boolean;
}

interface TelegramWebApp {
  initData: string;
  initDataUnsafe: { user?: TelegramWebAppUser; start_param?: string };
  version: string;
  platform: string;
  colorScheme: 'light' | 'dark';
  themeParams: Record<string, string>;
  isExpanded: boolean;
  viewportHeight: number;
  ready: () => void;
  expand: () => void;
  close: () => void;
  openLink?: (url: string, options?: { try_instant_view?: boolean }) => void;
  openTelegramLink?: (url: string) => void;
  openInvoice?: (url: string, callback?: (status: string) => void) => void;
  HapticFeedback?: { impactOccurred: (style: string) => void };
  MainButton?: { setText: (s: string) => void; show: () => void; hide: () => void; onClick: (cb: () => void) => void };
  BackButton?: {
    isVisible?: boolean;
    show: () => void;
    hide: () => void;
    onClick: (cb: () => void) => void;
    offClick: (cb: () => void) => void;
  };
  // QR / barcode scanner — `callback` returning true closes the popup.
  showScanQrPopup?: (
    params: { text?: string },
    callback?: (data: string) => boolean | void,
  ) => void;
  closeScanQrPopup?: () => void;
  // Some clients deliver the scan result via events instead of the callback.
  onEvent?: (event: string, handler: (payload?: { data?: string }) => void) => void;
  offEvent?: (event: string, handler: (payload?: { data?: string }) => void) => void;
}

declare global {
  interface Window {
    Telegram?: { WebApp?: TelegramWebApp };
    TelegramWebviewProxy?: unknown;
  }
}

/** True if the page is being run as a Mini-App inside Telegram.
 *
 * Signals (in order of trust):
 *  • `tgWebAppData=` in URL fragment/query — present on the very first hit
 *    when Telegram launches us. Most reliable for detecting "we were
 *    actually opened as a Mini-App" vs. "user pasted the URL in a Telegram
 *    chat and tapped it".
 *  • `TelegramWebviewProxy` / `TelegramWebviewProxyProto` — iOS / Android
 *    Mini-App wrappers. Only injected when Telegram opens the page as a
 *    Web App (not when it opens the URL in its in-app browser).
 *  • `Telegram.WebApp.platform` ≠ "unknown" — the SDK sets this only after
 *    parsing real launch parameters.
 *
 * Intentionally NOT used:
 *  • UserAgent containing "Telegram" — matches Telegram Desktop's in-app
 *    browser opening any pasted URL. That browser has NO initData and led
 *    to the "Telegram'dan ma'lumot kelmadi" loop when users opened muddato.uz
 *    via a plain link in a chat. Trusting only initData-bearing signals
 *    keeps that path on the normal browser login flow.
 */
export function isInsideTelegramClient(): boolean {
  if (typeof window === 'undefined') return false;
  if (/[#&?]tgWebAppData=/.test(location.hash + location.search)) return true;
  const w = window as unknown as Record<string, unknown>;
  if (w.TelegramWebviewProxy) return true;
  if (w.TelegramWebviewProxyProto) return true;
  const plat = window.Telegram?.WebApp?.platform;
  if (plat && plat !== 'unknown') return true;
  return false;
}

/** Best-effort detection — returns the WebApp handle once it's been initialised. */
export function getTelegramWebApp(): TelegramWebApp | null {
  const wa = typeof window === 'undefined' ? undefined : window.Telegram?.WebApp;
  if (!wa) return null;
  if (!wa.initData || wa.initData.length < 8) return null;
  return wa;
}

/**
 * Raw WebApp handle WITHOUT the signed-initData gate. Use only for native chrome
 * controls (BackButton / MainButton / header colour) that just need the SDK
 * present — never for anything that trusts the user identity (use
 * getTelegramWebApp() for that).
 */
export function getRawTelegramWebApp(): TelegramWebApp | null {
  return (typeof window === 'undefined' ? undefined : window.Telegram?.WebApp) ?? null;
}

/** Backwards-compatible alias for the public surface. */
export const isInsideTelegram = isInsideTelegramClient;

/**
 * Wait until ``window.Telegram.WebApp.initData`` is populated by the SDK.
 * The SDK script is included in index.html so this typically resolves
 * immediately — but on slower devices we poll for a short window.
 */
export async function waitForInitData(maxMs = 2500): Promise<TelegramWebApp | null> {
  const deadline = Date.now() + maxMs;
  // Telegram exposes ``WebApp.initData`` synchronously once its script runs.
  while (Date.now() < deadline) {
    const wa = getTelegramWebApp();
    if (wa) return wa;
    if (!isInsideTelegramClient() && Date.now() > deadline - maxMs + 200) {
      // No Telegram context at all — short-circuit so browser visitors don't pay.
      return null;
    }
    await new Promise((r) => setTimeout(r, 40));
  }
  return null;
}

export const BOT_USERNAME      = import.meta.env.VITE_TELEGRAM_BOT_USERNAME ?? 'MuddatoBot';
export const SUPPORT_USERNAME  = 'MuddatoSupport';
export const CHANNEL_USERNAME  = 'muddato';

/** Deep-link that opens the bot's chat in the Telegram client (or t.me web fallback). */
function openInTelegramUrl(startParam = 'login'): string {
  return `https://t.me/${BOT_USERNAME}?start=${encodeURIComponent(startParam)}`;
}
export const botDeepLink = openInTelegramUrl;

/**
 * Open an external URL (e.g. a Click/Payme checkout) the right way for the
 * environment. Inside a Mini-App, `window.open(_blank)` is unreliable — it can
 * spawn an orphan tab and break our polling. Telegram's `WebApp.openLink`
 * opens the provider in the in-app browser and returns the user to us.
 */
export function openExternalLink(url: string): void {
  const wa = typeof window === 'undefined' ? undefined : window.Telegram?.WebApp;
  if (wa?.openLink) { wa.openLink(url); return; }
  window.open(url, '_blank', 'noopener');
}

/**
 * Share a link **through Telegram** (forward to a chat) rather than the OS
 * share sheet. `https://t.me/share/url` opens Telegram's "share to chat"
 * picker; inside the Mini-App we route it through `openTelegramLink` (native
 * picker, stays in-app), and in a plain browser we open the same t.me URL
 * which hands off to the Telegram app/web. Returns true if it dispatched.
 */
export function shareViaTelegram(url: string, text?: string): boolean {
  const share = `https://t.me/share/url?url=${encodeURIComponent(url)}${text ? `&text=${encodeURIComponent(text)}` : ''}`;
  const wa = typeof window === 'undefined' ? undefined : window.Telegram?.WebApp;
  if (wa?.openTelegramLink) { wa.openTelegramLink(share); return true; }
  if (wa?.openLink) { wa.openLink(share); return true; }
  if (typeof window !== 'undefined') { window.open(share, '_blank', 'noopener'); return true; }
  return false;
}

/**
 * Share a link the best way the CURRENT environment offers.
 *
 * Order is by how well each mechanism keeps the sharer where they are:
 *
 *   inside Telegram → the native chat picker (`openTelegramLink`), which stays
 *     in-app and is where the recipient already is;
 *   in a browser with the Web Share API → the OS sheet, so the user can pick
 *     WhatsApp, SMS or anything else they actually use;
 *   otherwise → the caller's Copy fallback.
 *
 * Returns false when nothing dispatched, so the caller can fall back rather
 * than leaving a button that silently does nothing. The Web Share promise is
 * awaited because a user who cancels the sheet must not be told it was shared.
 */
export async function shareLink(url: string, text?: string): Promise<boolean> {
  if (isInsideTelegramClient()) return shareViaTelegram(url, text);

  const nav = typeof navigator === 'undefined' ? undefined : navigator;
  if (nav?.share) {
    try {
      await nav.share(text ? { text, url } : { url });
      return true;
    } catch {
      // AbortError (the user dismissed the sheet) is indistinguishable from a
      // real failure here, and both mean "not shared" — so Copy stays offered.
      return false;
    }
  }
  return false;
}

/** True if the Telegram client exposes the native QR scanner. */
export function canScanQr(): boolean {
  return typeof window !== 'undefined' && !!window.Telegram?.WebApp?.showScanQrPopup;
}

/**
 * Open Telegram's native QR/barcode scanner and resolve with the first
 * decoded value. Resolves to `null` if cancelled or unsupported. Strips
 * common URL/whitespace noise so a scanned IMEI is usable directly.
 */
export function scanQr(prompt?: string): Promise<string | null> {
  const wa = window.Telegram?.WebApp;
  if (!wa?.showScanQrPopup) return Promise.resolve(null);
  return new Promise((resolve) => {
    let done = false;
    const finish = (val: string | null) => {
      if (done) return;
      done = true;
      try { wa.closeScanQrPopup?.(); } catch { /* ignore */ }
      if (wa.offEvent) {
        wa.offEvent('qrTextReceived', onText);
        wa.offEvent('scanQrPopupClosed', onClosed);
      }
      resolve(val);
    };
    // Newer clients deliver the result via an event, not the callback.
    const onText = (p?: { data?: string }) => finish((p?.data || '').trim() || null);
    const onClosed = () => finish(null);
    if (wa.onEvent) {
      wa.onEvent('qrTextReceived', onText);
      wa.onEvent('scanQrPopupClosed', onClosed);
    }
    wa.showScanQrPopup!({ text: prompt }, (data: string) => {
      finish((data || '').trim() || null);
      return true; // close popup
    });
  });
}
