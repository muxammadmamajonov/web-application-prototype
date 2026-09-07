/**
 * telegramClosing — opt-in "are you sure?" when the user swipes the Mini App
 * closed (Bot API 6.2+). Enabled ONLY inside flows where an accidental close
 * loses real work or confuses a payment/contract state (pay, payoff, receipt
 * upload, application). Never global — a confirm on every screen is hostile.
 */
import { useEffect } from 'react';
import { tg, isVersionAtLeast } from './adapter';

/** Enable Telegram's closing confirmation while `active`, restore on cleanup. */
export function useClosingConfirmation(active: boolean): void {
  useEffect(() => {
    const wa = tg();
    if (!wa || !isVersionAtLeast('6.2') || !active) return;
    try {
      wa.enableClosingConfirmation?.();
    } catch {
      return;
    }
    return () => {
      try {
        wa.disableClosingConfirmation?.();
      } catch {
        /* ignore */
      }
    };
  }, [active]);
}
