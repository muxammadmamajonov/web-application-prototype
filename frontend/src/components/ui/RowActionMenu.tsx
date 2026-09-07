import { useEffect, useLayoutEffect, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { MoreHorizontal, type LucideIcon } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import { cn } from '@/lib/cn';

/** One entry in a {@link RowActionMenu}. Mark `destructive` to render it in the
 *  danger style, visually separated below the safe actions (spec §9). */
export interface RowAction {
  key: string;
  label: string;
  icon?: LucideIcon;
  onClick: () => void;
  destructive?: boolean;
  disabled?: boolean;
  hidden?: boolean;
}

/**
 * The standard row "⋯" overflow menu for list/table/card rows.
 *
 * The desktop dropdown is rendered in a **portal** to `document.body` and
 * positioned from the trigger's viewport rect, so it can NEVER be clipped by a
 * table/card `overflow` ancestor (the long-standing bug where the last/only
 * row's menu was cut off). It flips up when there isn't room below and aligns
 * to the right edge, re-measuring on scroll/resize. Mobile (`<sm`) keeps the
 * bottom sheet. Esc, outside-click, scroll, and running an action all close it.
 *
 * Only use this when a row has 2+ meaningful actions. If the only action is
 * "open detail", make the ROW clickable instead — don't add a menu.
 */
export function RowActionMenu({ actions, ariaLabel, className }: {
  actions: RowAction[];
  ariaLabel?: string;
  className?: string;
}) {
  const { t } = useTranslation();
  const [open, setOpen] = useState(false);
  const triggerRef = useRef<HTMLButtonElement>(null);
  const sheetRef = useRef<HTMLDivElement>(null);
  const dropRef = useRef<HTMLDivElement>(null);
  const [pos, setPos] = useState<{ top: number; left: number; placement: 'down' | 'up' } | null>(null);
  const visible = actions.filter((a) => !a.hidden);

  const MENU_W = 224; // sm:w-56
  const place = () => {
    const r = triggerRef.current?.getBoundingClientRect();
    if (!r) return;
    const vh = window.innerHeight;
    const vw = window.innerWidth;
    const estH = Math.min(visible.length * 44 + 12, 360);
    const below = vh - r.bottom;
    const placement: 'down' | 'up' = below < estH + 12 && r.top > below ? 'up' : 'down';
    const top = placement === 'down' ? r.bottom + 6 : r.top - 6 - estH;
    // Right-align to the trigger, clamped into the viewport.
    const left = Math.max(8, Math.min(r.right - MENU_W, vw - MENU_W - 8));
    setPos({ top: Math.max(8, top), left, placement });
  };

  useLayoutEffect(() => {
    if (open) place();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open]);

  // Move focus into the menu on open so arrow keys have somewhere to start.
  useEffect(() => {
    if (!open) return;
    const id = requestAnimationFrame(() => {
      (dropRef.current ?? sheetRef.current)
        ?.querySelector<HTMLButtonElement>('[role="menuitem"]:not([disabled])')
        ?.focus();
    });
    return () => cancelAnimationFrame(id);
  }, [open]);

  useEffect(() => {
    if (!open) return;
    const onDoc = (e: MouseEvent) => {
      const tgt = e.target as Node;
      if (!triggerRef.current?.contains(tgt) && !sheetRef.current?.contains(tgt) && !dropRef.current?.contains(tgt)) setOpen(false);
    };
    // role="menu"/"menuitem" promise arrow-key traversal, so it must actually be
    // implemented — and focus restored to the trigger on close, or the user
    // loses their place in the row.
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') { e.preventDefault(); setOpen(false); triggerRef.current?.focus(); return; }
      const nodes = Array.from(
        (dropRef.current ?? sheetRef.current)?.querySelectorAll<HTMLButtonElement>('[role="menuitem"]:not([disabled])') ?? [],
      );
      if (nodes.length === 0) return;
      const at = nodes.indexOf(document.activeElement as HTMLButtonElement);
      if (e.key === 'ArrowDown') { e.preventDefault(); nodes[at < 0 ? 0 : (at + 1) % nodes.length]?.focus(); }
      else if (e.key === 'ArrowUp') { e.preventDefault(); nodes[at <= 0 ? nodes.length - 1 : at - 1]?.focus(); }
      else if (e.key === 'Home') { e.preventDefault(); nodes[0]?.focus(); }
      else if (e.key === 'End') { e.preventDefault(); nodes[nodes.length - 1]?.focus(); }
      else if (e.key === 'Tab') { setOpen(false); }
    };
    const onScroll = () => place();
    document.addEventListener('mousedown', onDoc);
    document.addEventListener('keydown', onKey);
    window.addEventListener('scroll', onScroll, true);
    window.addEventListener('resize', onScroll);
    return () => {
      document.removeEventListener('mousedown', onDoc);
      document.removeEventListener('keydown', onKey);
      window.removeEventListener('scroll', onScroll, true);
      window.removeEventListener('resize', onScroll);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open]);

  if (visible.length === 0) return null;

  const run = (e: React.MouseEvent, a: RowAction) => {
    e.stopPropagation();
    if (a.disabled) return;
    setOpen(false);
    triggerRef.current?.focus();
    a.onClick();
  };

  const items = (
    <div role="menu" aria-label={ariaLabel ?? t('common.actions', 'Amallar')} onClick={(e) => e.stopPropagation()}>
      {visible.map((a, i) => {
        const Icon = a.icon;
        const firstDestructive = a.destructive && i > 0 && !visible[i - 1].destructive;
        return (
          <div key={a.key}>
            {firstDestructive && <div className="my-1 h-px bg-line" role="separator" />}
            <button
              type="button"
              role="menuitem"
              disabled={a.disabled}
              onClick={(e) => run(e, a)}
              className={cn(
                'flex w-full items-center gap-2.5 rounded-icon px-3 py-2.5 text-left text-body transition disabled:cursor-not-allowed disabled:opacity-40',
                a.destructive ? 'text-danger hover:bg-danger/10' : 'text-ink hover:bg-ink/5',
              )}
            >
              {Icon && <Icon size={16} className="shrink-0" />}
              {a.label}
            </button>
          </div>
        );
      })}
    </div>
  );

  return (
    <div className={cn('inline-block', className)}>
      <button
        ref={triggerRef}
        type="button"
        aria-haspopup="menu"
        aria-expanded={open}
        aria-label={ariaLabel ?? t('common.actions', 'Amallar')}
        onClick={(e) => { e.stopPropagation(); setOpen((o) => !o); }}
        className="grid h-11 w-11 tap-target place-items-center rounded-control text-ink-soft transition hover:bg-ink/5 hover:text-ink data-[open=true]:bg-ink/5"
        data-open={open}
      >
        <MoreHorizontal size={18} aria-hidden="true" />
      </button>

      {open && createPortal(
        <>
          {/* Mobile scrim + bottom sheet. */}
          <div className="fixed inset-0 z-[110] bg-ink/30 backdrop-blur-sm sm:hidden"
               onClick={(e) => { e.stopPropagation(); setOpen(false); }} aria-hidden />
          <div ref={sheetRef}
               onClick={(e) => e.stopPropagation()}
               className="fixed inset-x-0 bottom-0 z-[111] border border-line bg-surface p-1.5 shadow-pop rounded-t-card pb-[max(0.5rem,env(safe-area-inset-bottom))] sm:hidden">
            {items}
          </div>
          {/* Desktop: portaled, viewport-positioned dropdown — never clipped. */}
          <div ref={dropRef}
               style={{ top: pos?.top, left: pos?.left }}
               onClick={(e) => e.stopPropagation()}
               className="fixed z-[111] hidden w-56 rounded-card border border-line bg-surface p-1 shadow-pop sm:block">
            {items}
          </div>
        </>,
        document.body,
      )}
    </div>
  );
}
