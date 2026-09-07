import type { ReactNode } from 'react';
import { cn } from '@/lib/cn';

/**
 * The canonical dashboard panel: a `.card` with a title row (optional leading
 * icon chip + one-line meta), an optional right-aligned action, the body, and an
 * optional divided footer. Promoted from the per-page `Panel` helpers that used
 * to live inside the platform/tenant overview pages so every dashboard composes
 * sections the same way (one title weight, one header rhythm, one footer rule).
 *
 * Padding is responsive (16px mobile → 20px desktop) so sections feel dense on a
 * phone without becoming cramped, matching the StatCard / KpiCard rhythm.
 */
export function SectionCard({
  title, meta, icon, action, footer, className, bodyClassName, children,
}: {
  title?: ReactNode;
  meta?: ReactNode;
  /** Small tinted leading chip (e.g. a lucide icon) shown left of the title. */
  icon?: ReactNode;
  /** Right-aligned action — a link/button. Kept on the title baseline. */
  action?: ReactNode;
  /** Optional footer below a hairline rule — e.g. a "view all" button. */
  footer?: ReactNode;
  className?: string;
  bodyClassName?: string;
  children: ReactNode;
}) {
  const hasHeader = title != null || action != null;
  return (
    <section className={cn('card flex flex-col p-4 sm:p-5', className)}>
      {hasHeader && (
        <header className="mb-4 flex items-start justify-between gap-3">
          <div className="flex min-w-0 items-center gap-2.5">
            {icon && (
              <span className="grid h-8 w-8 shrink-0 place-items-center rounded-control bg-cream text-ink-soft">
                {icon}
              </span>
            )}
            <div className="min-w-0">
              {title != null && (
                <h3 className="truncate text-subsection font-semibold tracking-tight">{title}</h3>
              )}
              {meta != null && <p className="mt-0.5 truncate text-caption text-muted">{meta}</p>}
            </div>
          </div>
          {action && <div className="shrink-0">{action}</div>}
        </header>
      )}

      <div className={cn('min-w-0 flex-1', bodyClassName)}>{children}</div>

      {footer && <div className="mt-4 border-t border-line pt-3">{footer}</div>}
    </section>
  );
}
