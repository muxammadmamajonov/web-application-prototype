/**
 * Client mirror of `backend/app/core/catalog_identifiers.py` (MUDD-113).
 *
 * Distinct from `identifiers.ts` (the single free-text `serial_number` /
 * `identifier_type` pair) and from `categoryIdentifiers.ts` (which only
 * enforces the generic required/max_len/numeric shape an `IdField` carries).
 * This module is the client half of the PER-FIELD `validator` a category's
 * `identifier_schema` entry can declare — `imei` / `vin` / `mac` /
 * `inventory` / `plate` / `serial` — so a bad Luhn digit or an ISO-3779
 * -excluded VIN character is rejected inline, at the field, instead of
 * surfacing as the generic error screen after a round-trip. The server
 * re-validates every submission with the same rules and is the authority;
 * `catalogIdentifierVectors.json` is run case-for-case by both this module's
 * test suite and `backend/tests/test_mudd113_catalog_identifiers.py` —
 * currently 28 enumerated cases — so drift on THOSE cases is caught on both
 * sides (mirrors `identifiers.ts` / `identifiers.py` / `identifierVectors.json`'s
 * existing pattern). This is not a general drift guard: a rule added to only
 * one side, with no matching fixture case, is invisible to both suites — add
 * a vector alongside any new rule, not just the code.
 */
import { luhnOk, normalizeImei, normalizeSerial, serialCharsAllowed } from './identifiers';

export const IMEI_LENGTH = 15;
export const VIN_LENGTH = 17;

//: VIN excludes I, O, Q — visually confusable with 1/0, dropped from ISO 3779.
const VIN_CHARSET = /^[A-HJ-NPR-Z0-9]+$/;

const MAC_RE = /^[0-9A-Fa-f]{2}([:-][0-9A-Fa-f]{2}){5}$/;

//: A vehicle plate: letters, digits, spaces. Not pinned to one country's exact
//: grammar — plate formats vary by issue year/region even within Uzbekistan.
const PLATE_RE = /^[A-Za-z0-9 ]+$/;

export const ERR_REQUIRED = 'catalog_identifier_required';
export const ERR_TOO_SHORT = 'catalog_identifier_too_short';
export const ERR_TOO_LONG = 'catalog_identifier_too_long';
export const ERR_IMEI_NOT_NUMERIC = 'catalog_identifier_imei_not_numeric';
export const ERR_IMEI_LENGTH = 'catalog_identifier_imei_length';
export const ERR_IMEI_CHECKSUM = 'catalog_identifier_imei_checksum';
export const ERR_VIN_LENGTH = 'catalog_identifier_vin_length';
export const ERR_VIN_CHARSET = 'catalog_identifier_vin_charset';
export const ERR_MAC_FORMAT = 'catalog_identifier_mac_format';
export const ERR_INVENTORY_FORMAT = 'catalog_identifier_inventory_format';
export const ERR_PLATE_FORMAT = 'catalog_identifier_plate_format';

/** Every code this module can return, for `categoryIdentifiers.ts`'s
 *  `KNOWN_CODES` (a server 400 can carry any of these too). */
export const CATALOG_IDENTIFIER_ERROR_CODES = [
  ERR_REQUIRED, ERR_TOO_SHORT, ERR_TOO_LONG,
  ERR_IMEI_NOT_NUMERIC, ERR_IMEI_LENGTH, ERR_IMEI_CHECKSUM,
  ERR_VIN_LENGTH, ERR_VIN_CHARSET, ERR_MAC_FORMAT,
  ERR_INVENTORY_FORMAT, ERR_PLATE_FORMAT,
] as const;

export interface CatalogIdentifierResult {
  value: string | null;
  error: string | null;
}

/** The subset of an `IdField` this module reads — kept narrow so a caller
 *  can pass a plain schema-field object without importing `catalogSchema`. */
export interface CatalogIdentifierField {
  validator?: string | null;
  required?: boolean;
  min_len?: number | null;
  max_len?: number | null;
}

function codepointLength(value: string): number {
  return [...value].length;
}

/**
 * Validate `raw` against one `identifier_schema` field definition.
 *
 * Returns `{ value, error }` — exactly one is set, EXCEPT for a blank value
 * on a non-required field, which returns `{ value: null, error: null }` and
 * means "not captured" (not an error) — mirrors the server's
 * `validate_catalog_identifier` three-outcome contract exactly, including the
 * "looked non-blank, normalizes to nothing" case a lone zero-width space
 * produces: this normalizer strips invisible characters the same way
 * `identifiers.ts`'s does, so a value `categoryIdentifiers.ts`'s cheaper
 * normalizer saw as non-blank can still collapse here.
 */
export function validateCatalogIdentifier(
  field: CatalogIdentifierField, raw: string,
): CatalogIdentifierResult {
  const text = typeof raw === 'string' ? raw : '';
  const validator = field.validator ?? null;

  // IMEI strips scanner separators before length/charset checks; every other
  // kind uses the generic serial normalizer (NFC-compose, trim, collapse
  // whitespace, strip invisibles).
  const value = validator === 'imei' ? normalizeImei(text) : normalizeSerial(text);

  if (!value) return field.required ? { value: null, error: ERR_REQUIRED } : { value: null, error: null };

  if (validator === 'imei') {
    if (!/^\d+$/.test(value)) return { value: null, error: ERR_IMEI_NOT_NUMERIC };
    if (value.length !== IMEI_LENGTH) return { value: null, error: ERR_IMEI_LENGTH };
    if (!luhnOk(value)) return { value: null, error: ERR_IMEI_CHECKSUM };
    return { value, error: null };
  }

  if (validator === 'vin') {
    const vin = value.toUpperCase().replace(/[ -]/g, '');
    if (vin.length !== VIN_LENGTH) return { value: null, error: ERR_VIN_LENGTH };
    if (!VIN_CHARSET.test(vin)) return { value: null, error: ERR_VIN_CHARSET };
    return { value: vin, error: null };
  }

  if (validator === 'mac') {
    if (!MAC_RE.test(value)) return { value: null, error: ERR_MAC_FORMAT };
    return { value: value.toUpperCase(), error: null };
  }

  if (validator === 'inventory' && !serialCharsAllowed(value)) {
    return { value: null, error: ERR_INVENTORY_FORMAT };
  }
  if (validator === 'plate' && !PLATE_RE.test(value)) {
    return { value: null, error: ERR_PLATE_FORMAT };
  }
  // "serial" and unset validators: length bounds only — `normalizeSerial`
  // does not restrict the character set by itself, only `serialCharsAllowed`
  // (used above for "inventory") does.

  const minLen = field.min_len ?? null;
  const maxLen = field.max_len ?? null;
  const len = codepointLength(value);
  if (minLen && len < minLen) return { value: null, error: ERR_TOO_SHORT };
  if (maxLen && len > maxLen) return { value: null, error: ERR_TOO_LONG };
  return { value, error: null };
}
