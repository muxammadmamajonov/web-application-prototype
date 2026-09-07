import { Suspense, lazy } from 'react';
import { cn } from '@/lib/cn';
import { chartColorAt } from '@/lib/chartColors';
import { money } from '@/lib/format';

export interface DonutSlice {
  key: string;
  label: string;
  value: number;
  /** Optional explicit colour (e.g. semantic status hues). When omitted, the
   *  slice falls back to the tokenized categorical palette by index. (#631) */
  color?: string;
}

// (F-18) Only the recharts ring is code-split; the legend + centre total below
// render immediately (plain DOM), so the panel paints without waiting on the
// ~413 KB charts chunk — that chunk loads via dynamic import when the ring mounts.
const DonutRing = lazy(() => import('./Donut.chart'));

/** Donut chart used for "Tenants by status" / "Installment status" panels.
 *  `stacked` forces the ring above a full-width legend (instead of side-by-side) \u2014
 *  use it inside narrow (\u22481/3-width) cards where a row layout would overflow. */
export function Donut({
  slices, total, totalLabel, className, stacked,
}: { slices: DonutSlice[]; total?: number; totalLabel?: string; className?: string; stacked?: boolean }) {
  const sum = total ?? slices.reduce((a, s) => a + s.value, 0);
  return (
    <div className={cn('flex flex-col items-center gap-4', !stacked && 'sm:flex-row sm:gap-6', className)}>
      <div className="relative h-36 w-36 shrink-0 sm:h-44 sm:w-44">
        <Suspense fallback={<div className="h-full w-full animate-pulse rounded-pill bg-black/5" />}>
          <DonutRing slices={slices} />
        </Suspense>
        <div className="absolute inset-0 grid place-items-center text-center">
          <div>
            <div className="text-title font-semibold tabular-nums leading-none">{money(sum)}</div>
            {totalLabel && <div className="mt-1 text-micro uppercase tracking-wider text-muted">{totalLabel}</div>}
          </div>
        </div>
      </div>

      <ul className={cn('flex flex-col gap-2 text-body', stacked && 'w-full')}>
        {slices.map((s, i) => (
          <li key={s.key} className="flex items-center gap-3">
            <span className="h-2 w-2 shrink-0 rounded-pill" style={{ background: s.color ?? chartColorAt(i) }} />
            {/* Stacked (full-width) legend flexes + truncates so it never overflows a
                narrow card; side-by-side keeps the fixed label column it always had. */}
            <span className={cn('truncate text-ink-soft', stacked ? 'min-w-0 flex-1' : 'min-w-[88px]')}>{s.label}</span>
            <span className="ml-auto font-medium tabular-nums">{s.value}</span>
          </li>
        ))}
      </ul>
    </div>
  );
}
