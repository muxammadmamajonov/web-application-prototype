/**
 * telegramAdapter — the ONE place that touches `window.Telegram.WebApp`.
 *
 * Every other Telegram module (theme, viewport, buttons, dialogs, deepLinks,
 * closing, haptics) goes through this adapter instead of reaching into the
 * global directly, so version-gating, null-safety and SSR-safety live in a
 * single audited spot. Outside Telegram every accessor returns a safe default
 * (null / false / no-op), so callers can stay unconditional.
 *
 * Bot API version gating: Telegram injects an OLD client on some devices. Every
 * 6.1+/7.x/8.x/9.x capability is guarded with `isVersionAtLeast()` so calling a
 * method an old client lacks throws inside Telegram (it surfaces a "method
 * unsupported" warning). The raw handle is exported for the rare call the typed
 * surface doesn't cover yet.
 */

export interface TgThemeParams {
  bg_color?: string;
  text_color?: string;
  hint_color?: string;
  link_color?: string;
  button_color?: string;
  button_text_color?: string;
  secondary_bg_color?: string;
  header_bg_color?: string;
  bottom_bar_bg_color?: string;
  accent_text_color?: string;
  section_bg_color?: string;
  section_header_text_color?: string;
  section_separator_color?: string;
  subtitle_text_color?: string;
  destructive_text_color?: string;
}

export interface TgSafeAreaInset {
  top: number;
  bottom: number;
  left: number;
  right: number;
}

export interface TgBottomButton {
  text: string;
  color: string;
  textColor: string;
  isVisible: boolean;
  isActive: boolean;
  isProgressVisible: boolean;
  setText: (t: string) => TgBottomButton;
  onClick: (cb: () => void) => TgBottomButton;
  offClick: (cb: () => void) => TgBottomButton;
  show: () => TgBottomButton;
  hide: () => TgBottomButton;
  enable: () => TgBottomButton;
  disable: () => TgBottomButton;
  showProgress: (leaveActive?: boolean) => TgBottomButton;
  hideProgress: () => TgBottomButton;
  setParams: (p: {
    text?: string;
    color?: string;
    text_color?: string;
    is_active?: boolean;
    is_visible?: boolean;
    has_shine_effect?: boolean;
    position?: 'left' | 'right' | 'top' | 'bottom';
  }) => TgBottomButton;
}

interface TgBackButton {
  isVisible: boolean;
  show: () => TgBackButton;
  hide: () => TgBackButton;
  onClick: (cb: () => void) => TgBackButton;
  offClick: (cb: () => void) => TgBackButton;
}

interface TgSettingsButton {
  isVisible: boolean;
  show: () => TgSettingsButton;
  hide: () => TgSettingsButton;
  onClick: (cb: () => void) => TgSettingsButton;
  offClick: (cb: () => void) => TgSettingsButton;
}

interface TgHapticFeedback {
  impactOccurred: (style: 'light' | 'medium' | 'heavy' | 'rigid' | 'soft') => void;
  notificationOccurred: (type: 'error' | 'success' | 'warning') => void;
  selectionChanged: () => void;
}

interface TgPopupButton {
  id?: string;
  type?: 'default' | 'ok' | 'close' | 'cancel' | 'destructive';
  text?: string;
}

interface TgCloudStorage {
  setItem: (k: string, v: string, cb?: (err: string | null, ok?: boolean) => void) => void;
  getItem: (k: string, cb: (err: string | null, v?: string) => void) => void;
  getItems: (keys: string[], cb: (err: string | null, v?: Record<string, string>) => void) => void;
  removeItem: (k: string, cb?: (err: string | null, ok?: boolean) => void) => void;
  removeItems: (keys: string[], cb?: (err: string | null, ok?: boolean) => void) => void;
  getKeys: (cb: (err: string | null, keys?: string[]) => void) => void;
}

/** The fields/methods we use, typed; everything is optional because older
 *  clients omit later additions. */
interface TgWebApp {
  initData: string;
  initDataUnsafe: {
    user?: { id: number; first_name?: string; last_name?: string; username?: string; language_code?: string; is_premium?: boolean };
    start_param?: string;
    chat_type?: string;
  };
  version: string;
  platform: string;
  colorScheme: 'light' | 'dark';
  themeParams: TgThemeParams;
  isExpanded: boolean;
  viewportHeight: number;
  viewportStableHeight: number;
  isClosingConfirmationEnabled: boolean;
  safeAreaInset?: TgSafeAreaInset;
  contentSafeAreaInset?: TgSafeAreaInset;
  BackButton?: TgBackButton;
  MainButton?: TgBottomButton;
  SecondaryButton?: TgBottomButton;
  SettingsButton?: TgSettingsButton;
  HapticFeedback?: TgHapticFeedback;
  CloudStorage?: TgCloudStorage;

