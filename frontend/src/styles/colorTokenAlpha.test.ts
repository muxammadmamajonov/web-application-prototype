/**
 * Every colour token must survive Tailwind's opacity modifier.
 *
 * THE BUG THIS PINS (found on a real device, 2026-08-08). The palette exposed
 * each token as a bare `var(--c-*)` holding a COMPLETE colour — `oklch(...)`,
 * `#ffffff`, `rgba(...)`. Tailwind can only apply `/N` to a colour it can
 * decompose into channels, so it could not take those apart: every modifier
 * compiled to an invalid declaration and the browser fell back to
 * `transparent`.
 *
 * That silently killed 264 utilities across 88 files. The two that mattered:
 *
 *   - `bg-ink/50` and `bg-ink/40` are the ONLY scrim on every modal and detail
 *     drawer. With them transparent the backdrop had no dim at all — the sheet
 *     floated over fully-lit content with nothing but a blur behind it.
 *   - all 60 `hover:bg-ink/5` and the `hover:bg-ink/[0.03]` row hovers did
 *     nothing, so the sidebar and every list row had NO hover state.
 *
 * It is invisible in code review and in CI: the class names are correct,
 * Tailwind emits no warning, and no test asserted a computed colour. It only
 * shows up as `rgba(0, 0, 0, 0)` in the browser's computed style.
 *
 * The fix wraps each token in `color-mix(… calc(<alpha-value> * 100%) …)`.
 * Tailwind substitutes `<alpha-value>` with the modifier's decimal, or with `1`
 * when there is none — so a bare `bg-ink` still resolves to the solid token.
 * This test fails if any token goes back to a bare `var()`, which is the exact
 * edit that would silently re-break every scrim and hover in the product.
 */
import { describe, expect, it } from 'vitest';
import config from '../../tailwind.config.js';

/** Walk the nested colour object into `[path, value]` leaves. */
function leaves(obj: unknown, path: string[] = []): Array<[string, string]> {
  if (typeof obj === 'string') return [[path.join('.'), obj]];
  if (!obj || typeof obj !== 'object') return [];
  return Object.entries(obj as Record<string, unknown>)
    .flatMap(([k, v]) => leaves(v, [...path, k]));
}

// eslint-disable-next-line @typescript-eslint/no-explicit-any
const COLORS = leaves((config as any).theme?.extend?.colors);

describe('the palette is reachable at all', () => {
  it('resolves a meaningful number of tokens', () => {
    // Guards the test itself: if the config shape moves, the assertions below
    // would vacuously pass over an empty list.
    expect(COLORS.length).toBeGreaterThan(15);
  });

  it('still exposes the tokens the app names most', () => {
    const names = COLORS.map(([k]) => k);
    for (const key of ['ink', 'cream', 'surface', 'line', 'accent.DEFAULT', 'danger.DEFAULT']) {
      expect(names, `token "${key}" disappeared from the palette`).toContain(key);
    }
  });
});

describe('every token accepts an opacity modifier', () => {
  it('declares <alpha-value>, so `/N` is not silently dropped', () => {
    const broken = COLORS
      .filter(([, v]) => !v.includes('<alpha-value>'))
      .map(([k, v]) => `${k}: ${v}`);
    expect(broken,
      `these tokens cannot take an opacity modifier:\n${broken.join('\n')}\n` +
      'A bare `var(--c-*)` holding a complete colour makes `bg-<token>/50` ' +
      'compile to an invalid declaration that falls back to TRANSPARENT — ' +
      'which is how every modal scrim and every hover state in the app went ' +
      'blank. Wrap it with the `alpha()` helper in tailwind.config.js.',
    ).toEqual([]);
  });

  it('keeps the token itself unchanged — this is plumbing, not a retune', () => {
    // `alpha()` must WRAP the existing var, never replace the value. If a token
    // stopped referencing its `--c-*` custom property the palette would have
    // been silently redefined here rather than in globals.css.
    const detached = COLORS
      .filter(([, v]) => !v.includes('var(--c-'))
      .map(([k, v]) => `${k}: ${v}`);
    expect(detached,
      `these tokens no longer reference a --c-* custom property:\n${detached.join('\n')}`,
    ).toEqual([]);
  });

  it('resolves to the solid colour when no modifier is used', () => {
    // `calc(<alpha-value> * 100%)` with Tailwind's no-modifier substitution of
    // `1` must come out as 100% — i.e. plain `bg-ink` is still fully opaque.
    const ink = COLORS.find(([k]) => k === 'ink')?.[1] ?? '';
    expect(ink).toContain('calc(<alpha-value> * 100%)');
    expect(ink.replace('<alpha-value>', '1')).toContain('calc(1 * 100%)');
  });
});
