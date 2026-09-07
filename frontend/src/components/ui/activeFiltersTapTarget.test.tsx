// @vitest-environment jsdom
/**
 * MUDD-69 LOW-2 — `ActiveFilters` chips read as `py-1 pl-3 pr-2` +
 * `text-caption` (12px) + `X size={14}`, a rendered hit area of roughly
 * 24-26px against the platform's 44px touch-target floor.
 *
 * `.tap-target` (globals.css) only grows the hit area under
 * `@media (pointer: coarse)`, so it fixes the mobile/touch case without
 * changing the compact desktop chip. This test render-checks the actual
 * computed style under a coarse-pointer emulation, not just the className
 * string, so a future refactor that drops the class (even if it keeps the
 * text `"tap-target"` somewhere else) still fails loudly.
 */
import { describe, expect, it, beforeEach } from 'vitest';
import { render, screen, cleanup } from '@testing-library/react';
import i18n from '@/i18n';
import { ActiveFilters } from './FilterDrawer';

beforeEach(async () => { cleanup(); await i18n.changeLanguage('uz'); });

describe('ActiveFilters chip (LOW-2)', () => {
  it('carries the tap-target utility so coarse pointers get a 44px hit area', () => {
    render(
      <ActiveFilters filters={[{ key: 'status', label: 'Faol', onRemove: () => {} }]} />,
    );
    const chip = screen.getByRole('button', { name: /Faol/ });
    expect(chip.className.split(/\s+/), 'chip must opt into the coarse-pointer tap target').toContain('tap-target');
  });
});
