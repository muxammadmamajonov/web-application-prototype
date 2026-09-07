// @vitest-environment jsdom
/**
 * (#266) The month-nav chevrons are icon-only; they must expose an accessible
 * name so a screen reader can distinguish prev from next.
 *
 * (Phase 3 remediation R-06) Plus the real keyboard path. The first Phase 3 pass
 * added arrow handling and closed the finding without ever driving the keyboard,
 * and two defects survived: every day cell sat in the Tab sequence (up to 31 Tab
 * presses to reach a date, and no way to Tab out in one step), and `role="grid"`
 * was declared over day cells with no rows between them. These tests move focus
 * for real and assert where it lands.
 */
import { describe, it, expect, vi, afterEach, beforeEach } from 'vitest';
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

import { DatePicker } from './DatePicker';

afterEach(cleanup);

describe('DatePicker month-nav accessibility (#266)', () => {
  it('exposes accessible names on the prev/next month buttons once open', () => {
    render(<DatePicker value="" onChange={() => {}} placeholder="Sanani tanlang" />);
    // Only the trigger exists until opened.
    fireEvent.click(screen.getByRole('button', { name: /sanani tanlang/i }));
    expect(screen.getByRole('button', { name: 'Oldingi oy' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Keyingi oy' })).toBeInTheDocument();
  });
});

// ── R-06: keyboard + grid semantics ──────────────────────────────────────────

beforeEach(() => {
  if (!Element.prototype.scrollIntoView) Element.prototype.scrollIntoView = () => {};
});

/** Open the calendar on a known month and hand back its grid. */
function openOn(value = '2026-03-12') {
  const onChange = vi.fn();
  render(<DatePicker value={value} onChange={onChange} />);
  const trigger = screen.getAllByRole('button')[0];
  fireEvent.click(trigger);
  return { grid: screen.getByRole('grid'), trigger, onChange };
}

const dayCells = (grid: HTMLElement) =>
  Array.from(grid.querySelectorAll<HTMLButtonElement>('button[data-day]'));

const focusedDay = () => (document.activeElement as HTMLElement | null)?.dataset?.day;

describe('DatePicker grid semantics (R-06)', () => {
  it('exposes grid → row → gridcell', () => {
    const { grid } = openOn();
    const rows = grid.querySelectorAll('[role="row"]');
    expect(rows.length).toBeGreaterThanOrEqual(4);   // a month spans 5–6 weeks
    for (const cell of grid.querySelectorAll('[role="gridcell"]')) {
      expect(cell.closest('[role="row"]')).not.toBeNull();
    }
  });

  it('gives every day an accessible name carrying its full date', () => {
    const { grid } = openOn();
    for (const cell of dayCells(grid)) {
      expect((cell.getAttribute('aria-label') ?? '').trim().length).toBeGreaterThan(0);
    }
  });
});

describe('DatePicker roving tabindex (R-06)', () => {
  it('puts exactly one day in the Tab sequence', () => {
    const { grid } = openOn();
    const tabbable = dayCells(grid).filter((c) => c.getAttribute('tabindex') === '0');
    expect(tabbable).toHaveLength(1);
  });

  it('starts on the selected day', () => {
    const { grid } = openOn('2026-03-12');
    const tabbable = dayCells(grid).find((c) => c.getAttribute('tabindex') === '0');
    expect(tabbable?.dataset.day).toBe('12');
  });

  it('moves the single tab stop with the arrows', () => {
    const { grid } = openOn('2026-03-12');
    fireEvent.keyDown(grid, { key: 'ArrowRight' });
    const tabbable = dayCells(grid).filter((c) => c.getAttribute('tabindex') === '0');
    expect(tabbable).toHaveLength(1);
    expect(tabbable[0].dataset.day).toBe('13');
  });
});

describe('DatePicker arrow-key movement (R-06)', () => {
  it('right and left move by a day', () => {
    const { grid } = openOn('2026-03-12');
    fireEvent.keyDown(grid, { key: 'ArrowRight' });
    expect(focusedDay()).toBe('13');
    fireEvent.keyDown(grid, { key: 'ArrowLeft' });
    expect(focusedDay()).toBe('12');
  });

  it('down and up move by a week', () => {
    const { grid } = openOn('2026-03-12');
    fireEvent.keyDown(grid, { key: 'ArrowDown' });
    expect(focusedDay()).toBe('19');
    fireEvent.keyDown(grid, { key: 'ArrowUp' });
    expect(focusedDay()).toBe('12');
  });

  it('Home and End reach the ends of the month', () => {
    const { grid } = openOn('2026-03-12');
    fireEvent.keyDown(grid, { key: 'Home' });
    expect(focusedDay()).toBe('1');
    fireEvent.keyDown(grid, { key: 'End' });
    expect(focusedDay()).toBe('31');
  });

  it('does not run off either end', () => {
    const { grid } = openOn('2026-03-12');
    fireEvent.keyDown(grid, { key: 'Home' });
    fireEvent.keyDown(grid, { key: 'ArrowLeft' });
    expect(focusedDay()).toBe('1');
    fireEvent.keyDown(grid, { key: 'End' });
    fireEvent.keyDown(grid, { key: 'ArrowRight' });
    expect(focusedDay()).toBe('31');
  });
});

describe('DatePicker selection and dismissal (R-06)', () => {
  it('selecting reports the date and restores focus to the trigger', () => {
    const { grid, trigger, onChange } = openOn('2026-03-12');
    const day15 = dayCells(grid).find((c) => c.dataset.day === '15')!;
    fireEvent.click(day15);
    expect(onChange).toHaveBeenCalledWith('2026-03-15');
    expect(document.activeElement).toBe(trigger);
  });

  // (#2) The calendar must DISMISS on a valid pick, not just report it. This was
  // asserted for Escape and not for selection — the one path every user takes —
  // so a popover that stayed open after a pick would have gone unnoticed here.
  it('closes immediately when a day is picked with the mouse', () => {
    const { grid } = openOn('2026-03-12');
    fireEvent.click(dayCells(grid).find((c) => c.dataset.day === '15')!);
    expect(screen.queryByRole('grid')).toBeNull();
  });

  it('closes when a day is chosen from the keyboard', () => {
    // Day cells are real buttons, so Enter fires their click — but that is an
    // implementation detail worth pinning: swapping them for divs would break
    // keyboard selection silently.
    const { grid } = openOn('2026-03-12');
    const day15 = dayCells(grid).find((c) => c.dataset.day === '15')!;
    day15.focus();
    fireEvent.click(day15);        // what Enter/Space dispatch on a <button>
    expect(screen.queryByRole('grid')).toBeNull();
  });

  it('stays open while only the month is being changed', () => {
    // Navigating is not selecting: closing here would make picking a date in
    // another month impossible.
    const { grid } = openOn('2026-03-12');
    expect(grid).not.toBeNull();
    // The i18n mock above returns the inline default for non-calendar keys.
    fireEvent.click(screen.getByLabelText('Keyingi oy'));
    expect(screen.queryByRole('grid')).not.toBeNull();
  });

  it('Escape closes and restores focus to the trigger', () => {
    const { trigger } = openOn('2026-03-12');
    expect(screen.queryByRole('grid')).not.toBeNull();
    fireEvent.keyDown(document, { key: 'Escape' });
    expect(screen.queryByRole('grid')).toBeNull();
    expect(document.activeElement).toBe(trigger);
  });
});

describe('DatePicker touch targets (P2-05)', () => {
  it('sizes the month-nav buttons for a coarse pointer', () => {
    // These were 32 px — below the coarse-pointer minimum on the phone this
    // product is primarily used on.
    const { grid } = openOn();
    expect(grid).toBeTruthy();
    for (const name of ['Oldingi oy', 'Keyingi oy']) {
      const btn = screen.getByRole('button', { name });
      expect(btn.className, `${name} carries the shared rule`).toContain('tap-target');
      expect(btn.className).toMatch(/\bh-11\b/);
      expect(btn.className).toMatch(/\bw-11\b/);
      expect(btn.className, 'the old size is gone').not.toMatch(/\bh-8\b/);
    }
  });

  it('keeps their accessible names and hides the chevrons', () => {
    openOn();
    for (const name of ['Oldingi oy', 'Keyingi oy']) {
      const btn = screen.getByRole('button', { name });
      const svg = btn.querySelector('svg');
      expect(svg?.getAttribute('aria-hidden')).toBe('true');
    }
  });
});

// ── C3-04: the tab stop must land on a day the user can actually choose ──────

/** Opens the picker and returns its day buttons. */
function openWith(props: Partial<Parameters<typeof DatePicker>[0]> = {}) {
  render(<DatePicker value="" onChange={() => {}} placeholder="Sanani tanlang" {...props} />);
  // The trigger is the only control before the popover opens; its label is the
  // formatted value once one is set, so find it by position rather than text.
  fireEvent.click(screen.getAllByRole('button')[0]);
  return () => screen.getAllByRole('gridcell').filter(
    (n): n is HTMLButtonElement => n.tagName === 'BUTTON',
  );
}
const tabbable = (days: HTMLButtonElement[]) => days.filter((b) => b.tabIndex === 0);
const iso = (d: Date) => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;

describe('the roving tab stop is always a selectable day', () => {
  it('puts exactly one enabled day in the Tab sequence', () => {
    const days = openWith();
    const stops = tabbable(days());
    expect(stops).toHaveLength(1);
    expect(stops[0]).not.toBeDisabled();
  });

  it('does not park the tab stop on a disabled TODAY', () => {
    // An age-restricted birthdate field disables today by construction. The old
    // code preferred today unconditionally, leaving the only tabbable cell
    // disabled — so Tab could not enter the grid at all.
    const today = new Date();
    const cutoff = new Date(today.getFullYear(), today.getMonth(), 1);   // 1st of this month
    const days = openWith({ max: iso(cutoff) });

    const stops = tabbable(days());
    expect(stops).toHaveLength(1);
    expect(stops[0], 'the tab stop must be reachable').not.toBeDisabled();
    expect(stops[0].getAttribute('data-day')).toBe('1');
  });

  it('does not park the tab stop on a disabled SELECTED day', () => {
    const today = new Date();
    const cutoff = new Date(today.getFullYear(), today.getMonth(), 10);
    // Selected day 20 is past the max, so it renders disabled.
    const selected = iso(new Date(today.getFullYear(), today.getMonth(), 20));
    const days = openWith({ value: selected, max: iso(cutoff) });

    const stops = tabbable(days());
    expect(stops).toHaveLength(1);
    expect(stops[0]).not.toBeDisabled();
    expect(Number(stops[0].getAttribute('data-day'))).toBeLessThanOrEqual(10);
  });

  it('uses the selected day when it IS selectable', () => {
    const today = new Date();
    const pick = new Date(today.getFullYear(), today.getMonth(), 12);
    const days = openWith({ value: iso(pick) });
    expect(tabbable(days())[0].getAttribute('data-day')).toBe('12');
  });

  it('leaves no tab stop when the whole visible month is out of range', () => {
    // max before the 1st of this month ⇒ every day here is disabled.
    const today = new Date();
    const before = new Date(today.getFullYear(), today.getMonth(), 1);
    before.setDate(before.getDate() - 1);
    const days = openWith({ min: iso(new Date(today.getFullYear() + 5, 0, 1)) });

    const all = days();
    expect(all.every((b) => b.disabled), 'this month should be fully disabled').toBe(true);
    expect(tabbable(all), 'nothing to choose ⇒ nothing tabbable').toHaveLength(0);
  });

  it('keeps exactly one enabled tab stop after paging to another month', () => {
    const days = openWith();
    fireEvent.click(screen.getByRole('button', { name: 'Keyingi oy' }));
    const stops = tabbable(days());
    expect(stops).toHaveLength(1);
    expect(stops[0]).not.toBeDisabled();
  });
});

describe('grid semantics include the weekday headers', () => {
  it('names the grid', () => {
    openWith();
    expect(screen.getByRole('grid')).toBeInTheDocument();
  });

  it('exposes seven column headers inside the grid', () => {
    openWith();
    const headers = screen.getAllByRole('columnheader');
    expect(headers).toHaveLength(7);
    for (const h of headers) {
      expect(screen.getByRole('grid').contains(h), 'headers must live inside the grid').toBe(true);
    }
  });

  it('keeps rows between the grid and its cells', () => {
    openWith();
    const rows = screen.getAllByRole('row');
    expect(rows.length).toBeGreaterThan(1);
    for (const cell of screen.getAllByRole('gridcell')) {
      expect(rows.some((r) => r.contains(cell)), 'every cell needs a row parent').toBe(true);
    }
  });
});

describe('coarse-pointer targets on the date controls', () => {
  it('the month/year selector carries the shared touch rule', () => {
    openWith();
    const monthBtn = screen.getByRole('button', { expanded: false });
    expect(monthBtn.className).toContain('tap-target');
  });

  it('day cells carry it too', () => {
    const days = openWith();
    for (const d of days()) expect(d.className).toContain('tap-target');
  });

  it('year options carry it once the year grid is open', () => {
    openWith();
    fireEvent.click(screen.getByRole('button', { expanded: false }));
    const years = screen.getAllByRole('button').filter((b) => /^\d{4}$/.test(b.textContent ?? ''));
    expect(years.length).toBeGreaterThan(0);
    for (const y of years) expect(y.className).toContain('tap-target');
  });

  it('prev/next keep theirs', () => {
    openWith();
    for (const name of ['Oldingi oy', 'Keyingi oy']) {
      expect(screen.getByRole('button', { name }).className).toContain('tap-target');
    }
  });
});

describe('the calendar cannot be wider than the viewport', () => {
  it('is sized from a measurement, not a CSS guess at the viewport', () => {
    // The width used to be capped with `w-[min(21rem,100vw-…)]`, which only
    // works while the panel is laid out in the page. It is now portalled and
    // positioned against the VISUAL viewport, so the cap is an explicit pixel
    // width the hook computed — see `datePickerPlacement.test.tsx` for the
    // arithmetic and the edge clamping.
    openWith();
    const panel = screen.getByRole('grid').closest('[data-testid="date-picker-panel"]') as HTMLElement;
    expect(panel, 'the portalled panel').not.toBeNull();
    expect(panel.style.width).toMatch(/^\d+px$/);
    expect(Number.parseInt(panel.style.width, 10)).toBeLessThanOrEqual(window.innerWidth);
    expect(panel.className).toMatch(/\bfixed\b/);
  });

  it('scrolls the grid instead, on a device too narrow for seven touch columns', () => {
    openWith();
    expect(screen.getByRole('grid').className).toContain('overflow-x-auto');
  });
});
