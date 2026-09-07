// @vitest-environment jsdom
/**
 * C3-05 — no link or anchor on a reachable surface may contain a button.
 *
 * `<Link><Button/></Link>` renders a `<button>` inside an `<a>`. That is invalid
 * HTML: the parser's own error recovery decides what happens, assistive tech is
 * handed two nested controls for one action, and the keyboard gets two stops
 * where the user expects one. The fix everywhere is the same — style the link
 * itself with `buttonClasses()`.
 *
 * The rendered assertions below cover the shared primitives; the source sweep
 * covers the reachable surfaces this suite does not mount. Deferred (V2-only)
 * pages are excluded: they are unreachable while the scope flag is false, and
 * this pass does not touch them.
 */
import { describe, expect, it } from 'vitest';
import { readFileSync, readdirSync } from 'node:fs';
import { resolve } from 'node:path';
import { V2_SURFACES_ENABLED } from '@/routes/_scope';

const SRC = resolve(__dirname, '../..');

/** Pages behind the V2 scope flag — no route registered, so not reachable. */
const DEFERRED = [
  'routes/marketing/CareersPage.tsx',
  'routes/marketing/FeaturesPage.tsx',
  'routes/marketing/RoadmapPage.tsx',
  'routes/auth/SignupPage.tsx',
];

function sources(): Array<{ rel: string; text: string }> {
  const out: Array<{ rel: string; text: string }> = [];
  const walk = (dir: string) => {
    for (const e of readdirSync(dir, { withFileTypes: true })) {
      const p = resolve(dir, e.name);
      if (e.isDirectory()) { walk(p); continue; }
      if (!/\.tsx$/.test(e.name) || e.name.includes('.test.')) continue;
      const rel = p.slice(SRC.length + 1);
      if (DEFERRED.some((d) => rel.endsWith(d))) continue;
      out.push({ rel, text: readFileSync(p, 'utf8') });
    }
  };
  walk(SRC);
  return out;
}

/**
 * Blank out comments, keeping line numbers intact.
 *
 * Comments discuss markup — "renders telegram_url as an `<a href>`" — and would
 * otherwise be read as markup. `//` is left alone when it follows `:` so a URL
 * scheme is not mistaken for a line comment.
 */
function stripComments(text: string): string {
  return text
    .replace(/\/\*[\s\S]*?\*\//g, (c) => c.replace(/[^\n]/g, ' '))
    .replace(/(^|[^:/])\/\/[^\n]*/g, (m, lead: string) => lead + ' '.repeat(m.length - lead.length));
}

/** `<Link …>` / `<a …>` whose element content contains a `<Button`. */
function nestedButtons(source: string): number[] {
  const text = stripComments(source);
  const lines: number[] = [];
  for (const m of text.matchAll(/<(Link|a)\b[^>]*>/g)) {
    const tag = m[1];
    const rest = text.slice(m.index! + m[0].length);
    const close = rest.indexOf(`</${tag}>`);
    if (close === -1) continue;
    if (/<Button\b/.test(rest.slice(0, close))) {
      lines.push(text.slice(0, m.index).split('\n').length);
    }
  }
  return lines;
}

describe('the deferred-page exclusion is honest', () => {
  it('those pages really are unreachable', () => {
    // If the scope flag is ever flipped on, the exclusion list above stops
    // being justified and this test says so rather than hiding the gap.
    expect(V2_SURFACES_ENABLED,
      'V2 surfaces are reachable now — remove the DEFERRED exclusion and fix those pages too',
    ).toBe(false);
  });
});

describe('no reachable link or anchor wraps a button', () => {
  it('sweeps every reachable component and route', () => {
    const offenders = sources()
      .flatMap(({ rel, text }) => nestedButtons(text).map((line) => `${rel}:${line}`));
    expect(offenders,
      `style the link with buttonClasses() instead:\n${offenders.join('\n')}`).toEqual([]);
  });

  it('specifically covers the retained marketing surfaces', () => {
    for (const rel of ['routes/marketing/LandingPage.tsx', 'routes/marketing/DemoPage.tsx',
      'routes/marketing/ContactPage.tsx', 'components/marketing/PublicLayout.tsx',
      'components/layout/TrialBanner.tsx', 'routes/tenant/BillingPage.tsx']) {
      const text = readFileSync(resolve(SRC, rel), 'utf8');
      expect(nestedButtons(text), `${rel} nests a button in a link`).toEqual([]);
    }
  });
});

describe('the styled links keep what the button gave them', () => {
  const landing = readFileSync(resolve(SRC, 'routes/marketing/LandingPage.tsx'), 'utf8');
  const demo = readFileSync(resolve(SRC, 'routes/marketing/DemoPage.tsx'), 'utf8');

  it('uses the shared class helper rather than re-styling by hand', () => {
    expect(landing).toContain('buttonClasses(');
  });

  it('preserves the variants the CTAs had', () => {
    for (const v of ['inverse', 'outline', 'primary']) {
      expect(landing, `variant ${v} was dropped`).toContain(`variant: '${v}'`);
    }
    expect(landing, 'the full-width pricing CTA lost its block sizing').toContain('block: true');
  });

  it('leaves the demo page with no external hand-off to harden', () => {
    // The demo success panel used to offer a `t.me/…?start=demo_<slug>` anchor
    // that opened a bot which had already provisioned the visitor's shop. Self-
    // serve provisioning is LATER, so the anchor is gone rather than hardened —
    // and if one comes back, it must arrive with `rel`/`target` again.
    const hasExternalAnchor = /<a\s[^>]*href=\{?["']?https?:/.test(demo);
    expect(hasExternalAnchor, 'an external link reappeared on the demo page').toBe(false);
    expect(demo).not.toContain('target="_blank"');
  });
});
