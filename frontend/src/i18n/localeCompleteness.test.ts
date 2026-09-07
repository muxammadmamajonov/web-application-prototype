/**
 * Every `t()` key used in production source must exist in BOTH locale bundles.
 *
 * When a key is absent from both files, i18next falls back to the inline default
 * written at the call site — which in this codebase is Uzbek. The Russian UI then
 * silently renders Uzbek instead of a raw key, so nothing looks broken and no
 * existing test notices. PLATFORM_STANDARD §5 requires every user-visible string
 * to resolve through `t()` with entries in both bundles.
 *
 * Scope is production source only. Test files legitimately reference keys that
 * must NOT exist — `collectionsView.test.tsx` asserts `status.due` stays unmapped
 * — so including them would invert the check.
 *
 * Keys built at runtime (`t(`status.${s}`)`) cannot be resolved statically. They
 * are not ignored: each template's static prefix must still match real keys in
 * both bundles, and the short list of call sites that pass a plain variable is
 * enumerated below, so a new one fails until it is deliberately classified.
 */
import { describe, expect, it } from 'vitest';
import { readFileSync, readdirSync, statSync } from 'node:fs';
import { resolve } from 'node:path';
import uz from '@/i18n/locales/uz.json';
import ru from '@/i18n/locales/ru.json';
import { MENU_DESC_KEY } from '@/routes/_navConfig';
import { readNameList } from '@/components/ui/calendarNames';

const SRC = resolve(__dirname, '..');

/**
 * Call sites that pass a variable rather than a literal, so no key text exists
 * in the source at all. Each resolves to keys that are covered by other tests or
 * by construction (nav config labels, backend error-code maps, route titles).
 * A new entry here must be justified — it is a hole in this guard.
 */
const UNRESOLVABLE_CALL_FILES = [
  'components/layout/MiniAppHeader.tsx',   // t(key) — title key chosen by the route
  'components/marketing/PublicLayout.tsx', // t(group.titleKey) / t(it.labelKey) — nav config
  'lib/errors.ts',                         // t(CODE_KEYS[code]) — backend error-code map
  'routes/_navConfig.ts',                  // t(it.label) — nav item definitions
  'routes/router.tsx',                     // t(key) — document title per route
  'routes/tenant/SettingsPage.tsx',        // t(FORMULA_DESCS[k]) — calc formula descriptions
  'routes/tenant/StaffPage.tsx',           // t(ROLE_LABEL_KEY[role]) — shared role labels
  // `t(descKey, '')` where descKey = MENU_DESC_KEY[it.key] — a closed 18-entry
  // record in `_navConfig.ts` mapping each nav key to a `menuDesc.*` key. The
  // whole value set is asserted against both bundles below, so this file's keys
  // are covered by construction rather than exempted.
  'routes/shared/MenuPage.tsx',
  // `t(key, { returnObjects: true })` where key comes from the closed 3-entry
  // SHAPE table. These resolve to ARRAYS, not strings, so the leaf flattener
  // above cannot see them by design; they are asserted directly below with the
  // production `readNameList` validator.
  'components/ui/calendarNames.ts',
  // `t(identityMessageKey(state))` where state comes from the closed
  // IDENTITY_STATES + FlowState union in `lib/identityStates.ts`. Every value
  // is asserted against both bundles below, so the keys are covered by
  // construction rather than exempted.
  'components/ui/PhoneVerificationCard.tsx',
  // `t(keys.named)` / `t(keys.anonymous)` from the two exported greeting
  // tables. Both pairs are asserted against both bundles below.
  'lib/greeting.ts',
].sort();

/**
 * The `calendar.*` lists `calendarNames.ts` reads. Hardcoded because `SHAPE` is
 * module-private and TR-011 minimised exports — but the list is not taken on
 * trust: the test below asserts the production file references exactly these.
 */
const CALENDAR_SHAPE: Array<[key: string, length: number]> = [
  ['calendar.months', 12],
  ['calendar.monthsShort', 12],
  ['calendar.weekdaysShort', 7],
];

type Json = { [k: string]: Json | string };

const PLURAL = /_(zero|one|two|few|many|other)$/;

function flatten(obj: Json, prefix = ''): Map<string, string> {
  const out = new Map<string, string>();
  for (const [k, v] of Object.entries(obj)) {
    const key = `${prefix}${k}`;
    if (typeof v === 'string') out.set(key, v);
    else for (const [ck, cv] of flatten(v, `${key}.`)) out.set(ck, cv);
  }
  return out;
}

