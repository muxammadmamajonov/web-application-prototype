import { describe, it, expect } from 'vitest';
import {
  nationalDigits, formatUzPhone, isValidUzPhone, isValidUzMobile, uzOperatorCode, phoneOrEmpty,
} from './phone';

describe('lib/phone', () => {
  describe('nationalDigits', () => {
    it('drops +998 / 998 prefixes + non-digits and caps at 9', () => {
      expect(nationalDigits('+998 90 123 45 67')).toBe('901234567');
      expect(nationalDigits('998901234567')).toBe('901234567');
      expect(nationalDigits('901234567')).toBe('901234567');
      expect(nationalDigits('+998 90')).toBe('90');
      expect(nationalDigits('letters only')).toBe('');
      expect(nationalDigits('9012345678901')).toBe('901234567');
    });
  });

  describe('formatUzPhone', () => {
    it('always keeps the +998 prefix and groups 2-3-2-2', () => {
      expect(formatUzPhone('')).toBe('+998');
      expect(formatUzPhone('+')).toBe('+998');
      expect(formatUzPhone('9')).toBe('+998 9');
      expect(formatUzPhone('9012')).toBe('+998 90 12');
      expect(formatUzPhone('901234567')).toBe('+998 90 123 45 67');
      expect(formatUzPhone('998901234567')).toBe('+998 90 123 45 67');
      expect(formatUzPhone('+998 90 123 45 67')).toBe('+998 90 123 45 67');
    });
    it('strips letters/symbols and caps overflow digits', () => {
      expect(formatUzPhone('90abc1234567xyz')).toBe('+998 90 123 45 67');
    });
  });

  describe('isValidUzPhone (format only)', () => {
    it('is true only for a complete 9-digit number', () => {
      expect(isValidUzPhone('+998 90 123 45 67')).toBe(true);
      expect(isValidUzPhone('+998 71 123 45 67')).toBe(true); // landline passes format
      expect(isValidUzPhone('+998 90 123 45 6')).toBe(false);
      expect(isValidUzPhone('+998 90')).toBe(false);
      expect(isValidUzPhone('')).toBe(false);
    });
  });

  describe('uzOperatorCode', () => {
    it('returns the 2-digit operator code (or "" when absent)', () => {
      expect(uzOperatorCode('+998 90 123 45 67')).toBe('90');
      expect(uzOperatorCode('+998 9')).toBe('9');
      expect(uzOperatorCode('+998')).toBe('');
    });
  });

  describe('isValidUzMobile (format + recognised mobile code)', () => {
    it('is true for every known mobile operator code', () => {
      for (const code of ['20', '33', '50', '55', '77', '88', '90', '91', '93', '94', '95', '97', '98', '99']) {
        expect(isValidUzMobile(`+998 ${code} 123 45 67`)).toBe(true);
      }
    });
    it('is false for a landline area code (71) and unknown codes', () => {
      expect(isValidUzMobile('+998 71 123 45 67')).toBe(false);
      expect(isValidUzMobile('+998 10 123 45 67')).toBe(false);
    });
    it('is false when the number is incomplete', () => {
      expect(isValidUzMobile('+998 90 123 45 6')).toBe(false);
    });
  });

  describe('phoneOrEmpty', () => {
    it('returns "" for a prefix-only field, formatted otherwise', () => {
      expect(phoneOrEmpty('+998 ')).toBe('');
      expect(phoneOrEmpty('+998')).toBe('');
      expect(phoneOrEmpty('+998 90 123 45 67')).toBe('+998 90 123 45 67');
    });
  });
});
