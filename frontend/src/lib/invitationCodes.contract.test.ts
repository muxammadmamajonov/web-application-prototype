/**
 * Every invitation error the backend raises must have a message here.
 *
 * THE BUG THIS PINS. A recipient opening an already-used link saw "Havolada
 * xatolik / Nimadir noto'g'ri ketdi" — the generic fallback. The specific
 * message existed and was well written; it was simply keyed on
 * `invitation_used`, a code nothing in the backend emits. The two real codes,
 * `invitation_already_used` (the read) and `invitation_invalid_or_used` (the
 * claim), were unmapped, so the one situation the copy was written for was the
 * one situation it never appeared in.
 *
 * A hand-maintained map cannot be trusted to stay in step with the strings a
 * different language raises, so this reads the codes out of the backend source
 * rather than restating them — the same idiom as `deepLinks.contract.test.ts`
 * and `paymentMethods.contract.test.ts`.
 */
import { describe, expect, it } from 'vitest';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import uz from '@/i18n/locales/uz.json';
import ru from '@/i18n/locales/ru.json';

const BACKEND = resolve(__dirname, '../../../backend/app');
const errorsSrc = readFileSync(resolve(__dirname, 'errors.ts'), 'utf8');
const invitationsSrc = readFileSync(resolve(BACKEND, 'api/invitations.py'), 'utf8');

/** Every `"invitation_*"` string literal the invitations router raises. */
function backendInvitationCodes(): string[] {
  const found = new Set<string>();
  for (const m of invitationsSrc.matchAll(/"(invitation_[a-z_]+)"/g)) found.add(m[1]);
  // `invitation_id` is an audit field name, not an error code.
  found.delete('invitation_id');
  return [...found].sort();
}

describe('the invitation errors the backend can raise', () => {
  const codes = backendInvitationCodes();

  it('is a non-empty set (the scan works)', () => {
    expect(codes.length).toBeGreaterThan(2);
    expect(codes).toContain('invitation_expired');
  });

  it.each(backendInvitationCodes())('%s is mapped to a message key', (code) => {
    // Matched against the source rather than the imported object because the
    // map is a plain record whose keys are the codes themselves.
    expect(errorsSrc, `${code} has no entry in errors.ts — it will fall through ` +
      'to the generic "something went wrong", which is what hid the ' +
      '"link already used" case.').toMatch(new RegExp(`\\b${code}\\s*:`));
  });
});

describe('every mapped message resolves in both bundles', () => {
  const keys = [...errorsSrc.matchAll(/\b(invitation_[a-z_]+)\s*:\s*'(errors\.[a-zA-Z_]+)'/g)]
    .map((m) => m[2].split('.')[1]);

  it('found the mappings', () => expect(keys.length).toBeGreaterThan(2));

  it.each([...new Set(keys)])('errors.%s exists in uz and ru', (key) => {
    const u = (uz as Record<string, Record<string, string>>).errors?.[key];
    const r = (ru as Record<string, Record<string, string>>).errors?.[key];
    expect(u, `uz errors.${key}`).toBeTruthy();
    expect(r, `ru errors.${key}`).toBeTruthy();
    expect(u).not.toBe(r);   // a shared string means one locale was forgotten
  });
});

describe('the already-used case specifically', () => {
  it('does not say "something went wrong"', () => {
    // The reported symptom. A single-use link being spent is an expected,
    // explainable outcome — not an unknown failure — and the copy must name
    // both the cause and the way out.
    for (const bundle of [uz, ru]) {
      const msg = (bundle as Record<string, Record<string, string>>).errors.invitation_used;
      expect(msg.length).toBeGreaterThan(20);
      expect(msg).toMatch(/—|-/);   // "cause — remedy"
    }
  });
});
