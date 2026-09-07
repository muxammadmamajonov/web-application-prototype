/**
 * The design system's mechanically enforceable invariants.
 *
 * The token layer is real but source had drifted away from it: two typographic
 * systems with conflicting ranges, a radius token used once against 180 uses of
 * a raw class in its place, three focus-ring opacities, 18 icon sizes, and hex
 * fallbacks inside `className`. Each of those is a *countable* property of the
 * source, so each gets a guard here rather than a promise to remember.
 *
 * Scope: every check runs over RETAINED source only. `EXCLUDED` lists the
 * surfaces that cannot be reached while `V2_SURFACES_ENABLED` is false — the
 * gated pages plus the files reachable only through them or through the
 * unrouted `routes/tenant/ProductsPage.tsx`. Those files deliberately still
 * carry the older vocabulary and must not be edited to satisfy a guard.
 *
 * The list is explicit on purpose. Import reachability was proven separately
 * and recorded in `docs/audits/PHASE-4-UI-IMPLEMENTATION.md`; re-deriving it
 * here would put a module resolver and a graph traversal inside a unit test to
 * restate a fact that changes only when a route is added. What this file does
 * enforce is that the list stays honest: every entry must exist on disk, and
 * there must be no duplicates.
 */
import { describe, expect, it } from 'vitest';
import { readdirSync, readFileSync, existsSync } from 'node:fs';
import { resolve } from 'node:path';

const SRC = resolve(__dirname, '..');
const CSS = readFileSync(resolve(SRC, 'styles/globals.css'), 'utf8');

/** V2_SURFACES_ENABLED-gated pages, read off `routes/router.tsx`. */
const DEFERRED_PAGES = [
  'routes/platform/OverviewPage.tsx', 'routes/platform/UsersPage.tsx',
  'routes/platform/BroadcastsPage.tsx', 'routes/platform/CatalogPage.tsx',
  'routes/platform/AdsPage.tsx', 'routes/platform/PromosPage.tsx',
  'routes/platform/ReferralsPage.tsx', 'routes/tenant/BroadcastsPage.tsx',
  'routes/client/PayoffPage.tsx', 'routes/client/WelcomePage.tsx',
  'routes/marketing/FeaturesPage.tsx', 'routes/marketing/CareersPage.tsx',
  'routes/marketing/RoadmapPage.tsx', 'routes/auth/SignupPage.tsx',
  'components/layout/TrialBanner.tsx',
];

/**
 * Files reachable ONLY through a gated page, or only through the unrouted
 * `routes/tenant/ProductsPage.tsx`. Each is listed with what reaches it.
 */
const DEFERRED_ONLY = [
  'components/ui/BusinessTypePicker.tsx',            // ← CategorySchemaModal ← CatalogPage
  'components/ui/CategoryIcon.tsx',                  // ← CatalogPage, ProductPickerModal
  'components/ui/ChartEmptyState.tsx',               // ← platform/OverviewPage
  'components/ui/EmojiPicker.tsx',                   // ← CategorySchemaModal ← CatalogPage
  'components/ui/MultiSelect.tsx',                   // ← CatalogPage
  'components/ui/ProductThumb.tsx',                  // ← ProductsPage (unrouted), ProductPickerModal
  'components/ui/Wizard.tsx',                        // ← CatalogPage, CategorySchemaModal
  'routes/platform/_modals/CategorySchemaModal.tsx', // ← CatalogPage
  'routes/platform/_modals/CreateAdModal.tsx',       // ← AdsPage
  'routes/platform/_modals/CreatePromoModal.tsx',    // ← PromosPage
  'routes/tenant/_modals/ProductModal.tsx',          // ← ProductsPage (unrouted)
  'routes/tenant/_modals/ProductPickerModal.tsx',    // ← ProductModal ← ProductsPage
  'routes/tenant/_modals/StockModal.tsx',            // ← ProductModal ← ProductsPage
];

const EXCLUDED = [...DEFERRED_PAGES, ...DEFERRED_ONLY];
const EXCLUDED_SET = new Set(EXCLUDED);

/** Every retained `.ts`/`.tsx` source file, as `[relativePath, contents]`. */
function retainedFiles(): [string, string][] {
  const out: [string, string][] = [];
  const walk = (dir: string) => {
    for (const e of readdirSync(dir, { withFileTypes: true })) {
      const p = resolve(dir, e.name);
      if (e.isDirectory()) { walk(p); continue; }
      if (!/\.tsx?$/.test(e.name) || e.name.includes('.test.')) continue;
      const rel = p.slice(SRC.length + 1);
      if (EXCLUDED_SET.has(rel)) continue;
      out.push([rel, readFileSync(p, 'utf8')]);
    }
  };
  walk(SRC);
  return out;
}

