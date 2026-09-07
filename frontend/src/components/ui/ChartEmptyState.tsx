import type { ReactNode } from 'react';
import { cn } from '@/lib/cn';

/**
 * Strong empty state for a chart that has no data yet — an accent icon chip, a
 * title, a short body, and an optional fainter hint line, centered in a dashed
 * well that keeps the chart card useful (not a weak blank box) at zero data.
 *
 * Sized to sit in place of a chart: it fills the card height (`h-full`) with a
 * sensible minimum, so the panel keeps its shape whether the chart renders or
 * the empty state does. Kept compact on mobile per the dashboard contract.
 */
export function ChartEmptyState({ icon, title, body, hint, className }: {
  icon: ReactNode;
  title: string;
  body: string;
  /** Optional muted line under the body — e.g. how the metric is computed. */
  hint?: string;
  className?: string;
}) {
  return (
    <div
      className={cn(
        'flex h-full min-h-[200px] flex-col items-center justify-center gap-2 rounded-xl border border-dashed border-line px-6 py-8 text-center sm:min-h-[220px]',
        className,
      )}
    >
      <span className="grid h-12 w-12 place-items-center rounded-2xl bg-[color:var(--c-accent-soft)] text-accent">
        {icon}
      </span>
      <div className="text-subsection font-semibold tracking-tight">{title}</div>
      <p className="max-w-sm text-caption text-muted">{body}</p>
      {hint && <p className="mt-0.5 max-w-sm text-micro text-muted">{hint}</p>}
    </div>
  );
}
