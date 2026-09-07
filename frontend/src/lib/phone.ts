/** Uzbek phone helpers — single source of truth for the +998 mask.
 *
 *  Canonical display format: `+998 90 123 45 67` (country code + 9 national
 *  digits grouped 2-3-2-2). The `+998 ` prefix is always present and cannot be
 *  deleted by the user; typing only ever edits the 9 national digits. */

const CC = '+998';

/** Extract the up-to-9 national digits from any input (drops +998 / 998). */
export function nationalDigits(raw: string): string {
  let d = (raw || '').replace(/\D/g, '');
  if (d.startsWith('998')) d = d.slice(3);
  return d.slice(0, 9);
}

/** Format any input as `+998 NN NNN NN NN`. Always keeps the +998 prefix. */
export function formatUzPhone(raw: string): string {
  const d = nationalDigits(raw);
  const parts = [CC, d.slice(0, 2), d.slice(2, 5), d.slice(5, 7), d.slice(7, 9)].filter(Boolean);
  return parts.join(' ');
}

/** True once all 9 national digits are present (format only). */
export function isValidUzPhone(v: string): boolean {
  return /^\+998 \d{2} \d{3} \d{2} \d{2}$/.test((v || '').trim());
}

/** Known Uzbek MOBILE operator codes (the 2 digits after +998). Landline area
 *  codes (71 Tashkent, etc.) are intentionally excluded — use isValidUzPhone for
 *  format-only checks and this when a mobile number is required. */
const UZ_MOBILE_CODES = new Set([
  '20', '33', '50', '55', '77', '88', '90', '91', '93', '94', '95', '97', '98', '99',
]);

/** The 2-digit operator code of an entered number ('' if not yet entered). */
export function uzOperatorCode(v: string): string {
  return nationalDigits(v).slice(0, 2);
}

/** True when the number is a complete, valid Uzbek MOBILE number (format +
 *  recognised operator code). */
export function isValidUzMobile(v: string): boolean {
  return isValidUzPhone(v) && UZ_MOBILE_CODES.has(uzOperatorCode(v));
}

/** Returns the formatted phone, or '' when no national digits were entered —
 *  so an untouched (prefix-only) field is treated as empty on save. */
export function phoneOrEmpty(v: string): string {
  return nationalDigits(v).length > 0 ? formatUzPhone(v) : '';
}

/** Display form for a phone that came back from the API.
 *
 *  The backend stores one canonical string, `+998901112233` (r96phoneuniq) —
 *  that is what makes "is this number already taken?" answerable at all. It is
 *  not what a person reads, so every render site puts the stored value through
 *  here. Null-safe, and returns '' for a missing number so callers can drop it
 *  straight into a `.filter(Boolean).join(' · ')`.
 *
 *  Note `tel:` hrefs deliberately keep the raw stored value: dialers want an
 *  unspaced number, and the spacing is purely cosmetic. */
export function displayPhone(v: string | null | undefined): string {
  return v ? formatUzPhone(v) : '';
}
