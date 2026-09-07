import { useEffect, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { useTranslation } from 'react-i18next';
import { CalendarRange, ChevronLeft, ChevronRight } from 'lucide-react';
import { cn } from '@/lib/cn';
import { inputCls } from './Modal';
import { useDateGrid, toISO } from './dateGrid';
import { useCalendarNames } from './calendarNames';
import { useAnchoredOverlay } from '@/hooks/useAnchoredOverlay';

/** `w-[min(21rem,…)]` — the panel's preferred width. */
const PANEL_WIDTH = 336;

function parse(s: string) { return s ? new Date(s + 'T00:00:00') : null; }

/** Single custom date-range picker — one trigger, one calendar. First tap sets
 *  the start, second sets the end (auto-orders). Includes quick presets.
 *
 *  The panel is rendered in a PORTAL and positioned against the visual
 *  viewport (see `useAnchoredOverlay`, shared with `DatePicker`) — laid out in
 *  place it belongs to whatever `overflow-hidden`/`overflow-y-auto` ancestor
 *  holds the field (a Modal's dialog card), which clips it. */
export function DateRangePicker({
  from, to, onChange, max,
}: {
  from: string;
  to: string;
  onChange: (range: { from: string; to: string }) => void;
  max?: string;
}) {
  const { t } = useTranslation();
  const { months: MONTH_NAMES, shortMonths: SHORT_MONTHS, weekdays: WEEKDAY_NAMES } = useCalendarNames();
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);
  const panelRef = useRef<HTMLDivElement>(null);
  const triggerRef = useRef<HTMLButtonElement>(null);
  const { triggerRef: anchorRef, pos } = useAnchoredOverlay(open, PANEL_WIDTH);
  /** Close and hand focus back, so the keyboard user returns where they were. */
  const close = () => { setOpen(false); triggerRef.current?.focus(); };

  const today = new Date();
  const [cursor, setCursor] = useState(() => parse(to) ?? today);
  // While picking, hold the in-progress start until the user clicks the end.
  const [pendingStart, setPendingStart] = useState<string | null>(null);

  useEffect(() => {
    if (!open) return;
    // The panel is portalled out of `ref`, so both roots count as "inside".
    const onDoc = (e: MouseEvent) => {
      const target = e.target as Node;
      if (ref.current?.contains(target) || panelRef.current?.contains(target)) return;
      setOpen(false);
    };
    // Esc closes and restores focus to the trigger rather than dropping it on
    // the document.
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') close(); };
    document.addEventListener('mousedown', onDoc);
    document.addEventListener('keydown', onKey);
    return () => {
      document.removeEventListener('mousedown', onDoc);
      document.removeEventListener('keydown', onKey);
    };
  }, [open]);

  const y = cursor.getFullYear();
  const m = cursor.getMonth();
  const maxD = max ? parse(max) : null;
  const disabled = (d: number) => (maxD ? new Date(y, m, d) > maxD : false);

  // Same keyboard contract as DatePicker. The tab stop prefers the in-progress
  // start, then the committed range's edges, then today — each only when it is
  // actually selectable.
  const dayIn = (v: string | null) => {
    const d = parse(v ?? '');
    return d && d.getFullYear() === y && d.getMonth() === m ? d.getDate() : null;
  };
  const { cells, rovingDay, setActiveDay, onGridKey } = useDateGrid({
    open, year: y, month: m, isDisabled: disabled,
    prefer: [dayIn(pendingStart), dayIn(from), dayIn(to),
             today.getFullYear() === y && today.getMonth() === m ? today.getDate() : null],
  });

  const pick = (d: number) => {
    const v = toISO(y, m, d);
    if (!pendingStart) { setPendingStart(v); return; }
    // second click → order the two and commit
    const a = pendingStart <= v ? pendingStart : v;
    const b = pendingStart <= v ? v : pendingStart;
    onChange({ from: a, to: b });
    setPendingStart(null);
    close();
  };

  const inRange = (d: number) => {
    const v = toISO(y, m, d);
    if (pendingStart) return v === pendingStart;
    return v >= from && v <= to;
  };
  const isEdge = (d: number) => {
    const v = toISO(y, m, d);
    return v === from || v === to || v === pendingStart;
  };

  /** "5 Mar" — the day plus a validated short month name. */
  const short = (d: Date) => `${d.getDate()} ${SHORT_MONTHS[d.getMonth()]}`;

  const label = (() => {
    const f = parse(from), tt = parse(to);
    if (!f || !tt) return t('reports.pickRange', 'Sanani tanlang');
    return `${short(f)} – ${short(tt)}`;
  })();

  const applyPreset = (days: number | 'month') => {
    const end = new Date();
    let start: Date;
    if (days === 'month') start = new Date(end.getFullYear(), end.getMonth(), 1);
    else start = new Date(end.getTime() - days * 86400_000);
    onChange({ from: toISO(start.getFullYear(), start.getMonth(), start.getDate()), to: toISO(end.getFullYear(), end.getMonth(), end.getDate()) });
    setPendingStart(null);
    setCursor(end);
    close();
  };

  return (
    <div ref={ref} className="relative">
      <button ref={(el) => { triggerRef.current = el; anchorRef.current = el; }}
              type="button" onClick={() => { setOpen((v) => !v); setPendingStart(null); }}
              className={cn(inputCls, 'flex items-center gap-2 text-left')}>
        <CalendarRange size={16} className="text-muted" />
        <span className="tabular-nums">{label}</span>
      </button>

      {open && pos && createPortal(
        <div
          ref={panelRef}
          data-testid="date-range-picker-panel"
          style={{
            left: pos.left, top: pos.top, width: pos.width,
            maxHeight: pos.maxHeight, transform: pos.transform,
          }}
          // z-111 is the shared layer for portalled popovers (see DatePicker).
          className="fixed z-[111] overflow-y-auto overscroll-contain rounded-card border border-line bg-surface p-2 shadow-pop sm:p-3">
          {/* Presets */}
          <div className="mb-2 flex flex-wrap gap-1.5">
            <Preset label={t('reports.preset7', '7 kun')}   onClick={() => applyPreset(7)} />
            <Preset label={t('reports.preset30', '30 kun')} onClick={() => applyPreset(30)} />
            <Preset label={t('reports.presetMonth', 'Shu oy')} onClick={() => applyPreset('month')} />
            <Preset label={t('reports.preset90', '90 kun')} onClick={() => applyPreset(90)} />
          </div>

          <div className="mb-2 flex items-center justify-between">
            <button type="button" aria-label={t('a11y.prevMonth', 'Oldingi oy')} onClick={() => setCursor(new Date(y, m - 1, 1))}
                    className="tap-target grid h-11 w-11 place-items-center rounded-icon text-ink-soft hover:bg-ink/5"><ChevronLeft size={16} aria-hidden="true" /></button>
            <span className="text-body font-semibold">{MONTH_NAMES[m]} {y}</span>
            <button type="button" aria-label={t('a11y.nextMonth', 'Keyingi oy')} onClick={() => setCursor(new Date(y, m + 1, 1))}
                    className="tap-target grid h-11 w-11 place-items-center rounded-icon text-ink-soft hover:bg-ink/5"><ChevronRight size={16} aria-hidden="true" /></button>
          </div>

          {/* Same hierarchy as DatePicker: grid → rowgroup → row → gridcell,
              with the weekday strip inside the grid as its column headers. */}
          <div className="overflow-x-auto" role="grid" aria-label={t('a11y.calendar', 'Kalendar')}
               onKeyDown={onGridKey}>
            <div role="rowgroup">
              <div role="row" className="mb-1 grid grid-cols-7 gap-0.5 text-center text-micro font-medium text-muted">
                {WEEKDAY_NAMES.map((w) => <span key={w} role="columnheader">{w}</span>)}
              </div>
            </div>
            <div role="rowgroup" className="grid grid-cols-7 gap-0.5">
              {Array.from({ length: Math.ceil(cells.length / 7) }).map((_, w) => (
                <div key={`w${w}`} role="row" className="contents">
                  {cells.slice(w * 7, w * 7 + 7).map((d, i) => {
                    if (d === null) return <span key={`e${w}-${i}`} role="gridcell" />;
                    const dis = disabled(d);
                    const edge = isEdge(d);
                    const within = inRange(d);
                    return (
                      <button key={toISO(y, m, d)} type="button"
                              data-day={d}
                              role="gridcell"
                              aria-selected={edge}
                              // Roving tabindex: exactly one selectable day is in
                              // the Tab sequence, so Tab enters and leaves the
                              // calendar in one step.
                              tabIndex={rovingDay === d ? 0 : -1}
                              disabled={dis}
                              aria-label={`${d} ${MONTH_NAMES[m]} ${y}`}
                              onClick={() => { setActiveDay(d); pick(d); }}
                              className={cn(
                                'tap-target grid h-9 w-full place-items-center rounded-icon text-body',
                                dis && 'cursor-not-allowed text-muted/40',
                                edge && 'bg-ink text-white',
                                !edge && within && 'bg-[color:var(--c-accent-soft)] text-ink',
                                !edge && !within && !dis && 'hover:bg-ink/5',
                              )}>
                        {d}
                      </button>
                    );
                  })}
                </div>
              ))}
            </div>
          </div>
          <p className="mt-2 text-center text-micro text-muted">
            {pendingStart ? t('reports.pickEnd', 'Tugash sanasini tanlang') : t('reports.pickStart', 'Boshlanish sanasini tanlang')}
          </p>
        </div>,
        document.body,
      )}
    </div>
  );
}

function Preset({ label, onClick }: { label: string; onClick: () => void }) {
  return (
    <button type="button" onClick={onClick}
            className="tap-target inline-flex items-center rounded-pill border border-line px-3 py-1 text-caption font-medium text-ink-soft hover:border-ink/30 hover:text-ink">
      {label}
    </button>
  );
}
