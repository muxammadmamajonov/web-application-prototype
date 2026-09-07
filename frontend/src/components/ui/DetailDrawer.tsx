import { type CSSProperties, type ReactNode, useId, useRef } from 'react';
import { createPortal } from 'react-dom';
import { X } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import { cn } from '@/lib/cn';
import { isInsideTelegramClient } from '@/lib/telegram';
import { useFocusTrap } from '@/hooks/useFocusTrap';
import { useBottomObstruction } from '@/hooks/useBottomObstruction';

const WIDTHS = {
  md: 'sm:w-[28rem]',
  lg: 'sm:w-[34rem]',
  xl: 'sm:w-[42rem]',
} as const;

/**
 * Right-side detail panel — the desktop standard for "open a record" surfaces,
 * replacing cramped centered modals. Roomy, sectioned, with a sticky header +
 * footer, a subtle backdrop, focus-trap, Esc-to-close, and focus-return. On a
 * phone it is a bottom sheet that HUGS ITS CONTENT. Pair with
 * {@link DetailSection} / {@link DangerZone} for a consistent, premium layout.
 *
 * Why the phone presentation is a bottom sheet and not a full-height panel:
 * these records are short — a top-up request is five key/value rows — and a
 * full-height sheet stretched that over an 800px phone, marooning the
 * approve/reject buttons at the bottom of the screen with half a viewport of
 * blank cream between them and the last row. The actions read as unrelated to
 * the record they act on. Hugging the content keeps them together, and
 * `max-h-[90svh]` means a long record still scrolls instead of overflowing
 * (`svh`, not `vh`: with Telegram's chrome and a mobile URL bar, `vh` overshoots
 * the visible area and pushes the footer off-screen).
 */
