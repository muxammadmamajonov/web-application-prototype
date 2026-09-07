import type { ReactNode } from 'react';
import { cn } from '@/lib/cn';
import { money } from '@/lib/format';
import { Skeleton } from '@/components/ui/Skeleton';

interface Props {
  label: string;
  value: number | string;
  unit?: string;
  delta?: { value: string; positive?: boolean };
  hint?: string;
  tone?: 'default' | 'accent' | 'success' | 'warn' | 'danger';
  /** Optional lucide icon — rendered as a tone-tinted chip in the top-right. */
  icon?: ReactNode;
  /** Keep the card chrome (dot + label + icon) and skeleton just the value. */
  loading?: boolean;
  formatNumber?: boolean;
  className?: string;
}

const DOT_TONE: Record<NonNullable<Props['tone']>, string> = {
  success: 'bg-success', warn: 'bg-warn', danger: 'bg-danger', accent: 'bg-accent', default: 'bg-success',
};

const ICON_TONE: Record<NonNullable<Props['tone']>, string> = {
  success: 'bg-success-soft text-[color:var(--c-success-strong)]',
  warn:    'bg-warn-soft text-[color:var(--c-warn-strong)]',
  danger:  'bg-danger-soft text-[color:var(--c-danger-strong)]',
  accent:  'bg-[color:var(--c-accent-soft)] text-accent',
  default: 'bg-cream text-ink-soft',
};

/**
 * The KPI block from the dashboards: a small status dot + label and an optional
 * tone-tinted icon chip on top, a giant tabular-figures value with an optional
 * unit, and an optional delta + hint footer. Fixed min-height keeps a row of
 * cards perfectly aligned; `loading` swaps only the value for a skeleton so the
 * card never collapses or looks like an empty box while data arrives.
 */
export function KpiCard({
  label, value, unit, delta, hint, tone = 'default', icon, loading, formatNumber = true, className,
}: Props) {
  const display = typeof value === 'number' && formatNumber ? money(value) : value;

  return (
    <div className={cn('card p-4 sm:p-5 flex flex-col gap-2 sm:gap-3 min-h-[124px] sm:min-h-[150px] overflow-hidden', className)}>
      <div className="flex items-start justify-between gap-2">
        {/* Reserve two label lines from sm up so a wrapping label (e.g. "Shu oyda
            tasdiqlangan") never pushes its value below the others — every KPI
            value lands on the same baseline across the row. */}
        <div className="flex min-w-0 items-start gap-2 pt-0.5 text-caption text-ink-soft sm:min-h-10 sm:text-small">
          <span className={cn('mt-1 h-1.5 w-1.5 rounded-pill shrink-0', DOT_TONE[tone])} />
          <span className="line-clamp-2 font-medium leading-snug">{label}</span>
        </div>
        {icon && (
          <span className={cn('grid h-8 w-8 shrink-0 place-items-center rounded-control', ICON_TONE[tone])}>
            {icon}
          </span>
        )}
      </div>

      {loading ? (
        <Skeleton className="h-8 w-3/5" />
      ) : (
        <div className="min-w-0">
          {/* The number gets the full card width (the unit sits beneath as a tight
              caption) so a big UZS sum is never clipped in a dense 4-up grid. */}
          <div className="text-title font-semibold tabular-nums leading-none truncate">{display}</div>
          {unit && <div className="mt-1 text-micro uppercase tracking-wider text-muted">{unit}</div>}
        </div>
      )}

      {(hint || delta) && !loading && (
        <div className="mt-auto flex min-w-0 items-center justify-between gap-2 text-micro sm:text-caption">
          {hint && <span className="text-muted truncate">{hint}</span>}
          {delta && (
            <span className={cn('font-medium shrink-0', delta.positive ? 'text-[color:var(--c-success-strong)]' : 'text-[color:var(--c-danger-strong)]')}>
              {delta.value}
            </span>
          )}
        </div>
      )}
    </div>
  );
}
