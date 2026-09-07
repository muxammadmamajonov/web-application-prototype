/**
 * The offered payment methods must be the same list on both sides (#14).
 *
 * The tenant's settings screen decides what an owner can switch on; the backend
 * decides what a customer is offered to pay with. Those are different files in
 * different languages, and the failure they produce is quiet: a toggle for a
 * method no customer will ever see, or a method offered to payers that its
 * owner can no longer configure. Neither throws.
 *
 * Also pinned: the logo-URL bounds, for the same reason — the frontend refuses
 * a value inline and the backend refuses it with a 422, and an owner whose URL
 * passes one and fails the other gets the generic error screen the standard
 * forbids.
 */
import { describe, expect, it } from 'vitest';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import {
  ALL_PAYMENT_METHODS, OFFERED_PAYMENT_METHODS, WITHDRAWN_PAYMENT_METHODS,
  cardIsUsable, isOffered,
} from './paymentMethods';
import { LOGO_URL_MAX, LOGO_URL_MIN, validateLogoUrl } from './logoUrl';

const BACKEND = resolve(__dirname, '../../../backend/app');
const methodsSrc = readFileSync(resolve(BACKEND, 'core/payment_methods.py'), 'utf8');
const validatorsSrc = readFileSync(resolve(BACKEND, 'core/validators.py'), 'utf8');

/** Parse a `NAME: tuple[str, ...] = ("a", "b")` declaration. */
function pyTuple(src: string, name: string): string[] {
  const m = src.match(new RegExp(`^${name}[^=]*= \\(([^)]*)\\)`, 'm'));
  expect(m, `${name} not found in the backend source`).toBeTruthy();
  return [...m![1].matchAll(/"([^"]+)"/g)].map((x) => x[1]);
}

describe('the offered set', () => {
  it('matches the backend exactly', () => {
    expect([...OFFERED_PAYMENT_METHODS]).toEqual(pyTuple(methodsSrc, 'OFFERED'));
  });

  it('draws from the same vocabulary as the backend', () => {
    expect([...ALL_PAYMENT_METHODS]).toEqual(pyTuple(methodsSrc, 'ALL_METHODS'));
  });

  it('is a subset of the vocabulary', () => {
    for (const m of OFFERED_PAYMENT_METHODS) expect(ALL_PAYMENT_METHODS).toContain(m);
  });

  it('leaves the withdrawn methods in the vocabulary, not deleted', () => {
    // Stored settings still reference them; dropping the keys would orphan a
    // tenant's saved Click/Payme credentials.
    expect([...WITHDRAWN_PAYMENT_METHODS].sort()).toEqual(['click', 'payme', 'transfer']);
    for (const m of WITHDRAWN_PAYMENT_METHODS) expect(isOffered(m)).toBe(false);
  });

  it('recognises only what it offers', () => {
    expect(isOffered('cash')).toBe(true);
    expect(isOffered('card')).toBe(true);
    expect(isOffered('nonsense')).toBe(false);
  });
});

describe('card availability agrees with the backend rule', () => {
  it('needs a saved card, not just the toggle', () => {
    expect(cardIsUsable({ card: true, cards: [] })).toBe(false);
    expect(cardIsUsable({ card: true })).toBe(false);
    expect(cardIsUsable({ card: true, cards: [{}] })).toBe(true);
  });

  it('respects the toggle being off', () => {
    expect(cardIsUsable({ card: false, cards: [{}] })).toBe(false);
  });

  it('survives missing settings', () => {
    expect(cardIsUsable(null)).toBe(false);
    expect(cardIsUsable(undefined)).toBe(false);
  });
});

describe('logo URL bounds match the backend', () => {
  it('uses the same minimum and maximum', () => {
    const min = validatorsSrc.match(/^LOGO_URL_MIN = (\d+)/m);
    const max = validatorsSrc.match(/^LOGO_URL_MAX = (\d+)/m);
    expect(min, 'LOGO_URL_MIN not found').toBeTruthy();
    expect(max, 'LOGO_URL_MAX not found').toBeTruthy();
    expect(LOGO_URL_MIN).toBe(Number(min![1]));
    expect(LOGO_URL_MAX).toBe(Number(max![1]));
  });

  it('accepts and rejects the same values', () => {
    expect(validateLogoUrl('https://cdn.example.com/a.png')).toBeNull();
    expect(validateLogoUrl('')).toBeNull();                    // optional
    expect(validateLogoUrl('   ')).toBeNull();
    expect(validateLogoUrl('http://cdn.example.com/a.png')).toBe('bad_scheme');
    expect(validateLogoUrl('javascript:alert(1)')).toBe('bad_scheme');
    expect(validateLogoUrl('data:image/png;base64,AAAA')).toBe('bad_scheme');
    expect(validateLogoUrl('https://localhost/a.png')).toBe('malformed');
    expect(validateLogoUrl('https://a')).toBe('too_short');
    expect(validateLogoUrl(`https://e.com/${'a'.repeat(LOGO_URL_MAX)}`)).toBe('too_long');
  });

  it('never throws, whatever is typed', () => {
    // It runs on every keystroke, so a half-typed URL must not blow up.
    for (const partial of ['h', 'ht', 'https', 'https:', 'https:/', 'https://', '://x', '%%%']) {
      expect(() => validateLogoUrl(partial)).not.toThrow();
    }
  });
});
