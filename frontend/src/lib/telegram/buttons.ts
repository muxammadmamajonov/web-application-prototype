/**
 * telegramButtons — declarative React hooks for Telegram's native bottom
 * buttons (MainButton, Bot API 6.0; SecondaryButton, 7.10+).
 *
 * Pattern: a screen calls `useMainButton({...})` to OWN the singleton button for
 * its lifetime. The hook drives text / visibility / enabled / loading, wires a
 * stable click handler (latest callback via ref — never re-binds), and on
 * unmount hides the button + detaches the handler so the next screen starts
 * clean. Outside Telegram it's an inert no-op and returns `{ isNative: false }`
 * so the component can render its own in-page CTA instead.
 *
 * Loading drives `showProgress` AND disables the button, so a double-tap during
 * an in-flight payment can't fire twice.
 */
import { useEffect, useRef } from 'react';
import { tg, isInsideTelegram, isVersionAtLeast, type TgBottomButton } from './adapter';
import { haptics } from './haptics';

interface BottomButtonOpts {
  text: string;
  onClick: () => void;
  visible?: boolean;
  enabled?: boolean;
  loading?: boolean;
  color?: string;
  textColor?: string;
  /** Fire a haptic on tap (default: light impact). Set false to silence. */
  haptic?: boolean;
}

function driveButton(btn: TgBottomButton | undefined, opts: BottomButtonOpts, handler: () => void): () => void {
  if (!btn) return () => {};
  const visible = opts.visible ?? true;
  const enabled = (opts.enabled ?? true) && !opts.loading;

  btn.setText(opts.text);
  if (opts.color || opts.textColor) {
    btn.setParams({ color: opts.color, text_color: opts.textColor });
  }
  btn.onClick(handler);
  if (visible) btn.show();
  else btn.hide();
  if (enabled) btn.enable();
  else btn.disable();
  if (opts.loading) btn.showProgress(false);
  else btn.hideProgress();

  return () => {
    btn.offClick(handler);
    btn.hideProgress();
    btn.hide();
  };
}

function useBottomButton(which: 'MainButton' | 'SecondaryButton', opts: BottomButtonOpts, minVersion: string) {
  const ref = useRef(opts.onClick);
  ref.current = opts.onClick;
  const loadingRef = useRef(!!opts.loading);
  loadingRef.current = !!opts.loading;

  const isNative = isInsideTelegram() && isVersionAtLeast(minVersion) && !!tg()?.[which];

  // Stable handler: guards against a tap while loading (double-submit defence),
  // fires a haptic, then calls the latest callback.
  const handlerRef = useRef<() => void>(() => {});
  handlerRef.current = () => {
    if (loadingRef.current) return;
    if (opts.haptic !== false) haptics.impact('light');
    ref.current();
  };

  useEffect(() => {
    if (!isNative) return;
    const btn = tg()?.[which];
    const handler = () => handlerRef.current();
    return driveButton(btn, opts, handler);
    // Re-run whenever the visible button state changes.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [isNative, which, opts.text, opts.visible, opts.enabled, opts.loading, opts.color, opts.textColor]);

  return { isNative };
}

/** Drive Telegram's MainButton from a screen. Returns `{ isNative }`: true when
 *  it's driving the real button (so render no in-page CTA), false in a browser. */
export function useMainButton(opts: BottomButtonOpts) {
  return useBottomButton('MainButton', opts, '6.0');
}

/**
 * Show Telegram's native SettingsButton (the ⚙️ in the header, Bot API 7.0+) and
 * route it to `onClick` — a one-tap jump to Profile/Settings from any screen.
 * The latest callback is read via a ref so it never goes stale; hidden on
 * unmount. No-op outside Telegram / on old clients.
 */
export function useSettingsButton(onClick: () => void): void {
  const ref = useRef(onClick);
  ref.current = onClick;
  useEffect(() => {
    const sb = tg()?.SettingsButton;
    if (!sb || !isInsideTelegram() || !isVersionAtLeast('7.0')) return;
    const handler = () => ref.current();
    sb.onClick(handler);
    sb.show();
    return () => {
      sb.offClick(handler);
      sb.hide();
    };
  }, []);
}
