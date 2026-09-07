import { describe, it, expect } from 'vitest';
import type { IdField } from './catalogSchema';
import {
  ERR_NOT_NUMERIC, ERR_REQUIRED, ERR_TOO_LONG,
  normalizedCategoryIdentifiers, parseCategoryIdentifierError, validateCategoryIdentifiers,
} from './categoryIdentifiers';

/**
 * The client half of the MUDD-85/98 category-identifier contract. Vectors
 * mirror `backend/tests/test_mudd85_category_identifiers.py` so the two ends
 * cannot silently drift — a phone's IMEI 1 required and IMEI 2 optional is
 * the exact schema that test suite exercises.
 */
const PHONE_SCHEMA: IdField[] = [
  { key: 'imei1', label_uz: 'IMEI 1', label_ru: 'IMEI 1', type: 'text', required: true, max_len: 17 },
  { key: 'imei2', label_uz: 'IMEI 2', label_ru: 'IMEI 2', type: 'text', required: false, max_len: 17 },
];

describe('validateCategoryIdentifiers', () => {
  it('rejects a missing required field', () => {
    expect(validateCategoryIdentifiers(PHONE_SCHEMA, {})).toEqual({ imei1: ERR_REQUIRED });
  });

  it('accepts the required field alone — the optional one may be omitted', () => {
    expect(validateCategoryIdentifiers(PHONE_SCHEMA, { imei1: '352099001761481' })).toEqual({});
  });

  it('accepts both fields when both are given', () => {
    expect(validateCategoryIdentifiers(PHONE_SCHEMA, {
      imei1: '352099001761481', imei2: '490154203237518',
    })).toEqual({});
  });

  it('rejects a value over the schema max_len', () => {
    expect(validateCategoryIdentifiers(PHONE_SCHEMA, { imei1: '1'.repeat(18) }))
      .toEqual({ imei1: ERR_TOO_LONG });
  });

  it('rejects a non-numeric value for a "number" field', () => {
    const numeric: IdField[] = [{ key: 'n', label_uz: 'N', label_ru: 'N', type: 'number', required: true }];
    expect(validateCategoryIdentifiers(numeric, { n: '12a' })).toEqual({ n: ERR_NOT_NUMERIC });
    expect(validateCategoryIdentifiers(numeric, { n: '123' })).toEqual({});
  });

  it('an empty schema has nothing to require', () => {
    expect(validateCategoryIdentifiers([], { anything: 'x' })).toEqual({});
  });

  it('a category with no schema treats every field as optional-and-absent, not an error', () => {
    // The UI never renders a field for a schema-less category, so there is
    // nothing submitted to validate — this documents that the function itself
    // does not invent a requirement.
    expect(validateCategoryIdentifiers([], {})).toEqual({});
  });
});

describe('normalizedCategoryIdentifiers', () => {
  it('drops a blank optional field rather than sending ""', () => {
    expect(normalizedCategoryIdentifiers(PHONE_SCHEMA, { imei1: '352099001761481', imei2: '' }))
      .toEqual({ imei1: '352099001761481' });
  });

  it('drops a key outside the schema — e.g. left over from a since-switched category', () => {
    expect(normalizedCategoryIdentifiers(PHONE_SCHEMA, { imei1: '352099001761481', vin: 'SOMEVIN' }))
      .toEqual({ imei1: '352099001761481' });
  });

  it('collapses internal whitespace the same way the server does', () => {
    expect(normalizedCategoryIdentifiers(PHONE_SCHEMA, { imei1: '  352 099001 761481  ' }))
      .toEqual({ imei1: '352 099001 761481' });
  });
});

describe('parseCategoryIdentifierError', () => {
  it('splits the server\'s "{code}:{key}" 400 detail', () => {
    expect(parseCategoryIdentifierError('category_identifier_duplicate:imei1'))
      .toEqual({ code: 'category_identifier_duplicate', key: 'imei1' });
  });

  it('returns null for a detail with no known category_identifier_ prefix', () => {
    expect(parseCategoryIdentifierError('customer_not_found')).toBeNull();
    expect(parseCategoryIdentifierError('unknown_category:smartfonlar')).toBeNull();
  });

  it('returns null for undefined', () => {
    expect(parseCategoryIdentifierError(undefined)).toBeNull();
  });
});
