import { describe, it, expect } from 'vitest';
import { luhn, mask, groups, onlyDigits } from './card';

describe('luhn (#204 — reject invalid cards)', () => {
  it('accepts Luhn-valid numbers (UZCARD/HUMO/Visa/MC all follow ISO 7812)', () => {
    expect(luhn('4242424242424242')).toBe(true);
    expect(luhn('4111111111111111')).toBe(true);
    expect(luhn('5555555555554444')).toBe(true);
  });
  it('rejects a single mistyped digit', () => {
    expect(luhn('4242424242424241')).toBe(false);
    expect(luhn('4111111111111112')).toBe(false);
  });
  it('rejects empty / non-numeric input', () => {
    expect(luhn('')).toBe(false);
    expect(luhn('abcd')).toBe(false);
  });
});

describe('mask', () => {
  it('shows first-4 + last-4 with a masked middle', () => {
    expect(mask('9860123456784455')).toBe('9860****4455');
    expect(mask('86001234')).toBe('8600****1234');
  });
  it('falls back to grouping for short input', () => {
    expect(mask('1234')).toBe('1234');
  });
});

describe('groups / onlyDigits', () => {
  it('groups digits in fours', () => {
    expect(groups('1234567890123456')).toBe('1234 5678 9012 3456');
  });
  it('strips non-digits', () => {
    expect(onlyDigits('9860 1234-5678')).toBe('986012345678');
  });
});
