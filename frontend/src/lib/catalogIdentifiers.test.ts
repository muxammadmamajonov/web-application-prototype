import { describe, expect, it } from 'vitest';
import vectors from './catalogIdentifierVectors.json';
import {
  ERR_IMEI_CHECKSUM, ERR_IMEI_LENGTH, ERR_IMEI_NOT_NUMERIC, ERR_INVENTORY_FORMAT,
  ERR_MAC_FORMAT, ERR_PLATE_FORMAT, ERR_REQUIRED, ERR_VIN_CHARSET, ERR_VIN_LENGTH,
  validateCatalogIdentifier,
} from './catalogIdentifiers';

/**
 * The client half of the MUDD-113 per-field `validator` contract.
 *
 * `catalogIdentifierVectors.json` is run case-for-case by BOTH this suite and
 * `backend/tests/test_mudd113_catalog_identifiers.py` — a rule only one side
 * enforces is a round-trip that fails after the shop has already typed the
 * value, so the fixture — not either implementation — is the source of truth
 * (same pattern as `identifiers.test.ts` / `identifierVectors.json`). This
 * only catches drift on cases actually enumerated in the fixture (28 today,
 * see the `toBeGreaterThanOrEqual` below): a validator rule added to one side
 * without a matching vector added here passes both suites silently.
 */

describe('shared vector contract', () => {
  it('runs every case the server runs', () => {
    expect(vectors.cases.length).toBeGreaterThanOrEqual(20);
  });

  for (const c of vectors.cases) {
    it(c.name, () => {
      expect(validateCatalogIdentifier(c.field, c.input)).toEqual({ value: c.value, error: c.error });
    });
  }
});
const VALID_IMEI = '352099001761481'; // correct GSMA Luhn check digit
const VALID_VIN = '1HGCM82633A004352'; // 17 chars, no I/O/Q

const IMEI_FIELD = { key: 'imei1', required: true, validator: 'imei', min_len: 15, max_len: 15 };
const VIN_FIELD = { key: 'vin', required: true, validator: 'vin', min_len: 17, max_len: 17 };
const INVENTORY_FIELD = { key: 'inventory_no', required: true, validator: 'inventory', min_len: 3, max_len: 24 };
const MAC_FIELD = { key: 'mac', required: false, validator: 'mac', min_len: 17, max_len: 17 };
const PLATE_FIELD = { key: 'plate', required: false, validator: 'plate', min_len: 6, max_len: 12 };

describe('imei', () => {
  it('accepts a valid IMEI', () => {
    expect(validateCatalogIdentifier(IMEI_FIELD, VALID_IMEI)).toEqual({ value: VALID_IMEI, error: null });
  });

  it('rejects a bad Luhn check digit', () => {
    const bad = VALID_IMEI.slice(0, -1) + String((Number(VALID_IMEI.at(-1)) + 1) % 10);
    expect(validateCatalogIdentifier(IMEI_FIELD, bad)).toEqual({ value: null, error: ERR_IMEI_CHECKSUM });
  });

  it('rejects a 14-digit IMEI as too short', () => {
    expect(validateCatalogIdentifier(IMEI_FIELD, VALID_IMEI.slice(0, -1)))
      .toEqual({ value: null, error: ERR_IMEI_LENGTH });
  });

  it('rejects a non-numeric IMEI', () => {
    expect(validateCatalogIdentifier(IMEI_FIELD, '35209900176148X'))
      .toEqual({ value: null, error: ERR_IMEI_NOT_NUMERIC });
  });

  it('strips scanner separators before checking', () => {
    expect(validateCatalogIdentifier(IMEI_FIELD, '35-209900-176148-1'))
      .toEqual({ value: VALID_IMEI, error: null });
  });

  it('rejects a blank required IMEI', () => {
    expect(validateCatalogIdentifier(IMEI_FIELD, '')).toEqual({ value: null, error: ERR_REQUIRED });
  });
});

describe('vin', () => {
  it('accepts a valid VIN', () => {
    expect(validateCatalogIdentifier(VIN_FIELD, VALID_VIN)).toEqual({ value: VALID_VIN, error: null });
  });

  it('rejects a 16-char VIN as wrong length', () => {
    expect(validateCatalogIdentifier(VIN_FIELD, VALID_VIN.slice(0, -1)))
      .toEqual({ value: null, error: ERR_VIN_LENGTH });
  });

  it.each(['I', 'O', 'Q'])('rejects a VIN with excluded letter %s', (ch) => {
    const poisoned = ch + VALID_VIN.slice(1);
    expect(validateCatalogIdentifier(VIN_FIELD, poisoned)).toEqual({ value: null, error: ERR_VIN_CHARSET });
  });

  it('is case- and separator-tolerant, and stores the canonical upper-cased form', () => {
    expect(validateCatalogIdentifier(VIN_FIELD, VALID_VIN.toLowerCase()))
      .toEqual({ value: VALID_VIN, error: null });
  });
});

describe('inventory', () => {
  it('accepts a plain tag', () => {
    expect(validateCatalogIdentifier(INVENTORY_FIELD, 'INV-2026-014'))
      .toEqual({ value: 'INV-2026-014', error: null });
  });

  it('rejects a tag under min_len', () => {
    expect(validateCatalogIdentifier(INVENTORY_FIELD, 'AB').error).not.toBeNull();
  });

  it('rejects illegal characters', () => {
    expect(validateCatalogIdentifier(INVENTORY_FIELD, 'INV#2026!'))
      .toEqual({ value: null, error: ERR_INVENTORY_FORMAT });
  });

  it.each(['ИНВ-001', 'Инв 12', 'INV 001'])(
    'accepts Cyrillic and internal-space tags — %s (MUDD-113 H2)',
    (raw) => {
      expect(validateCatalogIdentifier(INVENTORY_FIELD, raw)).toEqual({ value: raw, error: null });
    },
  );
});

describe('mac', () => {
  it('accepts and upper-cases a valid MAC', () => {
    expect(validateCatalogIdentifier(MAC_FIELD, 'aa:bb:cc:11:22:33'))
      .toEqual({ value: 'AA:BB:CC:11:22:33', error: null });
  });

  it('rejects a malformed MAC', () => {
    expect(validateCatalogIdentifier(MAC_FIELD, 'not-a-mac')).toEqual({ value: null, error: ERR_MAC_FORMAT });
  });

  it('a blank optional MAC is accepted as not captured', () => {
    expect(validateCatalogIdentifier(MAC_FIELD, '')).toEqual({ value: null, error: null });
  });

  it('an optional MAC made only of a zero-width space is not captured, not an error (B1)', () => {
    expect(validateCatalogIdentifier(MAC_FIELD, '​')).toEqual({ value: null, error: null });
  });
});

describe('plate', () => {
  it('accepts a plain plate', () => {
    expect(validateCatalogIdentifier(PLATE_FIELD, '01A123BC')).toEqual({ value: '01A123BC', error: null });
  });

  it('rejects illegal characters', () => {
    expect(validateCatalogIdentifier(PLATE_FIELD, '01-A123-BC'))
      .toEqual({ value: null, error: ERR_PLATE_FORMAT });
  });
});
