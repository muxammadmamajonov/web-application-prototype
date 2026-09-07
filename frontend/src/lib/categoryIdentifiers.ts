/**
 * Client mirror of `backend/app/core/category_identifiers.py` (MUDD-85/98).
 *
 * A `catalog_categories.identifier_schema` entry declares the per-unit fields
 * a category wants captured at sale time — e.g. phones: IMEI 1 (required),
 * IMEI 2 (optional); cars: VIN. This validates a contract line's submission
 * against the SELECTED category's schema before it leaves the browser, the
 * same way `identifiers.ts` mirrors the server for the single free-text
 * serial/IMEI field — but this module is for the category-declared, possibly
 * multi-field, NAMED set (`InstallmentItemIn.identifiers`), which is a
 * separate thing from that single field. An item can carry both, either, or
 * neither.
 *
 * Deliberately does NOT check for a submitted key outside the schema — the UI
 * only ever renders inputs the schema itself declared, so an
 * `category_identifier_unknown_key` can only originate server-side (the
 * schema changed under an open form) and is handled as a submission-error via
 * `parseCategoryIdentifierError`, not pre-empted here.
 *
 * Also does NOT — and cannot — check for a DUPLICATE value: that is a
 * per-tenant check against every other contract already on record, knowable
 * only from the server's response. `parseCategoryIdentifierError` is what
 * lets a caller bind that 400 back to the field that caused it.
 */
import { CATALOG_IDENTIFIER_ERROR_CODES, validateCatalogIdentifier } from './catalogIdentifiers';
import type { IdField } from './catalogSchema';

export const ERR_UNKNOWN_KEY = 'category_identifier_unknown_key';
export const ERR_REQUIRED = 'category_identifier_required';
export const ERR_TOO_LONG = 'category_identifier_too_long';
export const ERR_NOT_NUMERIC = 'category_identifier_not_numeric';
export const ERR_DUPLICATE = 'category_identifier_duplicate';

// Includes `catalog_identifier_*` (from `catalogIdentifiers.ts`, the mirror of
// `app/core/catalog_identifiers.py`'s per-`validator` shape checks — Luhn IMEI,
// VIN charset, ...) alongside this module's own generic shape codes, so a
// server 400 for either kind binds back to the field that caused it.
const KNOWN_CODES = new Set<string>([
  ERR_UNKNOWN_KEY, ERR_REQUIRED, ERR_TOO_LONG, ERR_NOT_NUMERIC, ERR_DUPLICATE,
  ...CATALOG_IDENTIFIER_ERROR_CODES,
]);

/** Agrees with `InstallmentItemIdentifier.value` (VARCHAR 64) and the
 *  server's `category_identifiers.MAX_VALUE_LENGTH`. */
const MAX_VALUE_LENGTH = 64;

/** NFC-compose, then trim and collapse internal whitespace runs — mirrors
 *  the server's `_normalize` for the same reason: a value composed as one
 *  codepoint and one composed as two must compare identically. */
function normalize(raw: string): string {
  return raw.normalize('NFC').split(/\s+/).filter(Boolean).join(' ');
}

/** Codepoints, not UTF-16 units — Python's `len()` and Postgres' VARCHAR(64)
 *  both count codepoints. */
function codepointLength(value: string): number {
  return [...value].length;
}

/**
 * Validate `values` against `schema`; returns `{ key: errorCode }` for every
 * field that fails. A blank optional field is not an error — most schema
 * fields exist for SOME categories' devices, not every line sold under them.
 *
 * A field carrying a `validator` (IMEI/VIN/MAC/inventory/plate/serial) is
 * additionally checked with `validateCatalogIdentifier` — the client mirror
 * of `app/core/catalog_identifiers.py` — AFTER the generic checks above pass,
 * the same two-stage order `_validate_item_identifiers` uses server-side, so
 * a bad Luhn digit or an ISO-3779-excluded VIN character surfaces inline at
 * the field instead of only after a round-trip to the server (MUDD-113 H1).
 */
export function validateCategoryIdentifiers(
  schema: IdField[], values: Record<string, string>,
): Record<string, string> {
  const errors: Record<string, string> = {};
  for (const field of schema) {
    const raw = values[field.key];
    const value = typeof raw === 'string' ? normalize(raw) : '';
    if (!value) {
      if (field.required) errors[field.key] = ERR_REQUIRED;
      continue;
    }
    const maxLen = field.max_len && field.max_len > 0 && field.max_len <= MAX_VALUE_LENGTH
      ? field.max_len : MAX_VALUE_LENGTH;
    if (codepointLength(value) > maxLen) { errors[field.key] = ERR_TOO_LONG; continue; }
    if (field.type === 'number' && !/^\d+$/.test(value)) { errors[field.key] = ERR_NOT_NUMERIC; continue; }
    if (field.validator) {
      const { error } = validateCatalogIdentifier(field, value);
      // `error: null` here can still mean "collapsed to nothing" (a value
      // made only of invisible characters) on an optional field — not a
      // rejection, same as the generic blank-optional case above.
      if (error) errors[field.key] = error;
    }
  }
  return errors;
}

/**
 * The values that should be submitted: a blank optional field is dropped
 * rather than sent as `""`, and any key outside `schema` (e.g. left over from
 * a category the line was switched away from) is dropped too — never sent as
 * an `unknown_key` the server would then have to refuse.
 *
 * Only meaningful for a submission `validateCategoryIdentifiers` already
 * found empty; callers check that first.
 *
 * A `validator` field is submitted in its CANONICAL form (e.g. an
 * upper-cased VIN) — `validateCatalogIdentifier`'s own normalized value —
 * so what the user sees matches what the server ends up storing, and a value
 * that collapses to nothing under the stricter invisible-character-stripping
 * normalizer (optional field, e.g. a lone zero-width space) is dropped here
 * too instead of sent as a phantom value.
 */
export function normalizedCategoryIdentifiers(
  schema: IdField[], values: Record<string, string>,
): Record<string, string> {
  const result: Record<string, string> = {};
  for (const field of schema) {
    const raw = values[field.key];
    const value = typeof raw === 'string' ? normalize(raw) : '';
    if (!value) continue;
    if (field.validator) {
      const { value: canon } = validateCatalogIdentifier(field, value);
      if (canon) result[field.key] = canon;
      continue;
    }
    result[field.key] = value;
  }
  return result;
}

export interface CategoryIdentifierApiError {
  code: string;
  key: string;
}

/**
 * Parse a `"{code}:{key}"` 400 detail from the server's
 * `_validate_item_identifiers` (e.g. `"category_identifier_duplicate:imei1"`).
 * Returns `null` for anything else, so a caller can fall through to the
 * generic error message for every other kind of failure.
 */
export function parseCategoryIdentifierError(detail: string | undefined): CategoryIdentifierApiError | null {
  if (!detail) return null;
  const idx = detail.indexOf(':');
  if (idx === -1) return null;
  const code = detail.slice(0, idx);
  const key = detail.slice(idx + 1);
  return KNOWN_CODES.has(code) && key ? { code, key } : null;
}
