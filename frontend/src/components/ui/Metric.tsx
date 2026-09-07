import type { ReactNode } from 'react';
import { cn } from '@/lib/cn';
import { Skeleton } from '@/components/ui/Skeleton';

/**
 * A single labelled metric — colored status dot + caption label, a large
 * tabular-figures value (with an optional trailing unit), and an optional hint
 * line. The building block for the cash-flow / coverage style mini-metric cards,
 * replacing the per-page `MoneyBlock` / inline `<div>` duplicates.
 *
 * The value scales 20px → 28px (`text-section` → `text-title`) so two metrics sit
 * side-by-side without overflow on a 360px phone, then grow on desktop.
 */
export function Metric({
  label, value, unit, hint, dot, loading, mono = true, className,
}: {
  label: ReactNode;
  value: ReactNode;
  unit?: string;
  hint?: ReactNode;
  /** Tailwind bg-* class for the status dot (e.g. `bg-success`). Omit for none. */
  dot?: string;
  loading?: boolean;
  /** Monospaced tabular value (default) — turn off for non-numeric values. */
  mono?: boolean;
  className?: string;
}) {
  return (
    <div className={cn('min-w-0', className)}>
      <div className="flex items-center gap-1.5">
        {dot && <span className={cn('h-1.5 w-1.5 shrink-0 rounded-pill', dot)} />}
        <span className="truncate text-caption text-muted">{label}</span>
      </div>

      {loading ? (
        <Skeleton className="mt-2 h-6 w-2/3" />
      ) : (
        <div className="mt-1.5 min-w-0">
          {/* Full-width number with the unit as a caption beneath — keeps a 7-digit
              UZS sum readable (never clipped) in narrow side-by-side metric cells. */}
          <div className={cn('truncate text-section font-semibold tracking-tight leading-none', mono && 'font-mono tabular-nums')}>
            {value}
          </div>
          {unit && <div className="mt-0.5 text-micro uppercase tracking-wider text-muted">{unit}</div>}
        </div>
      )}

      {hint != null && !loading && <div className="mt-1 truncate text-micro text-muted">{hint}</div>}
    </div>
  );
}

/** Two-up (then four-up wrapping) responsive grid for {@link Metric}s. */
export function MetricGrid({ children, className }: { children: ReactNode; className?: string }) {
  return <div className={cn('grid grid-cols-2 gap-x-4 gap-y-5', className)}>{children}</div>;
}