/** Report every `file:line` whose text matches `bad`. */
function offenders(bad: RegExp): string[] {
  const hits: string[] = [];
  for (const [rel, src] of retainedFiles()) {
    src.split('\n').forEach((line, i) => {
      if (bad.test(line)) hits.push(`${rel}:${i + 1}  ${line.trim().slice(0, 100)}`);
    });
  }
  return hits;
}

const fail = (what: string, hits: string[]) => `${what}\n${hits.join('\n')}`;

describe('the exclusion set is honest', () => {
  it('every excluded path exists and none is listed twice', () => {
    const missing = EXCLUDED.filter((f) => !existsSync(resolve(SRC, f)));
    expect(missing, `a stale exclusion silently shrinks every guard below:\n${missing.join('\n')}`).toEqual([]);
    expect(EXCLUDED_SET.size, 'duplicate entries hide a mistake').toBe(EXCLUDED.length);
  });
});

describe('UI-001 — one authoritative type scale', () => {
  // The `.h-*` classes used to carry their own clamps, which is how the product
  // ended up with two different `display` ramps under one name.
  const CSS_LINES = CSS.split('\n');
  const fontSizeDecls = CSS_LINES
    .map((l, i) => [l, i] as const)
    .filter(([l]) => /(^|[^-])font-size\s*:/.test(l));

  it('globals.css declares exactly one font-size', () => {
    const shown = fontSizeDecls.map(([l, i]) => `globals.css:${i + 1}  ${l.trim()}`);
    expect(shown, fail('name the size in tailwind.config.js fontSize and @apply it:', shown)).toHaveLength(1);
  });

  it('and that one is the iOS input-zoom safeguard, not a scale step', () => {
    // Pinned to the *rule*, not merely to the string "16px": a stray
    // `font-size: 16px` anywhere else in the file must still fail.
    const [line, idx] = fontSizeDecls[0];
    expect(line.trim()).toBe('font-size: 16px;');
    const enclosing = CSS_LINES.slice(Math.max(0, idx - 6), idx).join('\n');
    expect(enclosing, 'must sit in the small-screen rule').toMatch(/@media\s*\(\s*max-width:\s*767px\s*\)/);
    expect(enclosing, 'must apply to text-entry controls').toMatch(/\binput\b/);
    expect(enclosing, 'must apply to text-entry controls').toMatch(/\btextarea\b/);
  });

  it('no design-scale font-size survives outside tailwind.config.js', () => {
    const scaleDecls = fontSizeDecls.filter(([, i]) => {
      const enclosing = CSS_LINES.slice(Math.max(0, i - 6), i).join('\n');
      return !/@media\s*\(\s*max-width:\s*767px\s*\)/.test(enclosing);
    }).map(([l, i]) => `globals.css:${i + 1}  ${l.trim()}`);
    expect(scaleDecls, fail('heading/nav sizes belong in the scale:', scaleDecls)).toEqual([]);
  });

  it('every .h-* heading class resolves through the scale', () => {
    const bad: string[] = [];
    for (const m of CSS.matchAll(/^\s*\.(h-[\w-]+)\s*\{([^}]*)\}/gm)) {
      const [, name, body] = m;
      if (!/@apply[^;]*\btext-[\w-]+/.test(body)) bad.push(`.${name} — no @apply text-<token>`);
    }
    expect(bad, fail('heading classes must compose from the scale:', bad)).toEqual([]);
  });
});

