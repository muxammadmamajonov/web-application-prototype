// @vitest-environment jsdom
/**
 * The Russian UI must not fall back to the Uzbek text written at the call site.
 *
 * `localeCompleteness.test.ts` proves the keys exist in both bundles. This file
 * proves the consequence at runtime: the components the audit named actually
 * render Russian under `ru`, and the Uzbek inline default never reaches the DOM.
 *
 * A static key-presence check alone would still pass if a key were added with
 * the Uzbek string copied into ru.json, which is the exact mistake being guarded
 * against — so every assertion here is on rendered text.
 */
import { describe, expect, it, beforeEach, afterEach } from 'vitest';
import { cleanup, render, screen } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import i18n from '@/i18n';
import { SkipLink } from '@/components/a11y/SkipLink';
import { ActiveFilters, RangeGroup } from '@/components/ui/FilterDrawer';
import { SmartContact } from '@/components/ui/SmartContact';
import { CreatePlanModal } from '@/routes/platform/_modals/CreatePlanModal';

/** Uzbek inline defaults that must never appear while the locale is `ru`. */
const UZBEK_FALLBACKS = [
  "Asosiy kontentga o'tish",
  'Asosiy',
  'Narx',
  'Hammasini tozalash',
  'Operator kodini tekshiring',
  "Kod bir marta belgilanadi va keyinchalik o'zgartirilmaydi.",
];

function withQuery(ui: React.ReactNode) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return <QueryClientProvider client={qc}>{ui}</QueryClientProvider>;
}

beforeEach(async () => { await i18n.changeLanguage('ru'); });
afterEach(async () => { cleanup(); await i18n.changeLanguage('uz'); });

describe('ru renders Russian, not the Uzbek fallback', () => {
  it('SkipLink — the first focusable element on every authenticated shell', async () => {
    render(<SkipLink />);
    const link = screen.getByRole('link');
    expect(link.textContent).toBe('Перейти к основному содержимому');
    expect(link.textContent).not.toBe("Asosiy kontentga o'tish");
  });

  it('SkipLink still renders Uzbek under uz', async () => {
    await i18n.changeLanguage('uz');
    render(<SkipLink />);
    expect(screen.getByRole('link').textContent).toBe('Asosiy kontentga o‘tish');
  });

  it('CreatePlanModal — section kickers and hints', () => {
    render(withQuery(<CreatePlanModal open onClose={() => {}} />));
    const text = document.body.textContent ?? '';
    expect(text).toContain('Основное');
    expect(text).toContain('Цена');
    expect(text).toContain('Код задаётся один раз и в дальнейшем не меняется.');
    for (const uzbek of UZBEK_FALLBACKS) expect(text).not.toContain(uzbek);
  });

  it('ActiveFilters — shared "clear all" control', () => {
    // The control only renders with more than one active chip.
    render(
      <ActiveFilters
        filters={[
          { key: 'a', label: 'A', onRemove: () => {} },
          { key: 'b', label: 'B', onRemove: () => {} },
        ]}
        onClearAll={() => {}}
      />,
    );
    expect(document.body.textContent).toContain('Очистить всё');
    expect(document.body.textContent).not.toContain('Hammasini tozalash');
  });

  it('RangeGroup — from/to placeholders', () => {
    render(<RangeGroup label="Сумма" value={{ min: '', max: '' }} onChange={() => {}} />);
    // Both bounds are <input type="number"> -> role spinbutton, not textbox.
    const phs = screen.getAllByRole('spinbutton').map((el) => el.getAttribute('placeholder'));
    expect(phs).toEqual(['от', 'до']);
  });

  it('SmartContact — operator-code hint', () => {
    // A valid UZ number whose operator code is not a mobile one.
    render(<SmartContact value="+998 71 123 45 67" onChange={() => {}} />);
    expect(document.body.textContent).toContain('Проверьте код оператора');
    expect(document.body.textContent).not.toContain('Operator kodini tekshiring');
  });
});

describe('both bundles resolve the shared keys', () => {
  const SHARED = [
    ['a11y.skip', 'Asosiy kontentga o‘tish', 'Перейти к основному содержимому'],
    ['common.clearAll', 'Hammasini tozalash', 'Очистить всё'],
    ['filters.from', 'dan', 'от'],
    ['filters.to', 'gacha', 'до'],
    ['smartContact.phoneOperator', 'Operator kodini tekshiring', 'Проверьте код оператора'],
  ] as const;

  it.each(SHARED)('%s resolves distinctly in uz and ru', (key, uzValue, ruValue) => {
    expect(i18n.getFixedT('uz')(key)).toBe(uzValue);
    expect(i18n.getFixedT('ru')(key)).toBe(ruValue);
  });

  it('pluralized bizTypesSelected uses each language’s own categories', () => {
    const ru = i18n.getFixedT('ru');
    const uz = i18n.getFixedT('uz');
    expect(ru('platformCatalog.bizTypesSelected', { count: 1 })).toBe('Выбран 1');
    expect(ru('platformCatalog.bizTypesSelected', { count: 3 })).toBe('Выбрано 3');
    expect(ru('platformCatalog.bizTypesSelected', { count: 12 })).toBe('Выбрано 12');
    expect(uz('platformCatalog.bizTypesSelected', { count: 3 })).toBe('3 ta tanlandi');
  });
});