  ready: () => void;
  expand: () => void;
  close: () => void;
  isVersionAtLeast?: (v: string) => boolean;
  setHeaderColor?: (c: string) => void;
  setBackgroundColor?: (c: string) => void;
  setBottomBarColor?: (c: string) => void;
  enableClosingConfirmation?: () => void;
  disableClosingConfirmation?: () => void;
  enableVerticalSwipes?: () => void;
  disableVerticalSwipes?: () => void;
  requestFullscreen?: () => void;
  exitFullscreen?: () => void;
  openLink?: (url: string, options?: { try_instant_view?: boolean }) => void;
  openTelegramLink?: (url: string) => void;
  openInvoice?: (url: string, cb?: (status: string) => void) => void;
  showPopup?: (params: { title?: string; message: string; buttons?: TgPopupButton[] }, cb?: (id: string) => void) => void;
  showAlert?: (message: string, cb?: () => void) => void;
  showConfirm?: (message: string, cb?: (ok: boolean) => void) => void;
  requestContact?: (cb?: (ok: boolean, payload?: unknown) => void) => void;
  requestWriteAccess?: (cb?: (ok: boolean) => void) => void;
  showScanQrPopup?: (params: { text?: string }, cb?: (data: string) => boolean | void) => void;
  closeScanQrPopup?: () => void;
  onEvent: (event: string, handler: (payload?: unknown) => void) => void;
  offEvent: (event: string, handler: (payload?: unknown) => void) => void;
}

// NB: the `Window.Telegram` global is declared once in `@/lib/telegram`; we
// don't re-declare it here (that would conflict). `tg()` casts that handle up to
// our richer `TgWebApp` surface — the SDK injects these methods at runtime; the
// cast just lets the typed wrappers use them with version-gating.

/** Raw handle — null off-window / outside Telegram. Use the typed helpers below
 *  in preference; this is the escape hatch for not-yet-wrapped calls. */
export function tg(): TgWebApp | null {
  if (typeof window === 'undefined') return null;
  return (window.Telegram?.WebApp ?? null) as unknown as TgWebApp | null;
}

/** Inside a Telegram Mini-App? Mirrors the trusted-signal logic used elsewhere:
 *  initData-bearing launch param, native webview proxy, or a real platform. */
export function isInsideTelegram(): boolean {
  if (typeof window === 'undefined') return false;
  if (/[#&?]tgWebAppData=/.test(location.hash + location.search)) return true;
  const w = window as unknown as Record<string, unknown>;
  if (w.TelegramWebviewProxy || w.TelegramWebviewProxyProto) return true;
  const plat = window.Telegram?.WebApp?.platform;
  return !!plat && plat !== 'unknown';
}

/** Parse "8.0" / "7.10" style versions to a comparable [major, minor]. */
function parseVer(v: string): [number, number] {
  const [a, b] = v.split('.');
  return [parseInt(a, 10) || 0, parseInt(b ?? '0', 10) || 0];
}

/** Is the running client at least Bot API version `v`? Uses the SDK's own
 *  `isVersionAtLeast` when present, else compares `version` numerically. */
export function isVersionAtLeast(v: string): boolean {
  const wa = tg();
  if (!wa) return false;
  if (typeof wa.isVersionAtLeast === 'function') {
    try {
      return wa.isVersionAtLeast(v);
    } catch {
      /* fall through to manual compare */
    }
  }
  const [wMaj, wMin] = parseVer(wa.version || '6.0');
  const [tMaj, tMin] = parseVer(v);
  return wMaj > tMaj || (wMaj === tMaj && wMin >= tMin);
}

export function platform(): string {
  return tg()?.platform ?? 'unknown';
}

export function colorScheme(): 'light' | 'dark' {
  return tg()?.colorScheme ?? 'light';
}

/** Subscribe to a Telegram event; returns an unsubscribe fn (no-op outside TG). */
export function onTgEvent(event: string, handler: (payload?: unknown) => void): () => void {
  const wa = tg();
  if (!wa) return () => {};
  wa.onEvent(event, handler);
  return () => wa.offEvent(event, handler);
}
