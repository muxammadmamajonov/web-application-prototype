// @vitest-environment jsdom
import { afterEach, describe, expect, it } from 'vitest';
import { cleanup, render } from '@testing-library/react';
import { Button } from './Button';
import { buttonClasses, type Size } from './buttonClasses';

/**
 * The shared button's touch-target floor (Batch 6).
 *
 * THE FINDING. `Button size="md"` is `h-10` — 40px — and carried no
 * `.tap-target`, so on a coarse pointer every primary action that nobody had
 * remembered to adapt was a 40px target, under WCAG 2.5.5's 44px minimum. It
 * was raised in Batch 3, restated in Batch 4, and left open both times because
 * changing the shared control is a design-system decision rather than a
 * correction-pass one.
 *
 * THE FIX is in the primitive, not in the screens: `.tap-target` is part of the
 * `sm` and `md` size classes. That inverts the default — a control is compliant
 * unless somebody opts out, instead of compliant only when somebody opts in.
 *
 * WHAT IS DELIBERATELY NOT DONE. The visual heights are unchanged: 36/40/48px on
 * a fine pointer, because 40px IS the design system's md control and desktop
 * density depends on it. `.tap-target` lives inside `@media (pointer: coarse)`
 * (globals.css), so it raises the hit area on a phone and does nothing at all on
 * a mouse. And `lg` is excluded because it is already 48px — this is a minimum,
 * not a uniform size, and a control whose design height is intentionally larger
 * keeps it.
 */
describe('the shared button meets the touch-target minimum', () => {
  // This suite renders several buttons; without cleanup they accumulate in
  // document.body and every role query finds all of them.
  afterEach(cleanup);

  it('gives every compact size the coarse-pointer floor', () => {
    for (const size of ['sm', 'md'] as Size[]) {
      expect(buttonClasses({ size })).toContain('tap-target');
    }
  });

  it('does not add a 44px floor to a control that is already 48px', () => {
    // `lg` is h-12. Forcing a minimum below its own height would be noise, and
    // asserting it here would let someone "fix" the noise by shrinking lg.
    expect(buttonClasses({ size: 'lg' })).toContain('h-12');
    expect(buttonClasses({ size: 'lg' })).not.toContain('tap-target');
  });

  it('keeps the design system heights on a fine pointer', () => {
    // The fix must not have quietly become "make everything 44px".
    expect(buttonClasses({ size: 'sm' })).toContain('h-9');
    expect(buttonClasses({ size: 'md' })).toContain('h-10');
    expect(buttonClasses({ size: 'lg' })).toContain('h-12');
  });

  it('applies to the default button, which is the one nobody configures', () => {
    const { getByRole } = render(<Button>Davom etish</Button>);
    expect(getByRole('button').className).toContain('tap-target');
  });

  it('survives a caller passing their own className', () => {
    // `cn` merges Tailwind conflicts; `tap-target` is a plain class and must not
    // be dropped when a screen adds its own layout classes.
    const { getByRole } = render(<Button className="mt-4 w-full">Saqlash</Button>);
    const el = getByRole('button');
    expect(el.className).toContain('tap-target');
    expect(el.className).toContain('mt-4');
  });

  it('applies to every variant, since the floor is about size not style', () => {
    for (const variant of ['primary', 'ghost', 'soft', 'inverse', 'outline', 'danger', 'telegram'] as const) {
      expect(buttonClasses({ variant })).toContain('tap-target');
    }
  });

  it('applies to a link styled as a button', () => {
    // A control that NAVIGATES renders an <a>, styled with buttonClasses. It is
    // tapped by the same finger, so it needs the same floor.
    expect(buttonClasses({ variant: 'primary', size: 'md', block: true }))
      .toContain('tap-target');
  });

  it('keeps loading and disabled states on the same target', () => {
    const { getByRole } = render(<Button loading>Yuborilmoqda</Button>);
    const el = getByRole('button');
    expect(el.className).toContain('tap-target');
    // jest-dom matchers are not installed in this project — assert the DOM
    // directly rather than adding a dependency for one line.
    expect((el as HTMLButtonElement).disabled).toBe(true);
    expect(el.getAttribute('aria-busy')).toBe('true');
  });
});
