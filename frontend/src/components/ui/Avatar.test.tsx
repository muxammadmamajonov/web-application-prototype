// @vitest-environment jsdom
/**
 * The initials fallback must stay legible on its swatch.
 *
 * `Avatar` composes the tone (`bg-[…] text-white`) before the size (which
 * carries a type-scale step), so it had the same defect as `buttonClasses`:
 * while the scale was unregistered with tailwind-merge, the step counted as a
 * colour and evicted `text-white`, leaving dark initials on a saturated fill.
 *
 * Asserted across every size because only the sizes carrying a step could
 * trigger it — which is all of them, and a new size must not reintroduce it.
 */
import { describe, expect, it, afterEach } from 'vitest';
import { render, screen, cleanup } from '@testing-library/react';
import { Avatar } from './Avatar';

const SIZES = ['xs', 'sm', 'md', 'lg'] as const;
const STEP: Record<(typeof SIZES)[number], string> = {
  xs: 'text-micro',
  sm: 'text-caption',
  md: 'text-body',
  lg: 'text-subsection',
};

afterEach(cleanup);

describe.each(SIZES)('initials at size %s', (size) => {
  it('keep the tone foreground and the type-scale step', () => {
    render(<Avatar name="Sardor Mamatov" size={size} />);
    const classes = screen.getByLabelText('Sardor Mamatov').className.split(/\s+/);
    expect(classes).toContain('text-white');
    expect(classes).toContain(STEP[size]);
    expect(classes.some((c) => c.startsWith('bg-[color:var(--c-avatar-'))).toBe(true);
  });
});
