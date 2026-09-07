/**
 * LOW-1 (MUDD-62) — the onboarding wizard's plan-price unit was a strict
 * `lng === 'ru' ? 'сум/мес' : 'soʻm/oy'` ternary in `WelcomePage.tsx`, bypassing
 * `t()` entirely. Every other price/currency surface in the app (e.g.
 * `platform/PlansPage.tsx`, `platform/AdsPage.tsx`) resolves the currency word
 * through `t('common.sum', ...)`; this was the one holdout, and a translator
 * editing `locales/*.json` could never touch this string.
 *
 * The fix composes two keys that already exist and are already translated in
 * both locales — `common.sum` ("so'm" / "сум") and `billing.monthly`
 * ("/oy" / "/мес") — rather than adding a third, narrower key.
 */
import { describe, expect, it } from 'vitest';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import uz from '@/i18n/locales/uz.json';
import ru from '@/i18n/locales/ru.json';

const SRC = resolve(__dirname, 'WelcomePage.tsx');

describe('welcome wizard plan-price unit is localized (LOW-1)', () => {
  const src = readFileSync(SRC, 'utf8');

  it('does not hardcode the Russian currency string', () => {
    expect(src).not.toContain('сум/мес');
  });

  it('does not branch on locale to hand-pick a raw currency literal', () => {
    expect(src).not.toMatch(/lng === 'ru' \? '[^']*сум[^']*' : '[^']*'/);
  });

  it('reads the currency word and the per-month suffix through t()', () => {
    expect(src).toMatch(/t\(['"]common\.sum['"]/);
    expect(src).toMatch(/t\(['"]billing\.monthly['"]/);
  });

  it('both locale bundles carry both keys the fix depends on', () => {
    expect(uz.common?.sum, 'uz.common.sum').toBeTruthy();
    expect(ru.common?.sum, 'ru.common.sum').toBeTruthy();
    expect(uz.billing?.monthly, 'uz.billing.monthly').toBeTruthy();
    expect(ru.billing?.monthly, 'ru.billing.monthly').toBeTruthy();
  });
});
