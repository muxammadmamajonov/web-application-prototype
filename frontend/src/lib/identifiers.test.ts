import { describe, it, expect } from 'vitest';
import vectors from './identifierVectors.json';
import {
  ERR_ILLEGAL_CHARS, ERR_IMEI_NOT_NUMERIC, ERR_REQUIRED, ERR_TOO_LONG,
  MAX_IDENTIFIER_LENGTH, normalizeSerial, validateIdentifier,
} from './identifiers';

/**
 * The client half of the identifier contract.
 *
 * `identifierVectors.json` is run case-for-case by BOTH this suite and
 * `backend/tests/test_fb006_product_identifier_semantics.py`. A rule only one
 * side enforces is a round-trip that fails after the shop has already typed the
 * value, so the fixture — not either implementation — is the source of truth.
 */

describe('shared vector contract', () => {
  it('runs every case the server runs', () => {
    expect(vectors.cases.length).toBeGreaterThanOrEqual(40);
  });

  for (const c of vectors.cases) {
    it(`${c.name}`, () => {
      const { value, error } = validateIdentifier(c.type, c.input);
      expect({ value, error }).toEqual({ value: c.value, error: c.error });
    });
  }
});

describe('letters are not ASCII', () => {
  // The defect this widening fixes: the product ships in Uzbek and Russian, and
  // an ASCII-only class rejected the contract numbers its own users write.
  it.each([
    'ДОГОВОР-2026/15',
    'ШАРТНОМА-А12',
    'СЕРИЯ_Б-44',
    'договор 17',
    'SHARTNOMA-Oʻ12',   // U+02BB — a modifier LETTER, so no special case needed
    'GʻALLA 2026/7',
    'ABC-123/Д_44',
  ])('accepts %s', (raw) => {
    const { value, error } = validateIdentifier('SERIAL_OR_CONTRACT', raw);
    expect(error).toBeNull();
    expect(value).toBe(raw);
  });

  it('keeps Cyrillic case', () => {
    expect(validateIdentifier('SERIAL_OR_CONTRACT', 'Договор-Аб').value).toBe('Договор-Аб');
  });

  it('composes decomposed letters so one identifier has one spelling', () => {
    const composed = validateIdentifier('SERIAL_OR_CONTRACT', 'Й-7').value;
    const decomposed = validateIdentifier('SERIAL_OR_CONTRACT', 'Й-7').value;
    expect(composed).toBe(decomposed);
    expect(composed).toBe('Й-7');
  });
});

describe('widening admitted letters only', () => {
  it.each(['ДОГОВОР😀', 'ДОГОВОР<b>', 'Договор’15', 'Договор«15»', 'Договор№15', 'A\\B'])(
    'still refuses %s',
    (bad) => {
      expect(validateIdentifier('SERIAL_OR_CONTRACT', bad).error).toBe(ERR_ILLEGAL_CHARS);
    },
  );

  it('still refuses a value that is only invisible characters', () => {
    expect(validateIdentifier('SERIAL_OR_CONTRACT', '​​').error).toBe(ERR_REQUIRED);
  });

  it('did not widen IMEI', () => {
    expect(validateIdentifier('IMEI', '35209900176148Д').error).toBe(ERR_IMEI_NOT_NUMERIC);
  });
});

describe('the length limit', () => {
  it('counts characters, not UTF-16 units', () => {
    // Cyrillic is one codepoint per letter but two bytes; the column and the
    // server both count characters, so `.length` alone would disagree above
    // the BMP and give the two ends different answers for one input.
    expect(validateIdentifier('SERIAL_OR_CONTRACT', 'Д'.repeat(MAX_IDENTIFIER_LENGTH)).error)
      .toBeNull();
    expect(validateIdentifier('SERIAL_OR_CONTRACT', 'Д'.repeat(MAX_IDENTIFIER_LENGTH + 1)).error)
      .toBe(ERR_TOO_LONG);
  });
});

describe('normalisation', () => {
  it('is idempotent', () => {
    for (const raw of ['  ДОГОВОР  2026 ', 'SN​-1']) {
      const once = normalizeSerial(raw);
      expect(normalizeSerial(once)).toBe(once);
    }
  });
});
