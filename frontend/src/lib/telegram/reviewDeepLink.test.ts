/**
 * A receipt-review deep link has to reach the resolver — and nothing else may.
 *
 * The `startapp` grammar already carried three shapes: a 6-character invite
 * code, a 32-character invitation credential, and `<kind>_<id>`. A review
 * intent is a fourth, and the danger is not that it fails to resolve — it is
 * that it resolves as one of the others. Sending an intent to `/i/` would try
 * to REDEEM it as an invitation, which is a different screen doing a different
 * and irreversible thing.
 *
 * So these tests are mostly about the boundaries between the shapes.
 */
import { describe, expect, it } from 'vitest';
import { startParamToPath } from './deepLinks';

/** 43 chars of base64url — the exact shape the backend mints. */
const INTENT = 'cHJYIlW2CmFCWrTaYrD-qi4waoLToxlY8d723PApDmQ';

describe('review intent deep links', () => {
  it('routes a review intent to the resolver', () => {
    expect(startParamToPath(INTENT, 'uz')).toBe(`/r/${INTENT}`);
  });

  it('is locale-independent — the resolver picks the language', () => {
    // The destination is decided by the SERVER's answer, so the intent path
    // carries no locale of its own.
    expect(startParamToPath(INTENT, 'ru')).toBe(`/r/${INTENT}`);
  });

  it('passes the token through byte for byte', () => {
    // It is an HMAC payload. Upper-casing it — which the legacy 6-character
    // code path does — would change the signature and resolve to nothing.
    const mixed = 'aB-_0123456789aB-_0123456789aB-_0123456789x';
    expect(mixed).toHaveLength(43);
    expect(startParamToPath(mixed, 'uz')).toBe(`/r/${mixed}`);
  });

  it('does not swallow a 32-character invitation credential', () => {
    // The boundary that matters most: an invitation is REDEEMED when opened.
    const credential = 'A'.repeat(32);
    expect(startParamToPath(credential, 'uz')).toBe(`/i/${credential}`);
  });

  it('does not swallow a 6-character invite code', () => {
    expect(startParamToPath('A7K3QX', 'uz')).toBe('/i/A7K3QX');
  });

  it('does not swallow a kinded link', () => {
    expect(startParamToPath('pay_abc123', 'uz')).toBe('/uz/me/installments/abc123/pay');
  });

  it('a kinded link that happens to be 43 characters stays kinded', () => {
    // `pay_` + 39 characters. The kind prefix wins, so a long id cannot be
    // mistaken for an intent.
    const raw = `pay_${'a'.repeat(39)}`;
    expect(raw).toHaveLength(43);
    expect(startParamToPath(raw, 'uz')).toBe(`/uz/me/installments/${'a'.repeat(39)}/pay`);
  });

  it.each([
    ['too short', 'A'.repeat(42)],
    ['too long', 'A'.repeat(44)],
    ['illegal character', `${'A'.repeat(42)}!`],
    ['empty', ''],
  ])('rejects a token that is not intent-shaped: %s', (_label, raw) => {
    const out = startParamToPath(raw, 'uz');
    expect(out === null || !out.startsWith('/r/')).toBe(true);
  });

  it('never routes an intent into the customer space', () => {
    // A review is a SHOP screen. Landing a merchant intent in `/me` would show
    // the wrong console entirely.
    expect(startParamToPath(INTENT, 'uz')).not.toContain('/me');
  });
});