const UZ = flatten(uz as unknown as Json);
const RU = flatten(ru as unknown as Json);
const UZ_BASE = new Set([...UZ.keys()].map((k) => k.replace(PLURAL, '')));
const RU_BASE = new Set([...RU.keys()].map((k) => k.replace(PLURAL, '')));

function productionSources(): Array<{ rel: string; text: string }> {
  const out: Array<{ rel: string; text: string }> = [];
  const walk = (dir: string) => {
    for (const entry of readdirSync(dir)) {
      const full = resolve(dir, entry);
      if (statSync(full).isDirectory()) {
        if (entry === '__tests__' || entry === 'locales') continue;
        walk(full);
        continue;
      }
      if (!/\.tsx?$/.test(entry)) continue;
      if (/\.test\.tsx?$/.test(entry)) continue;
      out.push({ rel: full.slice(SRC.length + 1), text: readFileSync(full, 'utf8') });
    }
  };
  walk(SRC);
  return out;
}

/** `t('key'`, `t("key"`, or `` t(`key` `` — first argument only. */
const LITERAL_CALL = /\bt\(\s*(['"`])((?:\\.|(?!\1)[^\\])*)\1/g;
/** `` t(`prefix.${x}`) `` — the static head of a runtime-built key. */
const TEMPLATE_CALL = /\bt\(\s*`([^`$]*)\$\{/g;
/**
 * A `t()` call whose FIRST argument is not a string or template literal:
 * `t(someVar)`, `t(MAP[code])`, `t(descKey, '')`, `t(key, { returnObjects: true })`.
 *
 * Matching on "the first argument is not a literal" rather than on enumerated
 * variable shapes is deliberate. The previous form listed the punctuation that
 * may follow an identifier — `[`, `.`, `)` — and omitted `,`, so every variable
 * call that ALSO passed a default or an options object went unseen.
 *
 * The whitespace sits INSIDE the lookahead: written as `\s*(?!…)` it can
 * backtrack to zero width and then mis-flag a literal that starts on the next
 * line, which is how `t(\n  'platformBroadcasts.searchPh', …)` in
 * `BroadcastsPage.tsx` gets falsely reported.
 */
const NON_LITERAL_FIRST_ARG = /\bt\((?!\s*['"`)])/g;
const KEY_PREFIX = /keyPrefix:\s*['"]([^'"]+)['"]/g;

interface Site { key: string; files: Set<string> }

function collect() {
  const literal = new Map<string, Site>();
  const templates = new Map<string, Set<string>>();
  const variableFiles = new Set<string>();

  for (const { rel, text } of productionSources()) {
    const prefixes = new Set([...text.matchAll(KEY_PREFIX)].map((m) => m[1]));
    const prefix = prefixes.size === 1 ? `${[...prefixes][0]}.` : '';

    for (const m of text.matchAll(LITERAL_CALL)) {
      const raw = m[2];
      if (raw.includes('${')) continue; // interpolated -> handled as a template
      const key = prefix + raw;
      const site = literal.get(key) ?? { key, files: new Set<string>() };
      site.files.add(rel);
      literal.set(key, site);
    }
    for (const m of text.matchAll(TEMPLATE_CALL)) {
      const head = prefix + m[1];
      if (!head) continue;
      (templates.get(head) ?? templates.set(head, new Set()).get(head)!).add(rel);
    }
    if (NON_LITERAL_FIRST_ARG.test(text)) variableFiles.add(rel);
    NON_LITERAL_FIRST_ARG.lastIndex = 0;
  }
  return { literal, templates, variableFiles };
}

const { literal, templates, variableFiles } = collect();

const resolves = (key: string, table: Map<string, string>, base: Set<string>) =>
  table.has(key) || base.has(key.replace(PLURAL, ''));

describe('locale completeness', () => {
  it('finds a meaningful number of t() call sites (guards the scanner itself)', () => {
    // If the regex or the walker breaks, everything below passes vacuously.
    expect(literal.size).toBeGreaterThan(1500);
  });

  it('every statically resolvable t() key exists in BOTH uz and ru', () => {
    const missing: string[] = [];
    for (const [key, site] of literal) {
      const inUz = resolves(key, UZ, UZ_BASE);
      const inRu = resolves(key, RU, RU_BASE);
      if (inUz && inRu) continue;
      const where = [...site.files].sort().join(', ');
      const which = !inUz && !inRu ? 'BOTH' : !inUz ? 'uz' : 'ru';
      missing.push(`${key} — missing from ${which} — ${where}`);
    }
    expect(missing.sort(), `${missing.length} unresolved key(s)`).toEqual([]);
  });

  it('uz and ru carry the same base keys (plural categories may differ)', () => {
    const uzOnly = [...UZ_BASE].filter((k) => !RU_BASE.has(k)).sort();
    const ruOnly = [...RU_BASE].filter((k) => !UZ_BASE.has(k)).sort();
    expect({ uzOnly, ruOnly }).toEqual({ uzOnly: [], ruOnly: [] });
  });

  it('runtime-built keys still have their static prefix present in both bundles', () => {
    const orphaned: string[] = [];
    for (const [head, files] of templates) {
      const hit = (base: Set<string>) => [...base].some((k) => k.startsWith(head));
      if (!hit(UZ_BASE) || !hit(RU_BASE)) {
        orphaned.push(`${head}* — no key with this prefix — ${[...files].sort().join(', ')}`);
      }
    }
    expect(orphaned.sort()).toEqual([]);
  });

  it('only the enumerated files pass a variable to t()', () => {
    // Keeps the unresolvable set from growing silently.
    expect([...variableFiles].sort()).toEqual(UNRESOLVABLE_CALL_FILES);
  });
});

/**
 * Two of the classified files draw their keys from a closed table, so the keys
 * ARE knowable — just not from the call site. Checking the tables directly is
 * what keeps those two rows a classification rather than an exemption.
 */
describe('closed key tables resolve in both bundles', () => {
  it('both greeting tables resolve in uz and ru (greeting.ts)', async () => {
    const { CLIENT_GREETING, WELCOME_GREETING } = await import('@/lib/greeting');
    for (const table of [CLIENT_GREETING, WELCOME_GREETING]) {
      for (const key of [table.named, table.anonymous]) {
        expect(UZ.get(key), `${key} missing in uz`).toBeTruthy();
        expect(RU.get(key), `${key} missing in ru`).toBeTruthy();
      }
    }
  });

  it('every identity state has copy in uz and ru (PhoneVerificationCard)', async () => {
    const { IDENTITY_STATES, identityMessageKey } = await import('@/lib/identityStates');
    const flow = ['idle', 'prompting', 'waiting', 'cancelled', 'unsupported', 'timeout', 'error'];
    for (const state of [...IDENTITY_STATES, ...flow]) {
      const key = identityMessageKey(state as never);
      expect(UZ.get(key), `${key} missing in uz`).toBeTruthy();
      expect(RU.get(key), `${key} missing in ru`).toBeTruthy();
    }
  });

  it('every MENU_DESC_KEY value exists in uz and ru (MenuPage)', () => {
    const keys = Object.values(MENU_DESC_KEY);
    // Imported, not transcribed: a new nav item is covered automatically.
    expect(keys.length).toBeGreaterThanOrEqual(18);
    const missing = keys.filter((k) => !(UZ.has(k) && RU.has(k)));
    expect(missing.sort(), 'menuDesc.* keys missing from a bundle').toEqual([]);
    // `t(descKey, '')` passes '' as the default, so an absent key renders an
    // EMPTY description rather than a raw key — invisible without this check.
    for (const k of keys) {
      expect(UZ.get(k)?.trim(), `${k} is blank in uz`).toBeTruthy();
      expect(RU.get(k)?.trim(), `${k} is blank in ru`).toBeTruthy();
    }
  });

  it('calendarNames still reads exactly the keys asserted here', () => {
    const src = readFileSync(resolve(SRC, 'components/ui/calendarNames.ts'), 'utf8');
    const referenced = [...src.matchAll(/'(calendar\.[A-Za-z]+)'/g)].map((m) => m[1]);
    expect([...new Set(referenced)].sort()).toEqual(CALENDAR_SHAPE.map(([k]) => k).sort());
  });

  it('every calendar list is well-formed in uz and ru (calendarNames)', () => {
    // The production validator, so the test cannot drift from the runtime rule.
    for (const [name, bundle] of [['uz', uz], ['ru', ru]] as const) {
      for (const [key, length] of CALENDAR_SHAPE) {
        const value = key.split('.').reduce<unknown>(
          (node, part) => (node as Record<string, unknown>)?.[part], bundle,
        );
        expect(() => readNameList(value, key, length, name)).not.toThrow();
      }
    }
  });
});
