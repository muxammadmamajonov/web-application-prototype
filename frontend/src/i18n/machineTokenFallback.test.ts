/**
 * A finite vocabulary must be fully translated, because its fallback is the
 * machine token itself.
 *
 * THE PATTERN THIS PINS. Several screens translate an enumerable value with
 * `t(\`prefix.${x}\`, x)` — the key is built from the value, and the fallback IS
 * that value. It is a reasonable shape: a new backend value still renders
 * *something* instead of blank. But the something is a machine token, i18next
 * warns nobody, and to the developer who added the value everything looks fine.
 * The failure is only ever visible to a user, in a language that is not theirs.
 *
 * Two instances had shipped this way:
 *
 *   - `audit.actions.*` — the audit log showed operators
 *     `tenant.impersonated_mutation`, the record of a platform admin acting
 *     inside somebody else's shop (guarded separately, against the backend
 *     source, in lib/auditActions.contract.test.ts);
 *   - `platformCatalog.type_*` — FIELD_TYPES has six members and only two were
 *     translated, so the category builder offered "color", "enum", "decimal"
 *     and "bool" as raw English in an Uzbek UI. Found by enabling the deferred
 *     surface and looking at it.
 *
 * The vocabularies below are declared in TypeScript, so they can be imported
 * and checked exactly. Add a list here whenever a new one starts using the
 * key-from-value shape.
 */
import { describe, expect, it } from 'vitest';
import uz from './locales/uz.json';
import ru from './locales/ru.json';
import { FIELD_TYPES } from '@/lib/catalogSchema';
import { LIMIT_KEYS } from '@/lib/planFeatures';
import { ALL_PAYMENT_METHODS } from '@/lib/paymentMethods';

/** A vocabulary, and the i18n prefix its members are looked up under. */
const VOCABULARIES: Array<{ name: string; values: readonly string[]; prefix: string }> = [
  // `t(`platformCatalog.type_${ty}`, ty)` — the category schema builder.
  { name: 'FIELD_TYPES', values: FIELD_TYPES, prefix: 'platformCatalog.type_' },
  // `t(`tenantSettings.pm_${k}`)` — payment-method labels, shared by the tenant
  // settings toggles, the payment ledger and the customer's own history.
  { name: 'ALL_PAYMENT_METHODS', values: ALL_PAYMENT_METHODS, prefix: 'tenantSettings.pm_' },
  // Plan limit rows. These carry their own uz/ru in LIMIT_LABEL rather than i18n,
  // so they are listed to assert exactly that — see the test below.
  { name: 'LIMIT_KEYS', values: LIMIT_KEYS, prefix: '' },
];

// eslint-disable-next-line @typescript-eslint/no-explicit-any
function lookup(bundle: any, dotted: string): unknown {
  return dotted.split('.').reduce<unknown>(
    (acc, part) => (acc && typeof acc === 'object' ? (acc as Record<string, unknown>)[part] : undefined),
    bundle,
  );
}

describe('the vocabularies are reachable', () => {
  it('imports non-empty lists', () => {
    for (const v of VOCABULARIES) {
      expect(v.values.length, `${v.name} is empty — re-point this test`).toBeGreaterThan(0);
    }
  });
});

describe('every member of a finite vocabulary has a label', () => {
  const withPrefix = VOCABULARIES.filter((v) => v.prefix);

  it.each(withPrefix.flatMap((v) => [['uz', uz, v], ['ru', ru, v]] as const))(
    '%s translates every %s member',
    (lng, bundle, vocab) => {
      const missing = vocab.values.filter((val) => !lookup(bundle, `${vocab.prefix}${val}`));
      expect(missing,
        `${vocab.name} members with no ${lng} label: ${missing.join(', ')}\n` +
        `These are looked up as \`${vocab.prefix}<value>\` with the VALUE itself ` +
        'as the fallback, so a missing key shows a user the raw machine token — ' +
        'silently, with no warning and nothing broken-looking to the developer.',
      ).toEqual([]);
    },
  );
});

describe('vocabularies that deliberately carry their own labels', () => {
  it('LIMIT_KEYS labels live in LIMIT_LABEL, not i18n', async () => {
    // Asserted rather than assumed: if these ever move to `t()` they acquire the
    // same silent-fallback risk and must be given a prefix in the table above.
    const { LIMIT_LABEL } = await import('@/lib/planFeatures');
    for (const key of LIMIT_KEYS) {
      expect(LIMIT_LABEL[key]?.uz, `LIMIT_LABEL.${key}.uz missing`).toBeTruthy();
      expect(LIMIT_LABEL[key]?.ru, `LIMIT_LABEL.${key}.ru missing`).toBeTruthy();
    }
  });
});
