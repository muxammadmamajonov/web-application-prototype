/**
 * `cn` must not let a type-scale step eat a colour.
 *
 * The product's font sizes are named for their role (`text-body`,
 * `text-subsection`) rather than for a t-shirt size, so stock tailwind-merge
 * cannot recognise them as font sizes: its `font-size` group matches only
 * `text-base`, t-shirt sizes and arbitrary lengths, and everything else under
 * the `text-` prefix falls through to `text-color`, whose validator accepts
 * anything. `text-subsection` was therefore classified as a COLOUR, and merging
 * `'bg-ink text-white'` with `'… text-subsection'` dropped `text-white` —
 * rendering the landing page's primary CTA as dark text on a dark fill.
 *
 * The fix registers the scale with tailwind-merge. These tests pin both halves:
 * the registration works, and the registered list has not drifted from the
 * config that defines the scale.
 */
import { describe, expect, it } from 'vitest';
import { cn, TYPE_SCALE_STEPS } from './cn';
import tailwindConfig from '../../tailwind.config.js';

describe('the type scale is registered with tailwind-merge', () => {
  it('matches the scale in tailwind.config.js exactly', () => {
    // tailwind.config.js calls itself "the single authoritative source for
    // every font size in the product", so a step added there must be added
    // here too or it silently starts eating colours again.
    const configured = Object.keys(
      (tailwindConfig as { theme: { extend: { fontSize: Record<string, unknown> } } })
        .theme.extend.fontSize,
    );
    expect([...TYPE_SCALE_STEPS].sort()).toEqual(configured.sort());
  });

  it.each([...TYPE_SCALE_STEPS])('text-%s does not evict a colour', (step) => {
    expect(cn('text-white', `text-${step}`)).toContain('text-white');
    expect(cn('text-white', `text-${step}`)).toContain(`text-${step}`);
  });

  it.each([...TYPE_SCALE_STEPS])('a colour does not evict text-%s', (step) => {
    // Order-independent: composition order is an implementation detail of the
    // caller, and neither order may lose information.
    expect(cn(`text-${step}`, 'text-ink')).toContain(`text-${step}`);
    expect(cn(`text-${step}`, 'text-ink')).toContain('text-ink');
  });
});

describe('cn still merges what it is supposed to merge', () => {
  it('keeps the last of two conflicting type-scale steps', () => {
    expect(cn('text-body', 'text-subsection')).toBe('text-subsection');
  });

  it('keeps the last of two conflicting colours', () => {
    expect(cn('text-ink', 'text-white')).toBe('text-white');
  });

  it('still collapses ordinary conflicts', () => {
    expect(cn('px-3', 'px-5')).toBe('px-5');
    expect(cn('bg-ink', 'bg-cream')).toBe('bg-cream');
  });

  it('leaves unrelated classes alone', () => {
    expect(cn('inline-flex', 'h-12', 'text-subsection', 'text-white'))
      .toBe('inline-flex h-12 text-subsection text-white');
  });
});
