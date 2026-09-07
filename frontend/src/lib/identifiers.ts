/**
 * What a product identifier on a contract line means, and what it may contain.
 *
 * The client mirror of `backend/app/core/identifiers.py`, so a shop is told what
 * is wrong while typing instead of after a round-trip. The server re-validates
 * every submission with the same rules and is the authority; a backend test
 * compares the two files so they cannot drift apart.
 *
 *   IMEI               — a device identity: 15 digits, Luhn-checked
 *   SERIAL_OR_CONTRACT — a shop's own serial or paper contract reference
 *   LEGACY_UNVERIFIED  — recorded before this distinction existed; displayed,
 *                        never offered as a choice, never re-interpreted
 */

export const IDENTIFIER_TYPES = ['IMEI', 'SERIAL_OR_CONTRACT', 'LEGACY_UNVERIFIED'] as const;
export type IdentifierType = (typeof IDENTIFIER_TYPES)[number];

/** What a person may choose. `LEGACY_UNVERIFIED` is a migration outcome. */
export const SELECTABLE_IDENTIFIER_TYPES = ['IMEI', 'SERIAL_OR_CONTRACT'] as const;
export type SelectableIdentifierType = (typeof SELECTABLE_IDENTIFIER_TYPES)[number];

/** Agrees with the `installment_items.serial_number` column and the API field. */
export const MAX_IDENTIFIER_LENGTH = 64;
export const IMEI_LENGTH = 15;

/** Structured codes; the caller maps them to uz/ru copy. */
export const ERR_REQUIRED = 'identifier_required';
export const ERR_IMEI_NOT_NUMERIC = 'identifier_imei_not_numeric';
export const ERR_IMEI_LENGTH = 'identifier_imei_length';
export const ERR_IMEI_CHECKSUM = 'identifier_imei_checksum';
export const ERR_TOO_LONG = 'identifier_too_long';
export const ERR_ILLEGAL_CHARS = 'identifier_illegal_characters';
export const ERR_UNKNOWN_TYPE = 'identifier_type_unknown';

/**
 * Letters in ANY script, decimal digits, and the four separators shops use.
 *
 * "Letters" must not quietly mean ASCII: this product runs in Uzbek and
 * Russian, where a shop's own reference looks like `ДОГОВОР-2026/15`. Uzbek
 * Latin's `oʻ`/`gʻ` carry U+02BB, which Unicode classifies as a modifier letter
 * (`\p{L}`), so the correct character passes under the same rule.
 *
 * Everything else stays refused — markup, quotes, emoji, backslash, and
 * punctuation outside the four separators.
 */
const SERIAL_ALLOWED = /^[\p{L}\p{Nd} _/-]+$/u;

/** The same charset test, exported so `catalogIdentifiers.ts` (the mirror of
 *  `app/core/catalog_identifiers.py`) can reuse it for the "inventory"
 *  validator instead of hand-rolling a second, driftable pattern — mirrors
 *  the server reusing `_serial_chars_allowed` for the same purpose. */
export function serialCharsAllowed(value: string): boolean {
  return SERIAL_ALLOWED.test(value);
}

/** Whitespace and every dash a device label might print between IMEI groups. */
const IMEI_SEPARATORS = /[\s\u2010-\u2015\u2212\uFE58\uFE63\uFF0D-]+/g;

/**
 * Characters that render as nothing but survive a copy-paste.
 *
 * Two values that look identical must not compare unequal — including to the
 * duplicate check — so they are removed rather than stored.
 *
 * The category set is exactly the server's: control, format, surrogate,
 * private-use and unassigned. Matching by PROPERTY rather than by a hand-listed
 * range is what keeps the two ends in step — an enumerated list silently misses
 * codepoints like U+0890 and U+E0001 that the server does strip.
 */
const INVISIBLE_CATEGORY = /^[\p{Cc}\p{Cf}\p{Cs}\p{Co}\p{Cn}]$/u;

