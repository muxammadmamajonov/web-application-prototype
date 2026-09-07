/**
 * A shop's money must be labelled with the shop's own currency.
 *
 * THE BUG THIS PINS (found on a real device, 2026-08-08). A tenant can pick its
 * currency in Biznes sozlamasi. That choice reached exactly ONE consumer: the
 * backend PDF renderer, which stamps `settings_row.currency` onto generated
 * contracts and receipts. Every screen instead printed the i18n string
 * `billing.currency`, hardcoded to `UZS`.
 *
 * So for a shop set to USD, all 102 money labels in the app read UZS while its
 * own contracts and receipts read USD — the screen and the legal document
 * disagreeing about the unit of a debt. Nothing failed; both sides were
 * internally consistent, which is why it survived every test and every mock.
 * Only real production data with a non-default currency exposes it.
 *
 * The regression these tests exist to catch is a tenant surface going back to
 * the ambient lookup, which would silently restore exactly that split.
 */
import { describe, expect, it } from 'vitest';
import { readdirSync, readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { DEFAULT_CURRENCY, SUPPORTED_CURRENCIES, currencyLabel } from './currency';

const SRC = resolve(__dirname, '..');

function sourceFiles(dir: string, prefix = ''): Array<{ rel: string; text: string }> {
  const out: Array<{ rel: string; text: string }> = [];
  for (const e of readdirSync(dir, { withFileTypes: true })) {
    const rel = prefix ? `${prefix}/${e.name}` : e.name;
    if (e.isDirectory()) { out.push(...sourceFiles(resolve(dir, e.name), rel)); continue; }
    if (!e.name.endsWith('.tsx') || e.name.includes('.test.')) continue;
    out.push({ rel, text: readFileSync(resolve(dir, e.name), 'utf8') });
  }
  return out;
}

/** The surfaces that show a SHOP's money and must therefore use its currency. */
const TENANT_SURFACES = [
  ...sourceFiles(resolve(SRC, 'routes/tenant'), 'routes/tenant'),
  ...sourceFiles(resolve(SRC, 'routes/staff'), 'routes/staff'),
];

describe('the sweep reaches the source', () => {
  it('scans the tenant and staff route trees', () => {
    expect(TENANT_SURFACES.length).toBeGreaterThan(10);
  });
});

describe('tenant money carries the tenant currency', () => {
  it('no tenant or staff screen reads the hardcoded i18n currency', () => {
    const offenders: string[] = [];
    for (const { rel, text } of TENANT_SURFACES) {
      text.split('\n').forEach((line, i) => {
        if (!line.includes("'billing.currency'")) return;
        offenders.push(`${rel}:${i + 1}  ${line.trim().slice(0, 90)}`);
      });
    }
    expect(offenders,
      `these label a SHOP's money with the hardcoded platform currency:\n${offenders.join('\n')}\n` +
      'Use `useCurrency()` — the shop may trade in something other than UZS, ' +
      'and its generated contracts and receipts are stamped with that choice. ' +
      'A screen that disagrees with the contract is the bug this pins.',
    ).toEqual([]);
  });

  it('the currency comes from the session, so it needs no extra request', () => {
    const src = readFileSync(resolve(SRC, 'lib/currency.ts'), 'utf8');
    expect(src, 'useCurrency must resolve from the session store').toContain('useSession');
    expect(src, 'and key off the ACTIVE tenant, not the first membership')
      .toContain('activeTenantId');
  });
});

describe('the default is a deliberate value, not a missing lookup', () => {
  it('labels an unset currency with the platform default', () => {
    expect(currencyLabel(null)).toBe(DEFAULT_CURRENCY);
    expect(currencyLabel(undefined)).toBe(DEFAULT_CURRENCY);
    expect(currencyLabel('   ')).toBe(DEFAULT_CURRENCY);
  });

  it('passes a stored code through untouched', () => {
    // Including one we do not offer: it is what the PDFs will print, so showing
    // it is strictly better than quietly substituting something else.
    expect(currencyLabel('USD')).toBe('USD');
    expect(currencyLabel('EUR')).toBe('EUR');
  });

  it('offers the default among the selectable currencies', () => {
    // Otherwise a shop could never get back to the default once it moved off it.
    expect(SUPPORTED_CURRENCIES).toContain(DEFAULT_CURRENCY);
  });
});
