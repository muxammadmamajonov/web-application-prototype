/** Pure card-number helpers shared by the card manager + payer views (#204).
 *  Kept dependency-free so they're unit-testable without the React component. */

export const onlyDigits = (s: string) => s.replace(/\D/g, '');

/** Group digits in 4s: "1234567890123456" → "1234 5678 9012 3456". */
export const groups = (digits: string) => digits.replace(/(.{4})/g, '$1 ').trim();

/** Masked display for lists — "9860123456784455" → "9860****4455". */
export const mask = (digits: string) =>
  digits.length >= 8 ? `${digits.slice(0, 4)}****${digits.slice(-4)}` : groups(digits);

/** Luhn checksum — rejects mistyped/invalid card numbers before saving.
 *  UZCARD (8600…) and HUMO (9860…) follow ISO/IEC 7812, so real cards pass. */
export function luhn(digits: string): boolean {
  let sum = 0;
  let alt = false;
  for (let i = digits.length - 1; i >= 0; i--) {
    const c = digits.charCodeAt(i) - 48;
    if (c < 0 || c > 9) return false;
    let n = c;
    if (alt) { n *= 2; if (n > 9) n -= 9; }
    sum += n;
    alt = !alt;
  }
  return digits.length > 0 && sum % 10 === 0;
}