/** Variation selectors are category Mn, so no C* property catches them — and
 *  they ride along on pasted emoji. */
function isVariationSelector(cp: number): boolean {
  return (cp >= 0xfe00 && cp <= 0xfe0f) || (cp >= 0xe0100 && cp <= 0xe01ef);
}

function stripInvisibles(raw: string): string {
  let out = '';
  // Iterating the string yields whole codepoints, so an astral character is
  // never split into surrogates the filter would then throw away.
  for (const ch of raw) {
    if (ch === '\t') { out += ' '; continue; }
    if (isVariationSelector(ch.codePointAt(0)!)) continue;
    if (INVISIBLE_CATEGORY.test(ch)) continue;
    out += ch;
  }
  return out;
}

/** The Luhn check digit, as GSMA specifies for an IMEI's 15th digit. */
export function luhnOk(digits: string): boolean {
  if (!digits || !/^\d+$/.test(digits)) return false;
  let total = 0;
  for (let i = 0; i < digits.length; i += 1) {
    let n = Number(digits[digits.length - 1 - i]);
    if (i % 2 === 1) {
      n *= 2;
      if (n > 9) n -= 9;
    }
    total += n;
  }
  return total % 10 === 0;
}

/** Digits only: a scanned label prints `35-209900-176148-1`. */
export function normalizeImei(raw: string): string {
  return stripInvisibles(raw).replace(IMEI_SEPARATORS, '').trim();
}

/** Trim the outside, collapse internal whitespace. Composed to NFC so `Й` typed
 *  as one codepoint and as two are the same identifier. Case is preserved — a
 *  serial is frequently case-significant. */
export function normalizeSerial(raw: string): string {
  return stripInvisibles(raw.normalize('NFC')).replace(/\s+/g, ' ').trim();
}

/** Codepoints, not UTF-16 units — Python's `len()` and Postgres' VARCHAR(64)
 *  both count codepoints, and `'…'.length` would disagree above the BMP. */
function codepointLength(value: string): number {
  return [...value].length;
}

export interface IdentifierResult {
  value: string | null;
  error: string | null;
}

/**
 * Validate under the DECLARED type. An empty value with no type means "no
 * identifier", which is valid: most sales are not of serialised goods.
 */
export function validateIdentifier(
  kind: string | null | undefined,
  raw: string | null | undefined,
): IdentifierResult {
  const text = typeof raw === 'string' ? raw : '';
  const declared = (kind ?? '').trim().toUpperCase();
  const blank = stripInvisibles(text).trim() === '';

  if (!declared) return blank ? { value: null, error: null } : { value: null, error: ERR_UNKNOWN_TYPE };
  if (declared === 'LEGACY_UNVERIFIED') return { value: null, error: ERR_UNKNOWN_TYPE };
  if (declared !== 'IMEI' && declared !== 'SERIAL_OR_CONTRACT') {
    return { value: null, error: ERR_UNKNOWN_TYPE };
  }
  if (blank) return { value: null, error: ERR_REQUIRED };

  if (declared === 'IMEI') {
    const value = normalizeImei(text);
    if (!/^\d+$/.test(value)) return { value: null, error: ERR_IMEI_NOT_NUMERIC };
    if (value.length !== IMEI_LENGTH) return { value: null, error: ERR_IMEI_LENGTH };
    if (!luhnOk(value)) return { value: null, error: ERR_IMEI_CHECKSUM };
    return { value, error: null };
  }

  const value = normalizeSerial(text);
  if (!value) return { value: null, error: ERR_REQUIRED };
  if (codepointLength(value) > MAX_IDENTIFIER_LENGTH) return { value: null, error: ERR_TOO_LONG };
  if (!SERIAL_ALLOWED.test(value)) return { value: null, error: ERR_ILLEGAL_CHARS };
  return { value, error: null };
}

/** i18n key for a rejection code. Never render the code itself. */
export function identifierErrorKey(code: string): string {
  return `identifier.${code}`;
}
