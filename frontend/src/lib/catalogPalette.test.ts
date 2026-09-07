/**
 * The colour palette is a catalogue, not a constant.
 *
 * THE BUG THIS PINS (found by sweeping for hardcoded lists that shadow live
 * catalogues, 2026-08-09). Colours live in `catalog_colors` — HQ-editable, 106
 * active rows in production — and are served by GET /catalog/colors. The
 * category builder nevertheless previewed "the global palette" from a 19-entry
 * literal in catalogSchema.ts.
 *
 * It had drifted in BOTH content and value:
 *
 *   - 19 entries against ~106, so its "+4 more" understated the palette by
 *     roughly ninety colours;
 *   - and its own hex values — `#1c1f29` for black where the catalogue says
 *     `#000000`, `#c7ccd1` for silver against `#C0C0C0`.
 *
 * A swatch's entire content IS its colour, so the control meant to show which
 * colours an axis offers was showing different colours than the axis offers.
 * That also rules out keeping the literal as a fallback: rendering it when the
 * API is unavailable would state the one thing it cannot state truthfully.
 *
 * Nothing about this fails loudly — the swatches render, they are simply wrong
 * — and the surface is currently behind the MVP scope flag, so no test that
 * exercises the UI would have caught it either.
 */
import { describe, expect, it } from 'vitest';
import { readdirSync, readFileSync } from 'node:fs';
import { resolve } from 'node:path';

const SRC = resolve(__dirname, '..');

function sourceFiles(dir = SRC, prefix = ''): Array<{ rel: string; text: string }> {
  const out: Array<{ rel: string; text: string }> = [];
  for (const e of readdirSync(dir, { withFileTypes: true })) {
    const rel = prefix ? `${prefix}/${e.name}` : e.name;
    if (e.isDirectory()) { out.push(...sourceFiles(resolve(dir, e.name), rel)); continue; }
    if (!/\.tsx?$/.test(e.name) || e.name.includes('.test.')) continue;
    out.push({ rel, text: readFileSync(resolve(dir, e.name), 'utf8') });
  }
  return out;
}

const FILES = sourceFiles();

describe('the sweep reaches the source', () => {
  it('scans a meaningful tree', () => {
    expect(FILES.length).toBeGreaterThan(100);
  });
});

describe('nothing re-declares the colour catalogue', () => {
  it('no source file carries a palette literal', () => {
    // The shape of the defect: several product-colour names, each paired with
    // its own hex, sitting in a source file. Design TOKENS are not this — they
    // live in globals.css/tailwind.config.js (outside this tree) and are named
    // by role (accent, danger), not by colour.
    const COLOUR_NAME = /\b(black|white|silver|gray|grey|gold|navy|beige|graphite|titanium)\b\s*['"]?\s*[,:]/gi;
    const HEX = /#[0-9a-f]{6}\b/gi;

    // `devMock.ts` is the one legitimate place a colour list may appear: it
    // exists to STAND IN for API responses, so a palette there represents the
    // catalogue rather than shadowing it — and it is dev-only, tree-shaken out
    // of any production build. The exemption is deliberately this one file;
    // widening it to "anything mock-ish" would re-open the hole.
    const CATALOGUE_STAND_INS = ['lib/devMock.ts'];

    const offenders: string[] = [];
    for (const { rel, text } of FILES) {
      if (CATALOGUE_STAND_INS.includes(rel)) continue;
      const names = new Set((text.match(COLOUR_NAME) ?? []).map((s) => s.toLowerCase()));
      const hexes = (text.match(HEX) ?? []).length;
      // Three or more product-colour names AND a comparable pile of hexes is a
      // palette; one or two is a coincidence.
      if (names.size >= 3 && hexes >= 3) offenders.push(`${rel}  (${names.size} colour names, ${hexes} hex values)`);
    }

    expect(offenders,
      `these look like a copy of the colour catalogue:\n${offenders.join('\n')}\n` +
      'Colours are rows in `catalog_colors`, editable by HQ and served by ' +
      'GET /catalog/colors — read them with useCatalogColors(). A literal ' +
      'copy cannot be kept honest: the last one drifted to a seventh of the ' +
      'real palette and disagreed with it on the actual hex values.',
    ).toEqual([]);
  });

  it('the category builder reads the palette from the API', () => {
    const rel = 'routes/platform/_modals/CategorySchemaModal.tsx';
    const text = FILES.find((f) => f.rel === rel)?.text ?? '';
    expect(text, `${rel} not found — re-point this test`).toBeTruthy();
    expect(text, 'the colour preview must come from the catalogue').toContain('useCatalogColors');
  });

  it('catalogSchema exports no colour pool', () => {
    const text = FILES.find((f) => f.rel === 'lib/catalogSchema.ts')?.text ?? '';
    expect(text).toBeTruthy();
    expect(text).not.toMatch(/export const COLOR_POOL/);
  });
});
