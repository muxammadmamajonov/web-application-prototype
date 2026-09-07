/**
 * The demo form and the API it posts to must accept the same contact values.
 *
 * The form deliberately takes either an Uzbek phone number or an email address
 * (one `SmartContact` field, `isValidPhone || isValidEmail`). If the server's
 * grammar is narrower, the visitor is told their address is fine and then the
 * request 422s — the lead is lost and nobody hears about it. That is exactly
 * what happened while the field was posted as `phone` against a phone-only type.
 *
 * So the two email grammars are pinned to each other here rather than being kept
 * in step by hand. This test reads the backend source for the same reason
 * `auditReferences.test.ts` does: a cross-language invariant that nothing else
 * can check is worth checking from the side that can.
 */
import { describe, expect, it } from 'vitest';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { isValidEmail } from './email';
import { isValidUzPhone } from './phone';

const VALIDATORS = resolve(__dirname, '../../../backend/app/core/validators.py');

/** `EMAIL_PATTERN = r"…"` — the one the backend actually compiles. */
function backendEmailPattern(): string {
  const src = readFileSync(VALIDATORS, 'utf8');
  const m = src.match(/^EMAIL_PATTERN\s*=\s*r"(.+)"$/m);
  expect(m, 'backend EMAIL_PATTERN not found — did the constant move?').toBeTruthy();
  return m![1];
}

describe('the client and the server agree on what an email is', () => {
  it('uses the identical pattern on both sides', () => {
    // `isValidEmail`'s literal, printed back out. Byte-comparing the two makes
    // any one-sided tightening fail here instead of in production.
    const clientPattern = /^[^\s@]+@[^\s@]+\.[^\s@]{2,}$/.source;
    expect(backendEmailPattern()).toBe(clientPattern);
  });

  it('is the pattern this module actually validates with', () => {
    // Guards the assertion above against drifting into a decorative constant:
    // the literal it compares must be the one `isValidEmail` behaves like.
    const re = new RegExp(backendEmailPattern());
    for (const s of ['a@b.uz', 'sardor@gmail.com', 'x@y', 'no-at-sign', 'a b@c.uz', '']) {
      expect(re.test(s), `disagreement on ${s || '(empty)'}`).toBe(isValidEmail(s));
    }
  });
});

describe('every value the form accepts is a value the API accepts', () => {
  // The direction that matters is one-way: nothing the form waves through may
  // 422. The reverse asymmetry is deliberate and harmless — `normalize_uz_phone`
  // also takes `+998901234567` and the bare 9-digit local part, which the field
  // never produces because `formatUzPhone` masks every keystroke, but which a
  // direct API caller reasonably would.
  //
  // `backend/tests/test_p9p2_lead_capture_contract.py` mirrors this corpus and
  // asserts the matching status for each value.
  it.each([
    '+998 90 123 45 67',        // the only phone shape the masked field yields
    '+998 33 555 44 33',
    'sardor@gmail.com',
    "o'tkir.aliyev@umail.uz",
  ])('%s passes the form gate, so the API must take it', (value) => {
    expect(isValidUzPhone(value) || isValidEmail(value)).toBe(true);
  });

  it.each([
    'not a contact at all',
    'hello@',
    '@gmail.com',
    'sardor@gmail',
    '+998 90 123 4',            // incomplete mask
    '',
  ])('%s is rejected by the form gate too', (value) => {
    expect(isValidUzPhone(value) || isValidEmail(value)).toBe(false);
  });
});
