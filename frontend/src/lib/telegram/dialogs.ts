/**
 * telegramDialogs — native Telegram popups (Bot API 6.2+) for confirmations and
 * alerts, with a clean "not available" signal so callers can fall back to the
 * in-app UI in a browser or on an old client.
 *
 * Critical BNPL actions (cancel a contract, pay now, delete) should read as a
 * deliberate OS-level decision — Telegram's native popup with a proper
 * destructive button does that far better than a custom div. `nativeConfirm`
 * returns `null` when no native popup is usable; the caller then renders its own
 * dialog (see lib/feedback.confirmDialog).
 */
import { tg, isVersionAtLeast } from './adapter';
import { haptics } from './haptics';

interface NativeConfirmOpts {
  title?: string;
  message: string;
  confirmLabel?: string;
  cancelLabel?: string;
  /** Style the confirm button as destructive (red) — for delete/cancel flows. */
  destructive?: boolean;
}

function canPopup(): boolean {
  return !!tg()?.showPopup && isVersionAtLeast('6.2');
}

/**
 * Native confirm. Resolves true/false. Returns `null` (synchronously) when a
 * native popup can't be shown — the caller must fall back to its own UI.
 */
export function nativeConfirm(opts: NativeConfirmOpts): Promise<boolean> | null {
  const wa = tg();
  if (!wa || !canPopup()) return null;
  return new Promise<boolean>((resolve) => {
    let settled = false;
    const done = (v: boolean) => {
      if (settled) return;
      settled = true;
      haptics.selection();
      resolve(v);
    };
    try {
      wa.showPopup!(
        {
          title: opts.title,
          message: opts.message,
          buttons: [
            {
              id: 'ok',
              type: opts.destructive ? 'destructive' : 'default',
              text: opts.confirmLabel ?? 'OK',
            },
            { id: 'cancel', type: 'cancel', text: opts.cancelLabel ?? 'Bekor' },
          ],
        },
        (pressedId: string) => done(pressedId === 'ok'),
      );
    } catch {
      // Telegram closes a popup-conflict with an exception; treat as cancelled
      // rather than leaving the promise dangling.
      done(false);
    }
  });
}

/** Native alert. Resolves when dismissed; `null` when unavailable. */
export function nativeAlert(message: string): Promise<void> | null {
  const wa = tg();
  if (!wa || !wa.showAlert || !isVersionAtLeast('6.2')) return null;
  return new Promise<void>((resolve) => {
    try {
      wa.showAlert!(message, () => resolve());
    } catch {
      resolve();
    }
  });
}
