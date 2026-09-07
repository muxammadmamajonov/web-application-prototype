/* eslint-disable react-refresh/only-export-components -- UI-kit module: the
   filter components are colocated with the shared matchesDatePreset() helper
   used across route pages. The rule only guards dev-time Fast Refresh. */
import { useId, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Check, ChevronDown, SlidersHorizontal, X } from 'lucide-react';
import { Button } from '@/components/ui/Button';
import { cn } from '@/lib/cn';
import { isInsideTelegramClient } from '@/lib/telegram';
import { useFocusTrap } from '@/hooks/useFocusTrap';

/** A "Filtr" trigger button with an active-count badge. */
export function FilterButton({ count, onClick, label }: { count: number; onClick: () => void; label?: string }) {
  const { t } = useTranslation();
  // role="dialog" + aria-modal MUST carry an accessible name, or this opens
  // silently for a screen-reader user. Named from its own visible heading.
  return (
    <button onClick={onClick}
            className="inline-flex shrink-0 items-center gap-2 whitespace-nowrap rounded-control border border-line bg-surface px-3.5 py-2 text-body font-medium text-ink-soft hover:border-ink/30 hover:text-ink">
      <SlidersHorizontal size={16} />
      {label ?? t('common.filter', 'Filtr')}
      {count > 0 && (
        <span className="grid h-5 min-w-[1.25rem] place-items-center rounded-pill bg-ink px-1 text-micro font-semibold text-white">{count}</span>
      )}
    </button>
  );
}

/** Removable chips for the currently-applied filters, shown under the toolbar.
 *  Each chip clears its own facet; tapping one is faster than reopening the
 *  drawer. Renders nothing when no filters are active. */
export function ActiveFilters({ filters, onClearAll }: {
  filters: { key: string; label: string; onRemove: () => void }[];
  onClearAll?: () => void;
}) {
  const { t } = useTranslation();
  if (filters.length === 0) return null;
  return (
    <div className="mb-4 flex flex-wrap items-center gap-2">
      {filters.map((f) => (
        <button key={f.key} type="button" onClick={f.onRemove}
                className="tap-target inline-flex items-center gap-1.5 rounded-pill border border-line bg-surface py-1 pl-3 pr-2 text-caption font-medium text-ink-soft transition hover:border-ink/30 hover:text-ink">
          {f.label}
          <X size={14} className="text-muted" />
        </button>
      ))}
      {filters.length > 1 && onClearAll && (
        <button type="button" onClick={onClearAll} className="text-caption font-medium text-muted hover:text-ink">
          {t('common.clearAll', 'Hammasini tozalash')}
        </button>
      )}
    </div>
  );
}

/** The standard list-page toolbar: a search box that grows to fill the row with
 *  the {@link FilterButton} (and any extra actions) pinned to the end. Using this
 *  everywhere keeps search + Filtr in the exact same place on every page — on
 *  mobile the search shrinks and the button stays a fixed, thumb-reachable size.
 *
 *  `min-w-0` is load-bearing: a flex item defaults to `min-width:auto`, which
 *  floors it at its content width and makes a long localized toolbar overflow a
 *  360px viewport. Deliberately still `nowrap` — the search is `w-full`, so
 *  `flex-wrap` would drop the trigger to a second line at *every* width. */
export function FilterBar({ children, className }: { children: React.ReactNode; className?: string }) {
  return <div className={cn('mb-4 flex min-w-0 items-center gap-2', className)}>{children}</div>;
}

/** Right-side slide-in filter panel (works on mobile). Children are the filter
 *  controls; the footer has Clear + Apply. */
