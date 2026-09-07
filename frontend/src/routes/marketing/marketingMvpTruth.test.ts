import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { describe, expect, it } from 'vitest';

const LANDING_SOURCE = readFileSync(resolve(__dirname, 'LandingPage.tsx'), 'utf8');
const PAYMENT_METHODS_SOURCE = readFileSync(
  resolve(__dirname, '../../../../backend/app/core/payment_methods.py'),
  'utf8',
);
const LOCALES = (['uz', 'ru'] as const).map((locale) => ({
  locale,
  landing: JSON.parse(
    readFileSync(resolve(__dirname, `../../i18n/locales/${locale}.json`), 'utf8'),
  ).landing as unknown,
}));

const DEFERRED_MATRIX_KEYS = [
  'click',
  'payme',
  'advanced_reports',
  'multi_branch',
  'commission',
  'risk_monitoring',
  'pdf_template_upload',
  'api_keys',
  'webhooks',
  'custom_domain',
  'white_label',
  'account_manager',
];

describe('public marketing matches the shipped MVP', () => {
  it('counts the offered payment rails instead of withdrawn provider methods', () => {
    const offered = PAYMENT_METHODS_SOURCE.match(/OFFERED:.*= \(([^)]*)\)/)?.[1]
      ?.match(/[a-z_]+/g);
    expect(offered).toEqual(['cash', 'card']);
    expect(LANDING_SOURCE).toContain("{ val: '2',    key: 'payMethods' }");
    expect(LANDING_SOURCE).not.toContain("{ val: '3',    key: 'payMethods' }");
  });

  it('never builds the public comparison table from deferred entitlements', () => {
    for (const key of DEFERRED_MATRIX_KEYS) {
      expect(LANDING_SOURCE, `deferred pricing key leaked: ${key}`).not.toContain(
        `feat(p, '${key}')`,
      );
    }
  });

  it('shows retained product areas instead of the deferred broadcasts tour', () => {
    expect(LANDING_SOURCE).toContain(
      "const TOUR_TABS = ['overview', 'customers', 'installments', 'payments', 'reports']",
    );
    expect(LANDING_SOURCE).not.toContain("'payments', 'broadcasts'");
  });

  it.each(LOCALES)('$locale copy does not promise deferred or nonexistent behaviour', ({ landing }) => {
    const copy = JSON.stringify(landing);
    for (const banned of [
      /\bClick\b/i,
      /\bPayme\b/i,
      /white label/i,
      /под своим брендом/i,
      /o'z brendingiz/i,
      /1C/i,
      /LTV/i,
      /offline navbat/i,
      /уйдут в очередь/i,
      /Pro va Scale/i,
    ]) {
      expect(copy, `public copy matched ${banned}`).not.toMatch(banned);
    }
  });

  it.each(LOCALES)('$locale copy names the retained manual rail and shared bot', ({ landing }) => {
    const copy = JSON.stringify(landing);
    expect(copy).toContain('MuddatoBot');
    if (copy.includes('bank')) expect(copy).toContain("bank o'tkazmasi");
    else expect(copy).toContain('банковский перевод');
  });
});
