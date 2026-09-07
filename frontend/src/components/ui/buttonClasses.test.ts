/**
 * Every button keeps its foreground colour, at every size.
 *
 * `buttonClasses` composes the variant (which carries the colour) before the
 * size (which carries a type-scale step). While the scale was unregistered with
 * tailwind-merge, the step was classified as a colour and evicted the variant's
 * — silently, and only for the sizes whose step collided. The `inverse` CTA on
 * the landing-page hero rendered `text-ink` on `bg-ink`: invisible.
 *
 * Colour is asserted here rather than in `cn.test.ts` because this is the
 * contract callers depend on: a variant means a legible pairing, whatever size
 * it is rendered at.
 */
import { describe, expect, it } from 'vitest';
import { buttonClasses, type Size, type Variant } from './buttonClasses';

/** The foreground each variant promises. */
const FOREGROUND: Record<Variant, string> = {
  primary: 'text-accent-ink',
  inverse: 'text-white',
  ghost: 'text-ink',
  soft: 'text-ink',
  outline: 'text-ink',
  danger: 'text-white',
  telegram: 'text-white',
};

/** The type-scale step each size applies — the class that used to win. */
const STEP: Record<Size, string> = {
  sm: 'text-small',
  md: 'text-body',
  lg: 'text-subsection',
};

const VARIANTS = Object.keys(FOREGROUND) as Variant[];
const SIZES = Object.keys(STEP) as Size[];
const COMBOS = VARIANTS.flatMap((variant) => SIZES.map((size) => [variant, size] as const));

describe.each(COMBOS)('%s / %s', (variant, size) => {
  const classes = buttonClasses({ variant, size }).split(/\s+/);

  it('keeps its foreground colour', () => {
    expect(classes).toContain(FOREGROUND[variant]);
  });

  it('keeps its type-scale step', () => {
    expect(classes).toContain(STEP[size]);
  });

  it('carries exactly one type-scale step', () => {
    const steps = classes.filter((c) => Object.values(STEP).includes(c));
    expect(steps).toEqual([STEP[size]]);
  });
});

describe('a caller can still override what it means to override', () => {
  it('lets className win on colour', () => {
    const classes = buttonClasses({ variant: 'inverse', size: 'lg', className: 'text-danger' })
      .split(/\s+/);
    expect(classes).toContain('text-danger');
    expect(classes).not.toContain('text-white');
  });

  it('lets className win on the type-scale step', () => {
    const classes = buttonClasses({ variant: 'inverse', size: 'lg', className: 'text-body' })
      .split(/\s+/);
    expect(classes).toContain('text-body');
    expect(classes).not.toContain('text-subsection');
    // …without taking the colour down with it.
    expect(classes).toContain('text-white');
  });

  it('still applies block sizing', () => {
    expect(buttonClasses({ block: true })).toContain('w-full');
  });
});
