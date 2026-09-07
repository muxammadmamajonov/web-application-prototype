import { useEffect, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { useTranslation } from 'react-i18next';
import { Calendar as CalIcon, ChevronLeft, ChevronRight } from 'lucide-react';
import { cn } from '@/lib/cn';
import { inputCls } from './Modal';
import { useDateGrid, toISO } from './dateGrid';
import { useCalendarNames } from './calendarNames';
import { useAnchoredOverlay } from '@/hooks/useAnchoredOverlay';

/** Seven 44 px columns plus padding. Capped to the viewport by the hook. */
const PANEL_WIDTH = 336;

/**
 * Device-independent calendar for picking a date. Monday-first grid, localized
 * labels, a year picker for fast jumps.
 *
 * The panel is rendered in a PORTAL and positioned against the visual viewport.
 * Laid out in place it belongs to whatever scroll container holds the field, so
 * on a short phone its lower rows land past the fold and underneath the fixed
 * bottom navigation — unreachable, and indistinguishable from a calendar that
 * refuses to close, because the tap never reaches a day at all.
 *
 * Selecting a day commits and closes. That is the only path that changes the
 * value: month and year navigation never close, and dismissing without a choice
 * leaves the committed value alone.
 */
export function DatePicker({
  value, onChange, max, min, placeholder, id = 'date',
}: {
  value: string;
  onChange: (iso: string) => void;
  max?: string;
  min?: string;
  placeholder?: string;
  id?: string;
}) {
  const { t } = useTranslation();
  const { months: MONTH_NAMES, weekdays: WEEKDAY_NAMES } = useCalendarNames();
  const [open, setOpen] = useState(false);
  const [yearGrid, setYearGrid] = useState(false);
  const ref = useRef<HTMLDivElement>(null);
  const panelRef = useRef<HTMLDivElement>(null);
  const triggerRef = useRef<HTMLButtonElement>(null);
  const { triggerRef: anchorRef, pos } = useAnchoredOverlay(open, PANEL_WIDTH);

  /** One place that ends an interaction, so open-ness has a single owner. */
  const close = (returnFocus = true) => {
    setOpen(false);
    setYearGrid(false);
    if (returnFocus) triggerRef.current?.focus();
  };

  /** Opening always starts on the committed date's month.
   *
   *  Month navigation is scoped to one visit: leaving the calendar on March
   *  because that is where you were browsing, then reopening it, would hide the
   *  date the field actually holds. */
  const openPanel = () => {
    const at = value ? new Date(value + 'T00:00:00') : null;
    setCursor(at ?? new Date(today.getFullYear(), today.getMonth(), 1));
    setYearGrid(false);
    setOpen(true);
  };
  const selected = value ? new Date(value + 'T00:00:00') : null;
  const today = new Date();
  // Open on the selected date if there is one, otherwise the current month —
  // not an arbitrary 20-years-ago default.
  const [cursor, setCursor] = useState(() => selected ?? new Date(today.getFullYear(), today.getMonth(), 1));

  useEffect(() => {
    if (!open) return;
    const onDoc = (e: MouseEvent) => {
      // The panel is portalled out of `ref`, so both roots count as "inside".
      const target = e.target as Node;
      if (ref.current?.contains(target) || panelRef.current?.contains(target)) return;
      close(false);        // dismissing is not choosing: the value is untouched
    };
    const onKey = (e: KeyboardEvent) => {
      // Esc hands focus back to the trigger rather than dropping it on the
      // document. IME composition owns its own Escape.
      const native = e as unknown as { isComposing?: boolean; keyCode?: number };
      if (native.isComposing || native.keyCode === 229) return;
      if (e.key === 'Escape') { e.stopPropagation(); close(); }
    };
    document.addEventListener('mousedown', onDoc);
    document.addEventListener('keydown', onKey);
    return () => {
      document.removeEventListener('mousedown', onDoc);
      document.removeEventListener('keydown', onKey);
    };
  }, [open]);

  const y = cursor.getFullYear();
  const m = cursor.getMonth();
  const maxD = max ? new Date(max + 'T00:00:00') : null;
  const minD = min ? new Date(min + 'T00:00:00') : null;

  const display = selected
    ? `${selected.getDate()} ${MONTH_NAMES[selected.getMonth()]} ${selected.getFullYear()}`
    : '';

  const disabled = (d: number) => {
    const dt = new Date(y, m, d);
    if (maxD && dt > maxD) return true;
    if (minD && dt < minD) return true;
    return false;
  };

  // Prefer the selected day, else today — but only when it is selectable. See
  // `dateGrid.ts` for the invariants this enforces.
  const inMonth = (d: Date | null) =>
    d && d.getFullYear() === y && d.getMonth() === m ? d.getDate() : null;
  const { cells, rovingDay, setActiveDay, onGridKey } = useDateGrid({
    open, year: y, month: m, isDisabled: disabled,
    prefer: [inMonth(selected), inMonth(today)],
  });

  const years = Array.from({ length: 120 }, (_, i) => today.getFullYear() - i);

  return (
    <div ref={ref} className="relative">
      <button
        ref={(el) => { triggerRef.current = el; anchorRef.current = el; }}
        type="button"
        id={id}
        aria-haspopup="dialog"
        aria-expanded={open}
        onClick={() => (open ? close() : openPanel())}
        className={cn(inputCls, 'flex items-center gap-2 text-left', !display && 'text-muted')}
      >
        <CalIcon size={16} className="text-muted" />
        {display || placeholder || t('common.pickDate', 'Sanani tanlang')}
      </button>

      {open && pos && createPortal(
        // Fixed to the visual viewport and above the bottom navigation, with an
        // explicit max height so a short screen scrolls the calendar instead of
        // hiding its last rows.
        <div
          ref={panelRef}
          data-testid="date-picker-panel"
          data-side={pos.side}
          role="dialog"
          aria-modal="false"
          aria-label={t('a11y.calendar', 'Kalendar')}
          style={{
            left: pos.left, top: pos.top, width: pos.width,
            maxHeight: pos.maxHeight, transform: pos.transform,
          }}
          // z-111 is the shared layer for portalled popovers (see RowActionMenu).
          // Portalling to <body> left the panel outside the dialog's stacking
          // context, so a local z-60 that used to sit above the form now sat
          // BEHIND the z-80 sheet that opened it.
          className="fixed z-[111] overflow-y-auto overscroll-contain rounded-card border border-line bg-surface p-2 shadow-pop sm:p-3">
          {/* Header */}
          <div className="mb-2 flex items-center justify-between">
            <button type="button" aria-label={t('a11y.prevMonth', 'Oldingi oy')}
                    onClick={() => setCursor(new Date(y, m - 1, 1))}
                    className="tap-target grid h-11 w-11 place-items-center rounded-icon text-ink-soft hover:bg-ink/5">
              <ChevronLeft size={16} aria-hidden="true" />
            </button>
            <button type="button" onClick={() => setYearGrid((v) => !v)}
                    aria-expanded={yearGrid}
                    className="tap-target rounded-icon px-3 py-1 text-body font-semibold hover:bg-ink/5">
              {MONTH_NAMES[m]} {y}
            </button>
            <button type="button" aria-label={t('a11y.nextMonth', 'Keyingi oy')}
                    onClick={() => setCursor(new Date(y, m + 1, 1))}
                    className="tap-target grid h-11 w-11 place-items-center rounded-icon text-ink-soft hover:bg-ink/5">
              <ChevronRight size={16} aria-hidden="true" />
            </button>
          </div>

          {yearGrid ? (
            <div className="grid max-h-[15rem] grid-cols-4 gap-1.5 overflow-y-auto">
              {years.map((yr) => (
                <button key={yr} type="button"
                        onClick={() => { setCursor(new Date(yr, m, 1)); setYearGrid(false); }}
                        className={cn('tap-target grid place-items-center rounded-icon py-2 text-body hover:bg-ink/5',
                                      yr === y && 'bg-ink text-white hover:bg-ink')}>
                  {yr}
                </button>
              ))}
            </div>
          ) : (
            // Below ~360px seven 44px columns cannot fit; the grid scrolls
            // inside the popover rather than the popover overflowing the page.
            <div className="overflow-x-auto" role="grid" aria-label={t('a11y.calendar', 'Kalendar')}
                 onKeyDown={onGridKey}>
              {/* The weekday strip is part of the grid, as its column headers —
                  outside it, the grid declares columns it never names. */}
              <div role="rowgroup">
                <div role="row" className="mb-1 grid grid-cols-7 gap-0.5 text-center text-micro font-medium text-muted sm:gap-1">
                  {WEEKDAY_NAMES.map((w) => <span key={w} role="columnheader">{w}</span>)}
                </div>
              </div>
              <div role="rowgroup" className="grid grid-cols-7 gap-0.5 sm:gap-1">
                {/* One row of seven per week keeps the hierarchy valid:
                    grid → rowgroup → row → gridcell. */}
                {Array.from({ length: Math.ceil(cells.length / 7) }).map((_, w) => (
                  <div key={`w${w}`} role="row" className="contents">
                {cells.slice(w * 7, w * 7 + 7).map((d, i) => {
                  if (d === null) return <span key={`e${w}-${i}`} role="gridcell" />;
                  const iso = toISO(y, m, d);
                  const isSel = value === iso;
                  const isToday = today.getFullYear() === y && today.getMonth() === m && today.getDate() === d;
                  const dis = disabled(d);
                  return (
                    <button
                      key={iso}
                      type="button"
                      data-day={d}
                      role="gridcell"
                      aria-selected={isSel}
                      // Roving tabindex: exactly one SELECTABLE day is in the Tab
                      // sequence, so Tab enters and leaves the calendar in one
                      // step and arrows do the moving inside it.
                      tabIndex={rovingDay === d ? 0 : -1}
                      disabled={dis}
                      aria-label={`${d} ${MONTH_NAMES[m]} ${y}`}
                      onClick={() => { setActiveDay(d); onChange(iso); close(); }}
                      className={cn(
                        'tap-target grid h-9 w-full place-items-center rounded-icon text-body',
                        dis && 'cursor-not-allowed text-muted/40',
                        !dis && !isSel && 'hover:bg-ink/5',
                        isSel && 'bg-ink text-white',
                        !isSel && isToday && 'ring-1 ring-accent',
                      )}
                    >{d}</button>
                  );
                })}
                  </div>
                ))}
              </div>
            </div>
          )}
        </div>,
        document.body,
      )}
    </div>
  );
}
