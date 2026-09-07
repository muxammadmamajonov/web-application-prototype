import { useEffect, useId, useLayoutEffect, useMemo, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { useTranslation } from 'react-i18next';
import { Check, ChevronDown, Search } from 'lucide-react';
import { cn } from '@/lib/cn';
import { useAnchoredOverlay } from '@/hooks/useAnchoredOverlay';
import { inputCls } from './Modal';

export interface SelectOption {
  value: string;
  label: string;
  /** Optional small description shown under the label in the dropdown. */
  hint?: string;
}

/** Device-independent dropdown — replaces the native <select> so the UI looks
 *  identical on every phone/desktop. Keyboard accessible (Arrows/Enter/Esc/
 *  Home/End), ARIA listbox roles, click-outside close, and a search box that
 *  appears automatically for long option lists. */
export function Select({
  value, onChange, options, placeholder, disabled, id, className, searchable,
}: {
  value: string;
  onChange: (v: string) => void;
  options: SelectOption[];
  placeholder?: string;
  disabled?: boolean;
  id?: string;
  className?: string;
  /** Force-show the search box (otherwise it appears when options > 8). */
  searchable?: boolean;
}) {
  const { t } = useTranslation();
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState('');
  const [active, setActive] = useState(0);
  const ref = useRef<HTMLDivElement>(null);
  const panelRef = useRef<HTMLDivElement>(null);
  const listRef = useRef<HTMLUListElement>(null);
  // (#639) Stable ids so we can wire aria-controls + aria-activedescendant per
  // the WAI-ARIA APG combobox pattern — a screen reader can then announce the
  // arrow-highlighted (not-yet-selected) option, which CSS background alone can't.
  const baseId = useId();
  const listId = `${baseId}-list`;
  const optionId = (i: number) => `${baseId}-opt-${i}`;
  // (H19) The panel is PORTALLED to <body> and positioned against the visual
  // viewport by the shared `useAnchoredOverlay` — the same hook DatePicker /
  // DateRangePicker / TimePicker use. Laid out in place it belongs to whatever
  // `overflow-hidden`/`overflow-y-auto` ancestor holds the field (a Modal's
  // dialog card), which visually clips it.
  //
  // A local `rect.bottom + 8` would portal the panel out of the clip and then
  // drop it straight through the bottom of a 360px Telegram screen: the hook is
  // what subtracts the bottom nav / soft keyboard (`bottomObstruction`), flips
  // above when there is no room below, clamps horizontally, and caps the height
  // to the band that is actually reachable. Reuse it; do not re-measure here.
  const [anchorWidth, setAnchorWidth] = useState(0);
  const { triggerRef: anchorRef, pos } = useAnchoredOverlay(open, anchorWidth);
  // The panel tracks the trigger's width, so the hook's preferred width has to
  // be measured rather than a constant. Runs before paint and the hook
  // re-measures when it changes, so the first open never paints at width 0.
  useLayoutEffect(() => {
    if (!open) return;
    const w = anchorRef.current?.offsetWidth;
    if (w) setAnchorWidth(w);
  }, [open, anchorRef]);

  const showSearch = searchable ?? options.length > 8;
  const filtered = useMemo(() => {
    if (!query.trim()) return options;
    const q = query.toLowerCase();
    return options.filter((o) => o.label.toLowerCase().includes(q) || o.hint?.toLowerCase().includes(q));
  }, [options, query]);

  useEffect(() => {
    if (!open) { setQuery(''); return; }
    // The panel is portalled outside `ref`, so both roots count as "inside".
    const onDoc = (e: MouseEvent) => {
      const target = e.target as Node;
      if (ref.current?.contains(target) || panelRef.current?.contains(target)) return;
      setOpen(false);
    };
    document.addEventListener('mousedown', onDoc);
    const idx = options.findIndex((o) => o.value === value);
    setActive(idx >= 0 ? idx : 0);
    return () => {
      document.removeEventListener('mousedown', onDoc);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open]);

  useEffect(() => { setActive(0); }, [query]);

  useEffect(() => {
    if (!open) return;
    (listRef.current?.children[active] as HTMLElement | undefined)?.scrollIntoView({ block: 'nearest' });
  }, [active, open]);

  const current = options.find((o) => o.value === value);
  const choose = (v: string) => { onChange(v); setOpen(false); };
  const activeId = open && filtered.length > 0 ? optionId(active) : undefined;

  const onKeyDown = (e: React.KeyboardEvent) => {
    if (!open && (e.key === 'Enter' || e.key === ' ' || e.key === 'ArrowDown')) {
      e.preventDefault(); setOpen(true); return;
    }
    if (!open) return;
    if (e.key === 'Escape') { e.preventDefault(); setOpen(false); }
    else if (e.key === 'ArrowDown') { e.preventDefault(); setActive((a) => Math.min(filtered.length - 1, a + 1)); }
    else if (e.key === 'ArrowUp') { e.preventDefault(); setActive((a) => Math.max(0, a - 1)); }
    else if (e.key === 'Home') { e.preventDefault(); setActive(0); }
    else if (e.key === 'End') { e.preventDefault(); setActive(filtered.length - 1); }
    else if (e.key === 'Enter') { e.preventDefault(); const o = filtered[active]; if (o) choose(o.value); }
  };

  return (
    <div ref={ref} className={cn('relative', className)} onKeyDown={onKeyDown}>
      <button
        ref={(el) => { anchorRef.current = el; }}
        type="button"
        id={id}
        disabled={disabled}
        role="combobox"
        aria-expanded={open}
        aria-haspopup="listbox"
        aria-controls={open ? listId : undefined}
        aria-activedescendant={activeId}
        onClick={() => setOpen((v) => !v)}
        className={cn(inputCls, 'flex items-center justify-between gap-2 text-left disabled:opacity-50', !current && 'text-muted')}
      >
        <span className="truncate">{current ? current.label : (placeholder ?? '—')}</span>
        <ChevronDown size={16} className={cn('shrink-0 text-muted transition', open && 'rotate-180')} />
      </button>

      {open && pos && createPortal(
        <div
          ref={panelRef}
          data-testid="select-panel"
          style={{
            top: pos.top, left: pos.left, width: pos.width,
            maxHeight: pos.maxHeight, transform: pos.transform,
          }}
          // z-111 is the shared layer for portalled popovers (see RowActionMenu
          // / DatePicker). Fixed positioning + a portal to <body> is what keeps
          // this panel from being clipped by a Modal's overflow-hidden card.
          className="fixed z-[111] overflow-y-auto overscroll-contain rounded-card border border-line bg-surface p-1 shadow-pop">
          {showSearch && (
            <div className="flex items-center gap-2 border-b border-line px-2.5 pb-2 pt-1">
              <Search size={14} className="shrink-0 text-muted" />
              <input
                autoFocus
                value={query}
                onChange={(e) => setQuery(e.target.value)}
                role="combobox"
                aria-expanded
                aria-controls={listId}
                aria-activedescendant={activeId}
                aria-autocomplete="list"
                aria-label={t('common.search', 'Qidirish…')}
                placeholder={t('common.search', 'Qidirish…')}
                // text-subsection (16px) — stops iOS Safari zooming in when this
                // dropdown search field gains focus.
                className="min-w-0 flex-1 bg-transparent text-subsection outline-none placeholder:text-muted"
              />
            </div>
          )}
          <ul ref={listRef} id={listId} role="listbox" className="overflow-y-auto">
            {filtered.length === 0 && (
              <li className="px-3 py-3 text-center text-caption text-muted">{t('common.noResults', 'Topilmadi')}</li>
            )}
            {filtered.map((o, i) => {
              const sel = o.value === value;
              return (
                <li key={o.value} id={optionId(i)} role="option" aria-selected={sel}>
                  <button
                    type="button"
                    onMouseEnter={() => setActive(i)}
                    onMouseDown={(e) => { e.preventDefault(); choose(o.value); }}
                    className={cn(
                      'flex w-full items-center justify-between gap-2 rounded-control px-3 py-2 text-left text-body transition',
                      sel ? 'bg-ink text-white' : i === active ? 'bg-ink/5 text-ink' : 'text-ink-soft',
                    )}
                  >
                    <span className="min-w-0">
                      <span className="block truncate">{o.label}</span>
                      {o.hint && <span className={cn('block truncate text-micro', sel ? 'text-white/70' : 'text-muted')}>{o.hint}</span>}
                    </span>
                    {sel && <Check size={16} className="shrink-0" />}
                  </button>
                </li>
              );
            })}
          </ul>
        </div>,
        document.body,
      )}
    </div>
  );
}
