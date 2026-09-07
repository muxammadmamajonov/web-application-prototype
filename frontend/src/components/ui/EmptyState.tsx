import type { ReactNode } from 'react';
import { cn } from '@/lib/cn';

/**
 * The standard empty / no-data panel — a centered card with an icon, title, an
 * optional body line, and an optional action (CTA). One consistent look for
 * "nothing here yet" / "no results" across the app, replacing the per-page
 * hand-rolled cards and the old `EmptyCard` that awkwardly lived inside
 * CustomersPage.
 *
 *  tone: `muted` (default, neutral grey icon) or `accent` (brand-tinted icon, for
 *  marketing / first-run states).
 */
export function EmptyState({ icon, title, body, action, tone = 'muted', size = 'default', className }: {
  icon?: ReactNode;
  title: string;
  body?: string;
  /** Optional CTA(s) under the text — e.g. a <Button>, plus a caption. Stacked. */
  action?: ReactNode;
  tone?: 'muted' | 'accent';
  /** `compact` trims the vertical whitespace + icon so a phone empty state doesn't
   *  dominate the viewport; it still grows to a comfortable size on ≥sm screens. */
  size?: 'default' | 'compact';
  className?: string;
}) {
  const compact = size === 'compact';
  return (
    <div
      className={cn(
        'card flex flex-col items-center gap-3 text-center',
        compact ? 'px-5 py-9 sm:py-12' : 'px-6 py-16',
        className,
      )}
    >
      {icon && (
        <div
          className={cn(
            'mb-1 grid place-items-center rounded-card',
            compact ? 'h-12 w-12' : 'h-14 w-14',
            tone === 'accent' ? 'bg-[color:var(--c-accent-soft)] text-accent' : 'bg-cream text-muted',
          )}
        >
          {icon}
        </div>
      )}
      <h3 className="text-section font-semibold tracking-tight">{title}</h3>
      {body && <p className="max-w-panel text-body text-ink-soft">{body}</p>}
      {action && <div className="mt-2 flex flex-col items-center gap-2">{action}</div>}
    </div>
  );
}