export function FilterDrawer({ open, onClose, onClear, children, title }: {
  open: boolean; onClose: () => void; onClear?: () => void; children: React.ReactNode; title?: string;
}) {
  const { t } = useTranslation();
  const panelRef = useRef<HTMLElement>(null);
  useFocusTrap(panelRef, open, onClose);  // (#161) Esc + focus-trap + return + scroll-lock
  const titleId = useId();
  if (!open) return null;
  // Clear Telegram's floating controls at the very top.
  const topPad = isInsideTelegramClient() ? 'calc(env(safe-area-inset-top) + 3.75rem)' : '0px';
  return (
    <div className="fixed inset-0 z-50 flex justify-end" role="dialog" aria-modal="true"
         aria-labelledby={titleId}>
      <button className="absolute inset-0 bg-ink/30 backdrop-blur-sm" aria-label={t('common.close', 'Yopish')} onClick={onClose} />
      <aside ref={panelRef} className="relative flex h-full w-[min(22rem,92vw)] flex-col border-l border-line bg-cream shadow-pop"
             style={{ paddingTop: topPad }}>
        <div className="flex items-center justify-between border-b border-line px-5 py-4">
          <h2 id={titleId} className="text-subsection font-semibold">{title ?? t('common.filter', 'Filtr')}</h2>
          {/* Named in its own right: the backdrop behind the drawer is a
              different control, and this one is keyboard-reachable. */}
          <button onClick={onClose}
                  aria-label={t('common.closeFilters', 'Filtrlarni yopish')}
                  className="tap-target grid h-11 w-11 place-items-center rounded-control border border-line text-ink-soft hover:text-ink">
            <X size={16} aria-hidden="true" />
          </button>
        </div>
        <div className="flex-1 space-y-5 overflow-y-auto px-5 py-4">{children}</div>
        <div className="flex items-center justify-between gap-2 border-t border-line px-5 py-3">
          <Button variant="ghost" size="md" onClick={onClear}>{t('common.clear', 'Tozalash')}</Button>
          {/* (#654) Filters apply live as options are tapped, so the primary
              action is a close/done affordance — not a draft "Apply" step. */}
          <Button variant="primary" size="md" onClick={onClose}>{t('common.done', 'Tayyor')}</Button>
        </div>
      </aside>
    </div>
  );
}

/** Single-choice pill group — the standard way to pick one value (status, etc.). */
export function RadioGroup<T extends string>({ label, options, value, onChange }: {
  label: string; options: { value: T; label: string; count?: number }[]; value: T; onChange: (v: T) => void;
}) {
  return (
    <div role="radiogroup" aria-label={label}>
      <div className="mb-2 text-micro uppercase tracking-wider text-muted">{label}</div>
      <div className="flex flex-wrap gap-1.5">
        {options.map((o) => (
          <button key={o.value} type="button" role="radio" aria-checked={value === o.value} onClick={() => onChange(o.value)}
                  className={cn('inline-flex items-center gap-1.5 rounded-pill border px-3 py-1.5 text-body transition',
                    value === o.value ? 'border-ink bg-ink text-white' : 'border-line text-ink-soft hover:border-ink/30')}>
            {o.label}
            {o.count != null && o.count > 0 && (
              <span className={cn('grid h-4 min-w-[1rem] place-items-center rounded-pill px-1 text-micro font-semibold',
                value === o.value ? 'bg-white/20 text-white' : 'bg-ink/10 text-ink')}>{o.count}</span>
            )}
          </button>
        ))}
      </div>
    </div>
  );
}

export type DatePreset = 'all' | 'today' | '7d' | '30d' | '90d';

/** Single-choice "by time" filter. Presets cover the common windows; pages map
 *  the chosen preset to a date field via {@link matchesDatePreset}. */
