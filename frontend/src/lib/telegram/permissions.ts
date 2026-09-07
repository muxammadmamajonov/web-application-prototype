/**
 * telegramPermissions — Telegram permission prompts (Bot API 6.9+).
 *
 * - `requestWriteAccessOnce()`: ask (once per device) for the bot's permission to
 *   message the user, so BNPL reminders / status updates actually deliver. Asked
 *   once — a repeated prompt every launch would be hostile.
 * - `requestContact()`: ask for the user's phone via Telegram to cut phone-verify
 *   friction. The server must STILL validate the contact↔account mapping (the
 *   client value is not trusted on its own).
 */
import { tg, isVersionAtLeast } from './adapter';

const WRITE_ASKED = 'muddato.tg.writeAccessAsked';

export function requestWriteAccessOnce(): void {
  const wa = tg();
  if (!wa?.requestWriteAccess || !isVersionAtLeast('6.9')) return;
  try {
    if (localStorage.getItem(WRITE_ASKED)) return;
    localStorage.setItem(WRITE_ASKED, '1');
  } catch {
    /* storage blocked — fall through and still ask once this session */
  }
  try {
    wa.requestWriteAccess();
  } catch {
    /* unsupported */
  }
}

/**
 * What Telegram's native consent prompt told us — and no more than that.
 *
 * `sent` means the person tapped "Share", NOT that a number has been verified.
 * The number itself never reaches this callback: it travels to the bot as an
 * authenticated update and is checked there against the sender. The caller's
 * next move is always to ask the server what it now believes.
 */
export type ContactPromptResult = 'sent' | 'cancelled' | 'unsupported';

/** Ask Telegram to share the person's own phone number.
 *
 *  Must be called from a user gesture — Telegram ignores the request otherwise.
 *  The server still decides what, if anything, was proved. */
export function requestContact(): Promise<ContactPromptResult> {
  const wa = tg();
  if (!wa?.requestContact || !isVersionAtLeast('6.9')) return Promise.resolve('unsupported');
  const req = wa.requestContact;
  return new Promise((resolve) => {
    let settled = false;
    const done = (r: ContactPromptResult) => { if (!settled) { settled = true; resolve(r); } };
    try {
      req((ok) => done(ok ? 'sent' : 'cancelled'));
    } catch {
      done('unsupported');
    }
  });
}
