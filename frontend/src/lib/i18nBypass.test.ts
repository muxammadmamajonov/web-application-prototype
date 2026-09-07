/**
 * F4-04 — retained surfaces must not carry their own copy.
 *
 * Two private translation systems had grown alongside the real one: a
 * `COPY: Record<'uz'|'ru', …>` catalog in `LandingPage` (74 keys) and another in
 * `DemoPage` (13 groups), plus ~40 inline `lng === 'ru' ? … : …` ternaries and
 * hand-maintained month/weekday tables in both date pickers.
 *
 * Copy held in JSX cannot be reviewed, corrected or kept in parity like every
 * other string, and PLATFORM_STANDARD §5 requires every user-visible string to
 * resolve through `t()` with entries in BOTH bundles.
 *
 * Deferred (V2-only) pages are out of scope for this pass and excluded
 * explicitly, with the exclusion itself checked.
 */
import { describe, expect, it } from 'vitest';
import { readFileSync, readdirSync } from 'node:fs';
import { resolve } from 'node:path';
import uz from '@/i18n/locales/uz.json';
import ru from '@/i18n/locales/ru.json';
import { V2_SURFACES_ENABLED } from '@/routes/_scope';

const SRC = resolve(__dirname, '..');

/** Unreachable while the scope flag is false — not corrected in this pass. */
const DEFERRED = [
  'routes/marketing/CareersPage.tsx',
  'routes/marketing/FeaturesPage.tsx',
  'routes/marketing/RoadmapPage.tsx',
  'routes/auth/SignupPage.tsx',
];

/** The retained files this finding names. */
const TOUCHED = [
  'components/ui/DatePicker.tsx',
  'components/ui/DateRangePicker.tsx',
  'routes/marketing/DemoPage.tsx',
  'routes/marketing/LandingPage.tsx',
  'routes/marketing/ContactPage.tsx',
  'components/marketing/PublicLayout.tsx',
];

const read = (rel: string) => readFileSync(resolve(SRC, rel), 'utf8');

function reachableSources(): Array<{ rel: string; text: string }> {
  const out: Array<{ rel: string; text: string }> = [];
  const walk = (dir: string) => {
    for (const e of readdirSync(dir, { withFileTypes: true })) {
      const p = resolve(dir, e.name);
      if (e.isDirectory()) { walk(p); continue; }
      if (!/\.tsx?$/.test(e.name) || e.name.includes('.test.')) continue;
      const rel = p.slice(SRC.length + 1);
      if (DEFERRED.some((d) => rel.endsWith(d))) continue;
      out.push({ rel, text: readFileSync(p, 'utf8') });
    }
  };
  walk(SRC);
  return out;
}

const flatten = (o: Record<string, unknown>, prefix = ''): string[] =>
  Object.entries(o).flatMap(([k, v]) => {
    const key = prefix ? `${prefix}.${k}` : k;
    return v && typeof v === 'object' && !Array.isArray(v)
      ? flatten(v as Record<string, unknown>, key) : [key];
  });
const uzKeys = new Set(flatten(uz as Record<string, unknown>));
const ruKeys = new Set(flatten(ru as Record<string, unknown>));

describe('the deferred exclusion is still justified', () => {
  it('those pages are unreachable', () => {
    expect(V2_SURFACES_ENABLED,
      'V2 surfaces are reachable — the DEFERRED list must shrink and those pages be corrected',
    ).toBe(false);
  });
});

/**
 * A locale branch that picks a hardcoded STRING is copy in JSX. A branch that
 * picks a server field (`p.name_ru`), a whole per-locale document, or a BCP-47
 * tag for `Intl` is data or configuration and must stay.
 */
