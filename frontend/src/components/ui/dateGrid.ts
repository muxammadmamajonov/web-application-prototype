import { useEffect, useState, type KeyboardEvent } from 'react';

/**
 * The shared WAI-ARIA date-grid keyboard model.
 *
 * `DatePicker` and `DateRangePicker` are two calendars with different selection
 * rules but exactly the same navigation contract, so the contract lives here
 * once. Duplicating it is how the two drifted apart in the first place: one grew
 * arrow keys, a roving tab stop and focus restoration while the other kept
 * Tab-through-every-cell.
 *
 * The invariants this owns:
 *
 *   * **Exactly one selectable day is tabbable** whenever any day is
 *     selectable, so Tab enters and leaves the calendar in one step instead of
 *     walking 31 cells. `rovingDay` is derived, not just stored, so this holds
 *     on the render right after the month changes too.
 *   * **A disabled day never owns the tab stop.** A `max`/`min` can disable the
 *     selected date or today — an age-restricted picker disables today by
 *     construction — and a disabled button with `tabIndex=0` leaves the grid
 *     with nothing tabbable at all.
 *   * **No selectable day ⇒ no tab stop**, which is correct: there is nothing
 *     in the month to choose.
 *   * Arrows move by day (±1) and week (±7); Home/End jump to the ends; focus
 *     follows the roving index so each day is announced as the user moves.
 */

/**
 * Zero-padded two digits — `9` → `"09"`.
 *
 * Existed three times over (`DatePicker`, `DateRangePicker`, `TimePicker`),
 * byte-identical in each, which is the same drift risk this module was created
 * to close: the padding rule behind every `YYYY-MM-DD` and `HH:MM` the product
 * emits belongs in one place.
 */
export function pad(n: number): string {
  return String(n).padStart(2, '0');
}

/**
 * `YYYY-MM-DD` for a LOCAL calendar date — no `toISOString()`, which would
 * shift the day for anyone east of UTC (the shops run at UTC+5).
 *
 * `month` is 0-based, matching `Date.prototype.getMonth()`, so a caller can
 * pass a Date's parts straight through. Was duplicated as `toISO` in
 * `DatePicker` and `iso` in `DateRangePicker` — same body, two names.
 */
export function toISO(y: number, m: number, d: number): string {
  return `${y}-${pad(m + 1)}-${pad(d)}`;
}

/** Monday-first cells for a month: leading blanks, then 1…daysInMonth. */
function monthCells(year: number, month: number): (number | null)[] {
  const firstDow = (new Date(year, month, 1).getDay() + 6) % 7;
  const daysInMonth = new Date(year, month + 1, 0).getDate();
  return [
    ...Array<null>(firstDow).fill(null),
    ...Array.from({ length: daysInMonth }, (_, i) => i + 1),
  ];
}

interface DateGrid {
  /** Monday-first cell list for the visible month. */
  cells: (number | null)[];
  /** Days the user can actually land on, in order. */
  enabledDays: number[];
  /** The day that owns the tab stop, or null when nothing is selectable. */
  rovingDay: number | null;
  /** Move the tab stop (e.g. on click, so it follows the selection). */
  setActiveDay: (day: number) => void;
  /** Attach to the element carrying `role="grid"`. */
  onGridKey: (e: KeyboardEvent<HTMLElement>) => void;
}

export function useDateGrid({ open, year, month, isDisabled, prefer }: {
  open: boolean;
  year: number;
  month: number;
  isDisabled: (day: number) => boolean;
  /** Candidate days for the initial tab stop, best first. Disabled ones are skipped. */
  prefer: Array<number | null | undefined>;
}): DateGrid {
  const [activeDay, setActiveDay] = useState<number | null>(null);

  const cells = monthCells(year, month);
  const enabledDays = cells.filter((d): d is number => d !== null && !isDisabled(d));

  // Seed the tab stop when the calendar opens or the visible month changes.
  // `prefer` is intentionally not a dependency: it is rebuilt every render, and
  // re-seeding on every render would fight the user's arrow keys.
  useEffect(() => {
    if (!open) { setActiveDay(null); return; }
    const first = prefer.find((d): d is number => typeof d === 'number' && !isDisabled(d));
    setActiveDay(first ?? enabledDays[0] ?? null);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, year, month]);

  const rovingDay = activeDay !== null && enabledDays.includes(activeDay)
    ? activeDay
    : enabledDays[0] ?? null;

  const onGridKey = (e: KeyboardEvent<HTMLElement>) => {
    const step = e.key === 'ArrowRight' ? 1 : e.key === 'ArrowLeft' ? -1
      : e.key === 'ArrowDown' ? 7 : e.key === 'ArrowUp' ? -7 : 0;
    const isHomeEnd = e.key === 'Home' || e.key === 'End';
    if (step === 0 && !isHomeEnd) return;
    e.preventDefault();
    const days = Array.from(
      e.currentTarget.querySelectorAll<HTMLButtonElement>('button[data-day]:not([disabled])'),
    );
    if (days.length === 0) return;

    const current = days.findIndex((b) => Number(b.dataset.day) === rovingDay);
    const from = current >= 0 ? current : 0;
    const next = isHomeEnd
      ? (e.key === 'Home' ? 0 : days.length - 1)
      : Math.min(days.length - 1, Math.max(0, from + step));

    const target = days[next];
    if (!target) return;
    // Move the tab stop AND the focus together, so the roving index and the real
    // focus never disagree.
    setActiveDay(Number(target.dataset.day));
    target.focus();
  };

  return { cells, enabledDays, rovingDay, setActiveDay, onGridKey };
}
