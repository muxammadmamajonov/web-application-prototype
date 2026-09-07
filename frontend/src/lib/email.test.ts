import { describe, it, expect } from 'vitest';
import { isValidEmail, nearestEmailDomain, emailSuggestions, EMAIL_DOMAINS } from './email';

describe('lib/email', () => {
  describe('isValidEmail', () => {
    it('accepts normal addresses', () => {
      expect(isValidEmail('a@gmail.com')).toBe(true);
      expect(isValidEmail('sardor.m@umail.uz')).toBe(true);
    });
    it('rejects malformed input', () => {
      expect(isValidEmail('a@gmail')).toBe(false);      // no TLD
      expect(isValidEmail('agmail.com')).toBe(false);   // no @
      expect(isValidEmail('a b@gmail.com')).toBe(false); // space
      expect(isValidEmail('')).toBe(false);
    });
  });

  describe('nearestEmailDomain (typo correction)', () => {
    it('corrects common near-miss typos', () => {
      expect(nearestEmailDomain('gmial.com')).toBe('gmail.com');
      expect(nearestEmailDomain('gmail.con')).toBe('gmail.com');
      expect(nearestEmailDomain('yandx.ru')).toBe('yandex.ru');
    });
    it('returns null for an already-known or far-off domain', () => {
      expect(nearestEmailDomain('gmail.com')).toBeNull();
      expect(nearestEmailDomain('mycompany.uz')).toBeNull();
    });
  });

  describe('emailSuggestions', () => {
    it('returns none before "@" or with an empty local part', () => {
      expect(emailSuggestions('sardor')).toEqual([]);
      expect(emailSuggestions('@gmail.com')).toEqual([]);
    });
    it('lists every domain right after "@"', () => {
      expect(emailSuggestions('a@')).toEqual(EMAIL_DOMAINS.map((d) => `a@${d}`));
    });
    it('prefix-completes a partial domain', () => {
      expect(emailSuggestions('a@gm')).toEqual(['a@gmail.com']);
    });
    it('offers a single typo fix for a full unknown domain', () => {
      expect(emailSuggestions('a@gmial.com')).toEqual(['a@gmail.com']);
    });
    it('returns nothing for an already-complete known domain', () => {
      expect(emailSuggestions('a@gmail.com')).toEqual([]);
    });
  });
});