export function DateRangeGroup({ label, value, onChange }: {
  label?: string; value: DatePreset; onChange: (v: DatePreset) => void;
}) {
  const { t } = useTranslation();
  const opts: { value: DatePreset; label: string }[] = [
    { value: 'all',   label: t('common.all', 'Barchasi') },
    { value: 'today', label: t('filters.today', 'Bugun') },
    { value: '7d',    label: t('filters.last7', "7 kun") },
    { value: '30d',   label: t('filters.last30', '30 kun') },
    { value: '90d',   label: t('filters.last90', '90 kun') },
  ];
  return (
    <div>
      <div className="mb-2 text-micro uppercase tracking-wider text-muted">{label ?? t('filters.byTime', 'Vaqt bo‘yicha')}</div>
      <div className="flex flex-wrap gap-1.5">
        {opts.map((o) => (
          <button key={o.value} type="button" onClick={() => onChange(o.value)}
                  className={cn('rounded-pill border px-3 py-1.5 text-body transition',
                    value === o.value ? 'border-ink bg-ink text-white' : 'border-line text-ink-soft hover:border-ink/30')}>
            {o.label}
          </button>
        ))}
      </div>
    </div>
  );
}

/** True if an ISO timestamp falls within the chosen preset window (client-side). */
export function matchesDatePreset(iso: string | null | undefined, preset: DatePreset): boolean {
  if (preset === 'all') return true;
  if (!iso) return false;
  const t = new Date(iso).getTime();
  if (Number.isNaN(t)) return false;
  const now = new Date();
  if (preset === 'today') {
    const start = new Date(now.getFullYear(), now.getMonth(), now.getDate()).getTime();
    return t >= start;
  }
  const days = preset === '7d' ? 7 : preset === '30d' ? 30 : 90;
  return t >= now.getTime() - days * 86_400_000;
}

/**
 * A labelled group of checkable option rows.
 *
 * Scales to long lists (categories, brands…) the enterprise way instead of
 * dumping 200 rows that bury every filter below them:
 *   • > 8 options  → a search box filters the list, capped to a scrollable area.
 *   • > 12 options → the whole group COLLAPSES behind its label (with a selected
 *     count), so the drawer stays scannable; tap to expand + search.
 * Short lists render exactly as before. Thresholds are overridable via props.
 */
export function CheckGroup({
  label, options, value, onChange, searchPlaceholder, collapsible, searchable,
}: {
  label: string;
  options: { value: string; label: string }[];
  value: string[];
  onChange: (v: string[]) => void;
  searchPlaceholder?: string;
  /** Force collapse-behind-header on/off. Defaults to true when > 12 options. */
  collapsible?: boolean;
  /** Force the search box on/off. Defaults to true when > 8 options. */
  searchable?: boolean;
}) {
  const { t } = useTranslation();
  const isCollapsible = collapsible ?? options.length > 12;
  const isSearchable = searchable ?? options.length > 8;
  const [open, setOpen] = useState(!isCollapsible);
  const [q, setQ] = useState('');

  const toggle = (v: string) =>
    onChange(value.includes(v) ? value.filter((x) => x !== v) : [...value, v]);
  const ql = q.trim().toLowerCase();
  const shown = ql ? options.filter((o) => o.label.toLowerCase().includes(ql)) : options;

  const rows = (
    <div className={cn('space-y-1', isSearchable && 'max-h-56 overflow-y-auto')}>
      {shown.map((o) => {
        const sel = value.includes(o.value);
        return (
          <button key={o.value} type="button" role="checkbox" aria-checked={sel} onClick={() => toggle(o.value)}
                  className={cn('flex w-full items-center justify-between gap-2 rounded-control border px-3 py-2 text-left text-body transition',
                    sel ? 'border-ink bg-ink/5 text-ink' : 'border-line text-ink-soft hover:border-ink/30')}>
            <span className="min-w-0 truncate">{o.label}</span>
            <span className={cn('grid h-4 w-4 shrink-0 place-items-center rounded-micro border', sel ? 'border-ink bg-ink text-white' : 'border-line')}>
              {sel && <Check size={12} />}
            </span>
          </button>
        );
      })}
      {shown.length === 0 && <div className="text-caption text-muted">—</div>}
    </div>
  );

  return (
    <div role="group" aria-label={label}>
      {isCollapsible ? (
        <button type="button" onClick={() => setOpen((o) => !o)} aria-expanded={open}
                className="mb-2 flex w-full items-center justify-between gap-2 text-left">
          <span className="text-micro uppercase tracking-wider text-muted">{label}</span>
          <span className="flex items-center gap-1.5 text-muted">
            {value.length > 0 && (
              <span className="grid h-4 min-w-[1rem] place-items-center rounded-pill bg-ink px-1 text-micro font-semibold text-white">{value.length}</span>
            )}
            <ChevronDown size={14} className={cn('transition-transform', open && 'rotate-180')} />
          </span>
        </button>
      ) : (
        <div className="mb-2 text-micro uppercase tracking-wider text-muted">{label}</div>
      )}
      {open && (
        <div className="space-y-2">
          {isSearchable && (
            // text-subsection (16px) stops iOS Safari's focus-zoom, like the other inputs.
            <input
              value={q}
              onChange={(e) => setQ(e.target.value)}
              placeholder={searchPlaceholder ?? t('common.search', 'Qidirish…') ?? ''}
              aria-label={`${label} — ${t('common.search', 'Qidirish')}`}
              className="h-9 w-full rounded-control border border-line bg-surface px-3 text-subsection outline-none placeholder:text-muted focus:border-ink/25 focus:bg-white"
            />
          )}
          {rows}
        </div>
      )}
    </div>
  );
}