describe('UI-002 / UI-011 — the scale is actually used', () => {
  it('no raw Tailwind size class survives in retained source', () => {
    const hits = offenders(/\btext-(xs|sm|base|lg|xl|2xl|3xl|4xl|5xl|6xl)\b/);
    expect(hits, fail('use a scale token (text-caption/body/subsection/section/title/…):', hits)).toEqual([]);
  });

  it('no arbitrary type value survives — px, rem, em or clamp', () => {
    // `text-[color:var(--x)]` is a colour reference, not a size, and is allowed.
    const hits = offenders(/\btext-\[\s*(?!color:)[^\]]*(\d(px|rem|em)|clamp\()/);
    expect(hits, fail('name it in the scale instead:', hits)).toEqual([]);
  });
});

describe('UI-008 — the design system does not opt out of its own scale', () => {
  it('globals.css uses no text-[Npx]', () => {
    const bad = CSS.split('\n').map((l, i) => [l, i + 1] as const)
      .filter(([l]) => /text-\[\s*[\d.]+(px|rem|em)/.test(l))
      .map(([l, n]) => `globals.css:${n}  ${l.trim()}`);
    expect(bad, fail('.kicker/.pill must @apply a token:', bad)).toEqual([]);
  });
});

describe('UI-005 — one radius vocabulary', () => {
  it('no raw radius step in retained source', () => {
    const hits = offenders(/\brounded-(xs|sm|md|lg|xl|2xl|3xl|full)\b/);
    expect(hits, fail('use rounded-card / control / icon / micro / pill:', hits)).toEqual([]);
  });

  it('no bare `rounded` — Tailwind\'s undocumented 4px default', () => {
    // Scanned inside string literals only: `ProductThumb` has a prop genuinely
    // *named* `rounded`, and a naive line regex would flag that identifier.
    const hits: string[] = [];
    for (const [rel, src] of retainedFiles()) {
      src.split('\n').forEach((line, i) => {
        for (const lit of line.matchAll(/'([^']*)'|"([^"]*)"/g)) {
          const body = lit[1] ?? lit[2] ?? '';
          if (body.split(/\s+/).includes('rounded')) hits.push(`${rel}:${i + 1}  ${body.slice(0, 70)}`);
        }
      });
    }
    expect(hits, fail('use rounded-micro (4px) for checkboxes and kbd:', hits)).toEqual([]);
  });
});

describe('UI-014 — one elevation vocabulary', () => {
  it('no raw or arbitrary box-shadow in retained source', () => {
    // `drop-shadow-*` is a filter, a different property with no token family.
    const hits = offenders(/(?<!drop-)\bshadow-(sm|md|lg|xl|2xl)\b|(?<!drop-)\bshadow-\[/);
    expect(hits, fail('use shadow-card / shadow-pop / shadow-header:', hits)).toEqual([]);
  });
});

describe('UI-007 — one focus-ring strength', () => {
  it('every ring-ink opacity is the same', () => {
    const seen = new Set<string>();
    for (const [, src] of retainedFiles()) {
      for (const m of src.matchAll(/ring-ink\/(\d+)/g)) seen.add(m[1]);
    }
    expect([...seen].sort(), 'one affordance, one opacity').toEqual(['20']);
  });
});

describe('UI-006 — icon sizes come from a documented set', () => {
  // Control icons: 12 dense · 14 inline · 16 default · 18 nav · 20 emphasis ·
  // 24 feature. Anything >=28 is an illustration dimension chosen per
  // composition, not a control-scale step.
  const CONTROL = new Set([12, 14, 16, 18, 20, 24]);
  it('no off-scale control icon size', () => {
    const hits: string[] = [];
    for (const [rel, src] of retainedFiles()) {
      src.split('\n').forEach((line, i) => {
        for (const m of line.matchAll(/size=\{(\d+)\}/g)) {
          const n = Number(m[1]);
          if (n < 28 && !CONTROL.has(n)) hits.push(`${rel}:${i + 1}  size={${n}}`);
        }
      });
    }
    expect(hits, fail('use 12/14/16/18/20/24, or >=28 for an illustration:', hits)).toEqual([]);
  });
});

describe('UI-010 — no raw hex in a utility class', () => {
  // Anchoring on the `-[…]` arbitrary-value bracket rather than on the word
  // `className`: the original offenders lived on bare string lines inside a
  // `cn(…)` array, where `className` is several lines above. Hex used as DATA
  // (chart ramps, avatar palettes, catalog seeds) is untouched by this, because
  // it never appears inside a Tailwind arbitrary value.
  const HEX_IN_UTILITY = /[\w-]-\[[^\]]*#[0-9a-fA-F]{3,8}/;
  it('no utility class embeds a hex literal or hex fallback', () => {
    const hits = offenders(HEX_IN_UTILITY);
    expect(hits, fail('use the semantic alias (bg-success-soft …):', hits)).toEqual([]);
  });

  it('and none hides on a className line either', () => {
    const hits = offenders(/className=.*#[0-9a-fA-F]{3,8}\b/);
    expect(hits, fail('use a token:', hits)).toEqual([]);
  });
});

describe('UI-009 — width intents are named', () => {
  it('the ad-hoc caps that had a named intent are gone', () => {
    const hits = offenders(/\bmax-w-(md|2xl|sm)\b|max-w-\[560px\]/);
    expect(hits, fail('use max-w-content / lead / panel / narrow / hero-col:', hits)).toEqual([]);
  });
});

describe('UI-012 — the public pages share one section rhythm', () => {
  const PUBLIC = [
    'routes/marketing/LandingPage.tsx', 'routes/marketing/ContactPage.tsx',
    'routes/marketing/DemoPage.tsx', 'routes/marketing/legal/LegalPage.tsx',
  ];
  it('every public <section> band uses a named role', () => {
    const hits: string[] = [];
    for (const rel of PUBLIC) {
      readFileSync(resolve(SRC, rel), 'utf8').split('\n').forEach((line, i) => {
        if (/<section\b/.test(line) && /\bpy-(1[0-9]|[2-9][0-9])\b/.test(line)) {
          hits.push(`${rel}:${i + 1}  ${line.trim().slice(0, 90)}`);
        }
      });
    }
    expect(hits, fail('use .section-hero / .section-band / .section-band-lg:', hits)).toEqual([]);
  });

  it('the three roles are defined exactly once each', () => {
    for (const role of ['section-hero', 'section-band', 'section-band-lg']) {
      expect(CSS.match(new RegExp(`\\.${role}\\s*\\{`, 'g'))?.length, `.${role}`).toBe(1);
    }
  });
});
