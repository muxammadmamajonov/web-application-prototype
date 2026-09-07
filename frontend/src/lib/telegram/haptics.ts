/**
 * telegramHaptics — tactile feedback for key moments (Bot API 6.1+). Every call
 * is a safe no-op outside Telegram or on a client without HapticFeedback, so
 * callers stay unconditional. Use sparingly: confirm a payment, flag an error,
 * tick a selection — not on every tap.
 */
import { tg, isVersionAtLeast } from './adapter';

function haptic() {
  if (!isVersionAtLeast('6.1')) return null;
  return tg()?.HapticFeedback ?? null;
}

export const haptics = {
  impact(style: 'light' | 'medium' | 'heavy' | 'rigid' | 'soft' = 'light') {
    try {
      haptic()?.impactOccurred(style);
    } catch {
      /* unsupported */
    }
  },
  notify(type: 'error' | 'success' | 'warning') {
    try {
      haptic()?.notificationOccurred(type);
    } catch {
      /* unsupported */
    }
  },
  selection() {
    try {
      haptic()?.selectionChanged();
    } catch {
      /* unsupported */
    }
  },
};
