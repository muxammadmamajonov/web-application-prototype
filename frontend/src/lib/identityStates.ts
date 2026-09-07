/**
 * The states the SERVER can report about a person's phone, and how to say them.
 *
 * Every value here is produced by the backend. The client renders them; it
 * never invents one, and in particular it never concludes "verified" from
 * Telegram's own consent callback — that callback reports a tap, and the number
 * it refers to arrives at the bot on a completely separate, authenticated path.
 */

export const IDENTITY_STATES = [
  'VERIFIED',
  'VERIFICATION_REQUIRED',
  'UNSUPPORTED_REGION',
  'MALFORMED',
  'PHONE_DOES_NOT_MATCH_INVITATION',
  'RECONCILIATION_REQUIRED',
  'CUSTOMER_LINKED_TO_OTHER_IDENTITY',
  'IDENTITY_ALREADY_LINKED_HERE',
  'PHONE_NOT_VERIFIED',
  'NO_MATCH',
] as const;

export type IdentityState = (typeof IDENTITY_STATES)[number];

/** Client-only states — where the flow is, not what the server concluded. */
export type FlowState =
  | 'idle'
  | 'prompting'      // Telegram's native sheet is open
  | 'waiting'        // shared; the bot update has not landed yet
  | 'cancelled'
  | 'unsupported'
  | 'timeout'
  | 'error';

export type VerificationState = IdentityState | FlowState;

/** i18n key for a state. Never render the code itself. */
export function identityMessageKey(state: VerificationState): string {
  return `identity.state.${state}`;
}

/** Whether a person can act on this themselves, or needs a human. */
export function needsSupport(state: VerificationState): boolean {
  return (
    state === 'RECONCILIATION_REQUIRED'
    || state === 'CUSTOMER_LINKED_TO_OTHER_IDENTITY'
    || state === 'IDENTITY_ALREADY_LINKED_HERE'
  );
}

/** Whether offering "try again" makes sense. */
export function canRetry(state: VerificationState): boolean {
  return (
    state === 'VERIFICATION_REQUIRED'
    || state === 'PHONE_NOT_VERIFIED'
    || state === 'cancelled'
    || state === 'timeout'
    || state === 'error'
    || state === 'MALFORMED'
  );
}

/** Whether the identity is settled and nothing more is being asked. */
export function isSettled(state: VerificationState): boolean {
  return state === 'VERIFIED';
}

/** How long to keep asking the server after Telegram says the contact was sent.
 *  The update is a webhook round-trip, not an instant fact. */
export const WAIT_TIMEOUT_MS = 30_000;
export const POLL_INTERVAL_MS = 1_500;
