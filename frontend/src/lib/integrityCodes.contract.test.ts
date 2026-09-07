/**
 * Every unique-constraint conflict the backend names must have a message here.
 *
 * THE BUG THIS PINS. `_INTEGRITY_CODES` in `backend/app/main.py` has turned
 * constraint violations into named 409 codes since #87 —
 * `customer_phone_exists`, `product_sku_exists` and friends. Not one of them
 * was listed in `CODE_KEYS`, so every single one fell through to
 * `errors.conflict`: "Bu ma'lumot allaqachon o'zgargan — sahifani yangilab,
 * qayta ko'ring."
 *
 * That copy is wrong twice over. Nothing changed under the user, and refreshing
 * cannot help — they typed a phone number, an SKU or a period that someone
 * already holds, which is a thing they can fix in two seconds if anyone tells
 * them. Instead the backend did the hard part correctly and the answer was
 * thrown away at the last step.
 *
 * The map is read out of the Python source rather than restated, the same idiom
 * as `invitationCodes.contract.test.ts` — a hand-copied list is exactly what
 * drifted in the first place.
 */
import { describe, expect, it } from 'vitest';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import uz from '@/i18n/locales/uz.json';
import ru from '@/i18n/locales/ru.json';

const BACKEND = resolve(__dirname, '../../../backend/app');
const errorsSrc = readFileSync(resolve(__dirname, 'errors.ts'), 'utf8');
const mainSrc = readFileSync(resolve(BACKEND, 'main.py'), 'utf8');

/** The values of `_INTEGRITY_CODES` — the codes a 409 can actually carry. */
function backendIntegrityCodes(): string[] {
  const block = mainSrc.match(/_INTEGRITY_CODES\s*=\s*\{([\s\S]*?)\n\}/);
  if (!block) throw new Error('_INTEGRITY_CODES not found — re-point this test');
  const codes = new Set<string>();
  for (const m of block[1].matchAll(/"[a-z_]+"\s*:\s*"([a-z_]+)"/g)) codes.add(m[1]);
  return [...codes].sort();
}

const codes = backendIntegrityCodes();

describe('unique-constraint conflicts reach the user as themselves', () => {
  it('reads a real set of codes out of the backend', () => {
    expect(codes.length).toBeGreaterThanOrEqual(6);
    expect(codes).toContain('customer_phone_exists');
    expect(codes).toContain('phone_already_registered');
  });

  it.each(codes)('%s is mapped in CODE_KEYS', (code) => {
    expect(
      errorsSrc.includes(`${code}: 'errors.`),
      `${code} is not in CODE_KEYS, so it renders as the generic "refresh the ` +
      `page" conflict message — advice that cannot help for a duplicate value.`,
    ).toBe(true);
  });

  it.each(codes)('%s has both uz and ru copy', (code) => {
    const key = errorsSrc.match(
      new RegExp(`${code}:\\s*'errors\\.([a-z_]+)'`),
    )?.[1];
    expect(key, `${code} has no CODE_KEYS entry to resolve`).toBeTruthy();
    const uzErrors = (uz as { errors: Record<string, string> }).errors;
    const ruErrors = (ru as { errors: Record<string, string> }).errors;
    expect(uzErrors[key!], `uz.json errors.${key} missing`).toBeTruthy();
    expect(ruErrors[key!], `ru.json errors.${key} missing`).toBeTruthy();
  });

  it('none of the messages tell the user to refresh the page', () => {
    // The specific failure being prevented: copy that describes a stale-state
    // conflict, applied to a duplicate-value conflict.
    const uzErrors = (uz as { errors: Record<string, string> }).errors;
    for (const code of codes) {
      const key = errorsSrc.match(new RegExp(`${code}:\\s*'errors\\.([a-z_]+)'`))?.[1];
      expect(key).not.toBe('conflict');
      expect(uzErrors[key!]?.toLowerCase()).not.toContain('yangilab');
    }
  });
});