const COPY_TERNARY = /(?:lng === 'ru'|\bru)\s*\?\s*(['"`])(?!(?:ru|uz)[-_])/;
/** The surfaces this finding names: retained marketing and date code. */
const IN_SCOPE = (rel: string) =>
  rel.startsWith('routes/marketing/') || /components\/ui\/Date\w+\.tsx$/.test(rel)
  || rel === 'components/marketing/PublicLayout.tsx';

describe('no retained marketing or date source branches on the locale for COPY', () => {
  it('has no hardcoded-string locale ternary in scope', () => {
    const offenders = reachableSources()
      .filter(({ rel }) => IN_SCOPE(rel))
      .flatMap(({ rel, text }) => text.split('\n')
        .map((line, i) => ({ line, n: i + 1 }))
        .filter(({ line }) => COPY_TERNARY.test(line))
        .map(({ n, line }) => `${rel}:${n}: ${line.trim().slice(0, 100)}`));
    expect(offenders, `move these into uz.json/ru.json:\n${offenders.join('\n')}`).toEqual([]);
  });

  it('the detector would still catch a reintroduced literal', () => {
    expect(COPY_TERNARY.test("{lng === 'ru' ? 'Повторить' : 'Qayta urinish'}")).toBe(true);
    expect(COPY_TERNARY.test("label: ru ? 'Сотрудники' : 'Xodimlar'")).toBe(true);
  });

  it('keeps locale selection over SERVER data, which is not copy', () => {
    // `p.name_ru` / `p.name_uz` come from the plans API; picking between them is
    // data selection, not a hardcoded string, and must stay.
    const src = read('routes/marketing/LandingPage.tsx');
    expect(src).toMatch(/name_ru|name_uz/);
    expect(COPY_TERNARY.test("(lng === 'ru' ? p.name_ru : p.name_uz)")).toBe(false);
  });

  it('keeps BCP-47 tags for Intl, which are not copy either', () => {
    expect(COPY_TERNARY.test("lng === 'ru' ? 'ru-RU' : 'uz-Latn-UZ',")).toBe(false);
    // Intl is still used where its data is reliable — full-date formatting.
    expect(read('routes/marketing/legal/LegalPage.tsx')).toContain('uz-Latn-UZ');
  });

  it('calendar NAMES come from the bundle, not from Intl', () => {
    // The browser pass found Chromium's uz-Latn calendar data missing: it
    // rendered "Mon/Tue" and "M06" in an Uzbek UI. Locale resources cannot
    // degrade that way, so the names live there.
    const src = read('components/ui/calendarNames.ts');
    expect(src).toContain('calendar.months');
    expect(src).not.toMatch(/new Intl\.DateTimeFormat/);
    for (const key of ['calendar.months', 'calendar.monthsShort', 'calendar.weekdaysShort']) {
      expect(uzKeys.has(key), `uz.json missing ${key}`).toBe(true);
      expect(ruKeys.has(key), `ru.json missing ${key}`).toBe(true);
    }
  });

  it('the legal date line uses Intl rather than a month table', () => {
    const src = read('routes/marketing/legal/LegalPage.tsx');
    expect(src).toContain('Intl.DateTimeFormat');
    expect(src, 'a hand-maintained month table is back').not.toMatch(/'янв|'yanvar'/);
  });
});

describe('no retained source keeps a private copy catalog', () => {
  it.each(TOUCHED)('%s has no COPY table', (rel) => {
    expect(read(rel), 'a second translation system').not.toMatch(/const COPY[:\s]/);
  });

  it('LandingPage no longer defines its own lookup helper', () => {
    expect(read('routes/marketing/LandingPage.tsx')).not.toMatch(/function tCopy/);
  });

  it('the date pickers no longer hand-maintain month or weekday tables', () => {
    for (const rel of ['components/ui/DatePicker.tsx', 'components/ui/DateRangePicker.tsx']) {
      const src = read(rel);
      expect(src, `${rel} still hardcodes names`).not.toMatch(/const (MONTHS|WEEKDAYS|SHORT):/);
      expect(src).toContain('useCalendarNames');
    }
  });
});

describe('the migrated copy exists in BOTH bundles', () => {
  it.each(['landing', 'demo', 'contact', 'public'])('%s has keys in uz and ru', (ns) => {
    const inUz = [...uzKeys].filter((k) => k.startsWith(`${ns}.`));
    const inRu = [...ruKeys].filter((k) => k.startsWith(`${ns}.`));
    expect(inUz.length, `${ns} is missing from uz.json`).toBeGreaterThan(5);
    expect(inRu.length, `${ns} is missing from ru.json`).toBeGreaterThan(5);
    expect(inUz.filter((k) => !ruKeys.has(k)), `${ns} keys absent from ru`).toEqual([]);
    expect(inRu.filter((k) => !uzKeys.has(k)), `${ns} keys absent from uz`).toEqual([]);
  });

  it('carries the whole landing catalog, not a sample', () => {
    // 74 flat COPY keys became nested `landing.*`, plus the ui/matrix additions.
    expect([...uzKeys].filter((k) => k.startsWith('landing.')).length).toBeGreaterThanOrEqual(74);
  });

  it('every migrated value is non-empty in both locales', () => {
    const get = (o: unknown, path: string) =>
      path.split('.').reduce<unknown>((a, p) => (a as Record<string, unknown>)?.[p], o);
    for (const key of [...uzKeys].filter((k) => /^(landing|demo|contact|public)\./.test(k))) {
      for (const [name, bundle] of [['uz', uz], ['ru', ru]] as const) {
        const v = get(bundle, key);
        expect(typeof v, `${key} is not a string in ${name}`).toBe('string');
        expect(String(v).trim().length, `${key} is empty in ${name}`).toBeGreaterThan(0);
      }
    }
  });
});

describe('whole-bundle parity is preserved', () => {
  it('every uz key exists in ru', () => {
    expect([...uzKeys].filter((k) => !ruKeys.has(k))).toEqual([]);
  });

  it('ru adds only Russian plural categories', () => {
    const extra = [...ruKeys].filter((k) => !uzKeys.has(k));
    expect(extra.every((k) => /_(few|many)$/.test(k)), `unexpected ru-only keys: ${extra}`).toBe(true);
  });
});

// ── P5-01: the locale bundles are the SOLE calendar-copy source ─────────────

describe('no calendar-name catalog survives in reachable source', () => {
  /**
   * Representative names from each list, in both locales.
   *
   * Matched as EXACT quoted tokens (`'Вс'`), which is the shape a name table
   * has. A bare substring search trips over ordinary prose — "Вс" occurs inside
   * Russian words on the legal pages — and would make this guard noise.
   */
  const CALENDAR_LITERALS = [
    // uz months / short months / weekdays
    'Yanvar', 'Fevral', 'Sentabr', 'Dekabr', 'Iyn', 'Iyl', 'Avg', 'Du', 'Ch', 'Ya',
    // ru months / short months / weekdays
    'Январь', 'Февраль', 'Сентябрь', 'Декабрь', 'Янв', 'Фев', 'Ноя',
    'Пн', 'Вт', 'Ср', 'Чт', 'Пт', 'Сб', 'Вс',
  ];
  const quoted = (lit: string) => new RegExp(`(['"\`])${lit}\\1`);

  it('no reachable non-test source contains a localized calendar literal', () => {
    const offenders: string[] = [];
    for (const { rel, text } of reachableSources()) {
      // The locale bundles themselves are JSON and are not scanned here.
      text.split('\n').forEach((line, i) => {
        const hit = CALENDAR_LITERALS.find((lit) => quoted(lit).test(line));
        if (hit) offenders.push(`${rel}:${i + 1}: contains ${JSON.stringify(hit)}`);
      });
    }
    expect(offenders,
      `calendar copy belongs only in uz.json/ru.json:\n${offenders.join('\n')}`).toEqual([]);
  });

  it('the detector matches a real table line but not prose', () => {
    expect(quoted('Yanvar').test("  uz: ['Yanvar', 'Fevral',")).toBe(true);
    expect(quoted('Вс').test("weekdaysShort: ['Пн', 'Вт', 'Вс']")).toBeTruthy();
    expect(quoted('Вс').test("<p>Все права защищены</p>")).toBe(false);
  });

  it('calendarNames.ts declares no name arrays of its own', () => {
    const src = read('components/ui/calendarNames.ts');
    expect(src, 'a second calendar catalog is back').not.toMatch(/FALLBACK/);
    // A 7- or 12-element string-literal array is the shape of such a table.
    const arrays = src.match(/\[\s*'[^']+'\s*(?:,\s*'[^']+'\s*){5,}\]/g) ?? [];
    expect(arrays, `hardcoded name arrays: ${arrays.join(' | ')}`).toEqual([]);
  });

  it('the pickers declare none either', () => {
    for (const rel of ['components/ui/DatePicker.tsx', 'components/ui/DateRangePicker.tsx']) {
      expect(read(rel)).not.toMatch(/const (MONTHS|WEEKDAYS|SHORT|FALLBACK)/);
    }
  });

  it('a malformed resource fails loudly instead of falling back', async () => {
    const { readNameList } = await import('@/components/ui/calendarNames');
    for (const bad of [undefined, null, [], ['a'], Array(12).fill(''), Array(11).fill('x'), 'nope', [1, 2, 3]]) {
      expect(() => readNameList(bad, 'calendar.months', 12, 'uz'),
        `${JSON.stringify(bad)} should have thrown`).toThrow(/calendar\.months/);
    }
  });

  it('names the locale and the key so the defect is actionable', async () => {
    const { readNameList } = await import('@/components/ui/calendarNames');
    expect(() => readNameList([], 'calendar.weekdaysShort', 7, 'ru'))
      .toThrow(/"calendar\.weekdaysShort" must be 7 non-empty strings in locale "ru"/);
  });
});

describe('both real bundles satisfy the calendar contract', () => {
  const get = (o: unknown, path: string) =>
    path.split('.').reduce<unknown>((a, p) => (a as Record<string, unknown>)?.[p], o);

  it.each([['uz', uz], ['ru', ru]] as const)('%s renders the expected arrays', async (name, bundle) => {
    const { readNameList } = await import('@/components/ui/calendarNames');
    const months = readNameList(get(bundle, 'calendar.months'), 'calendar.months', 12, name);
    const short = readNameList(get(bundle, 'calendar.monthsShort'), 'calendar.monthsShort', 12, name);
    const week = readNameList(get(bundle, 'calendar.weekdaysShort'), 'calendar.weekdaysShort', 7, name);
    expect(months).toHaveLength(12);
    expect(short).toHaveLength(12);
    expect(week).toHaveLength(7);
    // Distinct entries — a copy/paste slip would otherwise pass the length check.
    expect(new Set(months).size).toBe(12);
    expect(new Set(week).size).toBe(7);
  });

  it('uz and ru genuinely differ', () => {
    expect(get(uz, 'calendar.months')).not.toEqual(get(ru, 'calendar.months'));
    expect(get(uz, 'calendar.weekdaysShort')).not.toEqual(get(ru, 'calendar.weekdaysShort'));
  });

  it('neither bundle leaks English weekday or numeric month codes', () => {
    // The exact failure mode the Intl attempt shipped to the browser.
    for (const bundle of [uz, ru]) {
      const all = [
        ...(get(bundle, 'calendar.months') as string[]),
        ...(get(bundle, 'calendar.monthsShort') as string[]),
        ...(get(bundle, 'calendar.weekdaysShort') as string[]),
      ];
      expect(all.some((v) => /^(Mon|Tue|Wed|Thu|Fri|Sat|Sun)$/.test(v))).toBe(false);
      expect(all.some((v) => /\d/.test(v))).toBe(false);
    }
  });
});
