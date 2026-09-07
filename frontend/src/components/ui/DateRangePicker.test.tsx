// @vitest-environment jsdom
/**
 * F4-01 — the range picker must be as keyboard-operable as the single picker.
 *
 * C3-04 reported "date controls" corrected, but only `DatePicker` got the
 * date-grid model. `DateRangePicker` — used for report ranges and audit filters
 * — still had: no focus restoration on Escape or close, no grid semantics at
 * all, every day cell in the Tab sequence, and no arrow/Home/End navigation.
 * Audit A11Y-10 names both components, so half the finding was open.
 *
 * These tests drive real keyboard events. Range selection and the visual design
 * are unchanged and pinned here so the accessibility work cannot alter them.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, cleanup, fireEvent } from '@testing-library/react';
import '@testing-library/jest-dom/vitest';

// Echo the string default for ordinary keys (deterministic aria-labels without
// booting real i18next under jsdom), but resolve `calendar.*` from the REAL uz
// bundle — those arrays now live only there, so the fixture must not restate
// them or the test would stop proving the bundle is the source.
vi.mock('react-i18next', async () => {
  const uz = (await import('@/i18n/locales/uz.json')).default as Record<string, unknown>;
  const resolve = (key: string): unknown =>
    key.split('.').reduce<unknown>((a, p) => (a as Record<string, unknown>)?.[p], uz);
  return {
    useTranslation: () => ({
      t: (key: string, def?: unknown) =>
        (key.startsWith('calendar.') ? resolve(key) : (typeof def === 'string' ? def : key)),
      i18n: { language: 'uz' },
    }),
  };
});

import { DateRangePicker } from './DateRangePicker';

const FROM = '2026-03-05';
const TO = '2026-03-12';

function open(props: Partial<Parameters<typeof DateRangePicker>[0]> = {}) {
  const onChange = vi.fn();
  render(<DateRangePicker from={FROM} to={TO} onChange={onChange} {...props} />);
  const trigger = screen.getAllByRole('button')[0];
  fireEvent.click(trigger);
  return { onChange, trigger };
}
const dayCells = () => screen.getAllByRole('gridcell')
  .filter((n): n is HTMLButtonElement => n.tagName === 'BUTTON');
const tabbable = () => dayCells().filter((b) => b.tabIndex === 0);
const dayNamed = (d: number) => dayCells().find((b) => Number(b.dataset.day) === d)!;

beforeEach(() => {
  if (!Element.prototype.scrollIntoView) Element.prototype.scrollIntoView = () => {};
});
afterEach(cleanup);

describe('grid semantics', () => {
  it('declares a named grid', () => {
    open();
    expect(screen.getByRole('grid')).toBeInTheDocument();
  });

  it('exposes seven column headers inside the grid', () => {
    open();
    const headers = screen.getAllByRole('columnheader');
    expect(headers).toHaveLength(7);
    for (const h of headers) expect(screen.getByRole('grid').contains(h)).toBe(true);
  });

  it('keeps rows between the grid and its cells', () => {
    open();
    const rows = screen.getAllByRole('row');
    expect(rows.length).toBeGreaterThan(1);
    for (const cell of screen.getAllByRole('gridcell')) {
      expect(rows.some((r) => r.contains(cell)), 'every cell needs a row parent').toBe(true);
    }
  });
});

describe('the roving tab stop', () => {
  it('puts exactly one selectable day in the Tab sequence', () => {
    open();
    const stops = tabbable();
    expect(stops).toHaveLength(1);
    expect(stops[0]).not.toBeDisabled();
  });

  it('prefers the range start when it is selectable', () => {
    open();
    expect(tabbable()[0].dataset.day).toBe('5');
  });

  it('never parks on a disabled day', () => {
    // max before the range start ⇒ day 5 is disabled.
    open({ max: '2026-03-03' });
    const stops = tabbable();
    expect(stops).toHaveLength(1);
    expect(stops[0]).not.toBeDisabled();
    expect(Number(stops[0].dataset.day)).toBeLessThanOrEqual(3);
  });

  it('leaves no tab stop when no day in the month is selectable', () => {
    open({ from: '', to: '', max: '2020-01-01' });
    expect(dayCells().every((b) => b.disabled)).toBe(true);
    expect(tabbable(), 'nothing to choose ⇒ nothing tabbable').toHaveLength(0);
  });

  it('keeps exactly one selectable stop after changing month', () => {
    open();
    fireEvent.click(screen.getByRole('button', { name: 'Oldingi oy' }));
    const stops = tabbable();
    expect(stops).toHaveLength(1);
    expect(stops[0]).not.toBeDisabled();
  });
});

describe('arrow, Home and End navigation', () => {
  const grid = () => screen.getByRole('grid');

  it('ArrowRight moves one day forward and takes focus with it', () => {
    open();
    fireEvent.keyDown(grid(), { key: 'ArrowRight' });
    expect(document.activeElement).toBe(dayNamed(6));
    expect(dayNamed(6).tabIndex).toBe(0);
    expect(dayNamed(5).tabIndex).toBe(-1);
  });

  it('ArrowLeft moves one day back', () => {
    open();
    fireEvent.keyDown(grid(), { key: 'ArrowLeft' });
    expect(document.activeElement).toBe(dayNamed(4));
  });

  it('ArrowDown moves a week forward', () => {
    open();
    fireEvent.keyDown(grid(), { key: 'ArrowDown' });
    expect(document.activeElement).toBe(dayNamed(12));
  });

  it('ArrowUp moves a week back, clamped at the first selectable day', () => {
    open();
    // The stop starts on the 5th; a week earlier is out of the month, so the
    // model clamps to the first cell rather than wrapping into February.
    fireEvent.keyDown(grid(), { key: 'ArrowUp' });
    expect(document.activeElement).toBe(dayNamed(1));
  });

  it('Home jumps to the first selectable day, End to the last', () => {
    open();
    fireEvent.keyDown(grid(), { key: 'Home' });
    expect(document.activeElement).toBe(dayNamed(1));
    fireEvent.keyDown(grid(), { key: 'End' });
    expect(document.activeElement).toBe(dayNamed(31));   // March
  });

  it('clamps at the month edges instead of wrapping or crashing', () => {
    open();
    fireEvent.keyDown(grid(), { key: 'Home' });
    fireEvent.keyDown(grid(), { key: 'ArrowLeft' });
    expect(document.activeElement).toBe(dayNamed(1));
    fireEvent.keyDown(grid(), { key: 'End' });
    fireEvent.keyDown(grid(), { key: 'ArrowRight' });
    expect(document.activeElement).toBe(dayNamed(31));
  });

  it('skips disabled days when moving', () => {
    open({ max: '2026-03-10' });
    fireEvent.keyDown(grid(), { key: 'End' });
    const active = document.activeElement as HTMLButtonElement;
    expect(active).not.toBeDisabled();
    expect(Number(active.dataset.day)).toBe(10);
  });

  it('ignores keys it does not own', () => {
    open();
    const before = document.activeElement;
    fireEvent.keyDown(grid(), { key: 'a' });
    expect(document.activeElement).toBe(before);
  });
});

describe('focus restoration', () => {
  it('Escape closes and returns focus to the trigger', () => {
    const { trigger } = open();
    expect(screen.getByRole('grid')).toBeInTheDocument();
    fireEvent.keyDown(document, { key: 'Escape' });
    expect(screen.queryByRole('grid')).toBeNull();
    expect(document.activeElement).toBe(trigger);
  });

  it('committing a range returns focus to the trigger', () => {
    const { trigger, onChange } = open();
    fireEvent.click(dayNamed(3));     // start
    fireEvent.click(dayNamed(9));     // end → commits and closes
    expect(onChange).toHaveBeenCalledWith({ from: '2026-03-03', to: '2026-03-09' });
    expect(document.activeElement).toBe(trigger);
  });

  it('a preset also returns focus to the trigger', () => {
    const { trigger, onChange } = open();
    fireEvent.click(screen.getByRole('button', { name: '7 kun' }));
    expect(onChange).toHaveBeenCalled();
    expect(document.activeElement).toBe(trigger);
  });
});

describe('range selection is unchanged', () => {
  it('orders the two clicks whichever way round they are made', () => {
    const { onChange } = open();
    fireEvent.click(dayNamed(20));
    fireEvent.click(dayNamed(8));
    expect(onChange).toHaveBeenCalledWith({ from: '2026-03-08', to: '2026-03-20' });
  });

  it('the first click does not commit', () => {
    const { onChange } = open();
    fireEvent.click(dayNamed(20));
    expect(onChange).not.toHaveBeenCalled();
    expect(screen.getByRole('grid'), 'the calendar stays open for the second pick').toBeInTheDocument();
  });

  it('marks the committed edges as selected', () => {
    open();
    expect(dayNamed(5).getAttribute('aria-selected')).toBe('true');
    expect(dayNamed(12).getAttribute('aria-selected')).toBe('true');
    expect(dayNamed(8).getAttribute('aria-selected')).toBe('false');
  });

  it('a disabled day cannot be picked', () => {
    const { onChange } = open({ max: '2026-03-10' });
    fireEvent.click(dayNamed(20));
    expect(onChange).not.toHaveBeenCalled();
  });
});

describe('coarse-pointer targets are retained', () => {
  it('day cells, month nav and presets all carry the shared rule', () => {
    open();
    for (const d of dayCells()) expect(d.className).toContain('tap-target');
    for (const name of ['Oldingi oy', 'Keyingi oy', '7 kun', '30 kun']) {
      expect(screen.getByRole('button', { name }).className).toContain('tap-target');
    }
  });
});
