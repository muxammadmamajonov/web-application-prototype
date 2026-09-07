import { useTranslation } from 'react-i18next';

/**
 * Localized month and weekday names for the calendars.
 *
 * `uz.json` / `ru.json` are the ONLY source. Not `Intl`, and not a second copy
 * in this file.
 *
 * `Intl` was the obvious choice and it is wrong here: the browser pass found
 * that Chromium's trimmed ICU has no real `uz-Latn` calendar data, so it
 * silently falls back to the root locale and renders English weekdays ("Mon",
 * "Tue") and numeric month codes ("M06") inside an Uzbek UI. Node's fuller ICU
 * returns correct Uzbek for the same call, so nothing catches that except
 * looking at a real browser. `Intl` remains the right tool for NUMBER and
 * full-date formatting, where the data is reliable everywhere.
 *
 * A hardcoded fallback table was equally wrong, for a different reason: it is a
 * second translation catalog living outside the locale files, which is exactly
 * what the standard forbids. It cannot be reviewed with the rest of the copy, it
 * is not covered by the uz/ru parity check, and it makes a *missing* bundle
 * entry invisible — the UI keeps working while the two catalogs quietly diverge.
 *
 * So a malformed or missing resource throws. It can only come from a bad edit to
 * a statically-imported JSON file — never from user input or the network — so it
 * is a build-time defect, and the guard in `i18nBypass.test.ts` fails CI before
 * it can ship.
 */

/** Every calendar list the pickers need, with the length each must have. */
const SHAPE = {
  months: { key: 'calendar.months', length: 12 },
  monthsShort: { key: 'calendar.monthsShort', length: 12 },
  weekdaysShort: { key: 'calendar.weekdaysShort', length: 7 },
} as const;

/**
 * Read one list from the active bundle, or throw naming exactly what is wrong.
 *
 * Exported for the guard test, which asserts the real `uz` and `ru` bundles both
 * satisfy it — the check that replaces the old fallback table.
 */
export function readNameList(
  value: unknown, key: string, expected: number, lng: string,
): string[] {
  const ok = Array.isArray(value)
    && value.length === expected
    && value.every((x) => typeof x === 'string' && x.trim().length > 0);
  if (!ok) {
    throw new Error(
      `i18n: "${key}" must be ${expected} non-empty strings in locale "${lng}". `
      + `Fix the locale bundle — there is deliberately no hardcoded fallback.`,
    );
  }
  return value as string[];
}

/** Month names, short month names and Monday-first weekdays for the locale. */
export function useCalendarNames(): {
  months: string[]; shortMonths: string[]; weekdays: string[]; lng: 'uz' | 'ru';
} {
  const { t, i18n } = useTranslation();
  const lng = (i18n.language?.startsWith('ru') ? 'ru' : 'uz') as 'uz' | 'ru';

  const list = ({ key, length }: { key: string; length: number }) =>
    readNameList(t(key, { returnObjects: true }) as unknown, key, length, lng);

  return {
    lng,
    months: list(SHAPE.months),
    shortMonths: list(SHAPE.monthsShort),
    weekdays: list(SHAPE.weekdaysShort),
  };
}