/** A min–max numeric range (amount, count, days…). Both bounds are optional, so
 *  the user can express "≥ 1 000 000", "≤ 500", or a closed window. Values are
 *  controlled strings ('' = unbounded) so the inputs can be cleared cleanly. */
export function RangeGroup({ label, value, onChange, unit, placeholderMin, placeholderMax, step = 1 }: {
  label: string;
  value: { min: string; max: string };
  onChange: (next: { min: string; max: string }) => void;
  unit?: string;
  placeholderMin?: string;
  placeholderMax?: string;
  step?: number;
}) {
  const { t } = useTranslation();
  // text-subsection (16px) matches SearchInput — stops iOS Safari's focus-zoom.
  const inputCls = 'h-10 min-w-0 flex-1 rounded-control border border-line bg-surface px-3 text-subsection tabular-nums outline-none placeholder:text-muted focus:border-ink/25 focus:bg-white';
  const minPh = placeholderMin ?? t('filters.from', 'dan');
  const maxPh = placeholderMax ?? t('filters.to', 'gacha');
  return (
    <div>
      <div className="mb-2 text-micro uppercase tracking-wider text-muted">{unit ? `${label} · ${unit}` : label}</div>
      <div className="flex items-center gap-2">
        <input type="number" inputMode="numeric" min={0} step={step} value={value.min}
               onChange={(e) => onChange({ ...value, min: e.target.value })}
               placeholder={minPh} aria-label={`${label}: ${minPh}`} className={inputCls} />
        <span className="shrink-0 text-muted" aria-hidden>–</span>
        <input type="number" inputMode="numeric" min={0} step={step} value={value.max}
               onChange={(e) => onChange({ ...value, max: e.target.value })}
               placeholder={maxPh} aria-label={`${label}: ${maxPh}`} className={inputCls} />
      </div>
    </div>
  );
}

/** True if a numeric value falls inside the (optional) min/max bounds. Empty
 *  bounds are open; a row whose value is null/NaN is excluded once any bound is
 *  set (so "≥ 1M" doesn't silently keep unknown-amount rows). */
export function matchesRange(value: number | null | undefined, range: { min: string; max: string }): boolean {
  const lo = range.min.trim() === '' ? null : Number(range.min);
  const hi = range.max.trim() === '' ? null : Number(range.max);
  if (lo == null && hi == null) return true;            // no bound → no filter
  if (value == null || Number.isNaN(value)) return false;
  if (lo != null && !Number.isNaN(lo) && value < lo) return false;
  if (hi != null && !Number.isNaN(hi) && value > hi) return false;
  return true;
}

/** Active iff at least one bound is set — for the FilterButton count badge. */
export function rangeActive(range: { min: string; max: string }): boolean {
  return range.min.trim() !== '' || range.max.trim() !== '';
}