export function DetailDrawer({
  open, onClose, title, subtitle, eyebrow, badge, leading, children, footer, width = 'lg',
}: {
  open: boolean;
  onClose: () => void;
  title: ReactNode;
  subtitle?: ReactNode;
  /** Small uppercase label above the title (e.g. a record type). */
  eyebrow?: ReactNode;
  /** Right-aligned status badge in the header. */
  badge?: ReactNode;
  /** Optional leading slot left of the title block — e.g. an Avatar/logo. */
  leading?: ReactNode;
  children: ReactNode;
  footer?: ReactNode;
  width?: keyof typeof WIDTHS;
}) {
  const panelRef = useRef<HTMLElement>(null);
  useFocusTrap(panelRef, open, onClose); // Esc + focus-trap + return + scroll-lock
  const { t } = useTranslation();
  const titleId = useId();
  // Same contract as the Modal footer: on a phone this sheet is anchored to the
  // bottom, so its action bar is what a picker inside the drawer has to clear.
  // On ≥sm the drawer is a full-height side panel and its footer still sits on
  // the bottom edge — measuring covers both without a breakpoint here.
  const footerRef = useBottomObstruction<HTMLDivElement>(open && !!footer);
  if (!open) return null;
  // Clears Telegram's own top chrome — but ONLY on the ≥sm side panel, which is
  // anchored to the top of the viewport and would otherwise sit under it. The
  // phone sheet is anchored to the BOTTOM, so the same padding there was just a
  // blank band above the title. Passed as a CSS variable because the choice is
  // per-breakpoint and an inline style cannot express a media query.
  const topPad = isInsideTelegramClient() ? 'calc(var(--tg-safe-top) + 3.5rem)' : '1rem';

  // role="dialog" + aria-modal MUST carry an accessible name, or this opens
  // silently for a screen-reader user. Named from its own visible heading.
  return createPortal(
    <div className="fixed inset-0 z-[85] flex items-end justify-end sm:items-stretch"
         role="dialog" aria-modal="true" aria-labelledby={titleId}>
      <button type="button" aria-label={t('common.close', 'Yopish')} onClick={onClose}
              className="absolute inset-0 bg-ink/50 backdrop-blur-sm" />
      <aside ref={panelRef}
             style={{ '--drawer-top-pad': topPad } as CSSProperties}
             className={cn(
               'relative flex w-full flex-col overflow-hidden bg-cream shadow-pop',
               // Phone: bottom sheet, height follows the content.
               'max-h-[90svh] rounded-t-card',
               // ≥sm: the full-height right-side panel, unchanged.
               'sm:h-full sm:max-h-none sm:rounded-none sm:border-l sm:border-line',
               WIDTHS[width],
             )}>
        {/* Grab handle — the phone sheet's dismiss affordance. Inside Telegram
            the only other visible ✕ belongs to Telegram and closes the whole
            Mini App, so the sheet has to advertise its own way out. */}
        <div className="shrink-0 pb-0.5 pt-2.5 sm:hidden" aria-hidden="true">
          <div className="mx-auto h-1 w-9 rounded-pill bg-ink/15" />
        </div>
        <header className="flex shrink-0 items-start justify-between gap-3 border-b border-line bg-surface px-5 pb-4 pt-2 sm:pt-[var(--drawer-top-pad)]">
          <div className="flex min-w-0 items-center gap-3">
            {leading && <div className="shrink-0">{leading}</div>}
            <div className="min-w-0">
              {eyebrow && <div className="mb-0.5 text-micro font-medium uppercase tracking-wider text-muted">{eyebrow}</div>}
              <div className="flex items-center gap-2">
                <h2 id={titleId} className="truncate text-section font-semibold leading-tight">{title}</h2>
                {badge}
              </div>
              {subtitle && <p className="mt-0.5 truncate text-small text-muted">{subtitle}</p>}
            </div>
          </div>
          <button onClick={onClose} aria-label={t('common.close', 'Yopish')}
                  className="grid h-9 w-9 tap-target shrink-0 place-items-center rounded-control text-ink-soft hover:bg-ink/5 hover:text-ink">
            <X size={18} />
          </button>
        </header>
        {/* min-h-0 lets this shrink below its content so the sheet's max-height
            produces a scroll here rather than pushing the footer out of view. */}
        <div className="min-h-0 flex-1 space-y-5 overflow-y-auto px-5 py-5">{children}</div>
        {footer && (
          // On a phone the actions share the width — two 44px-tall thumb targets
          // beat two small right-aligned buttons in the corner. Desktop keeps
          // them natural-width and right-aligned.
          <div
            ref={footerRef}
            data-testid="drawer-footer"
            className="flex shrink-0 items-center justify-end gap-2 border-t border-line bg-surface px-5 py-3 pb-[max(0.75rem,env(safe-area-inset-bottom))] [&>button]:flex-1 sm:[&>button]:flex-initial">
            {footer}
          </div>
        )}
      </aside>
    </div>,
    document.body,
  );
}

/** A titled section inside a {@link DetailDrawer}. */
export function DetailSection({ title, action, children }: { title?: ReactNode; action?: ReactNode; children: ReactNode }) {
  return (
    <section>
      {(title || action) && (
        <div className="mb-2 flex items-center justify-between">
          {title && <h3 className="text-micro font-semibold uppercase tracking-wider text-muted">{title}</h3>}
          {action}
        </div>
      )}
      {children}
    </section>
  );
}

/** Key/value row for detail panels — label left, value right, hairline between. */
export function DetailRow({ label, children }: { label: ReactNode; children: ReactNode }) {
  return (
    <div className="flex items-center justify-between gap-4 border-b border-line/70 py-2 last:border-0">
      <span className="shrink-0 text-small text-muted">{label}</span>
      <span className="min-w-0 truncate text-right text-body font-medium">{children}</span>
    </div>
  );
}

/** The destructive-actions block — visually separated, danger-tinted, at the
 *  bottom of a detail panel. Keeps delete/block out of the safe content. */
export function DangerZone({ title, description, children }: { title: ReactNode; description?: ReactNode; children: ReactNode }) {
  return (
    <section className="rounded-card border border-danger/30 bg-[color:var(--c-danger-soft)] p-4">
      <h3 className="text-body font-semibold text-danger-strong">{title}</h3>
      {description && <p className="mt-0.5 text-caption text-ink-soft">{description}</p>}
      <div className="mt-3 flex flex-wrap gap-2">{children}</div>
    </section>
  );
}
