import { cn } from '@/lib/cn';

/** Content-shaped loading placeholder. Use instead of spinners/zero-cards so
 *  the layout doesn't shift and a loading screen never looks like real data. */
export function Skeleton({ className }: { className?: string }) {
  return <div className={cn('animate-pulse rounded-icon bg-ink/[0.07]', className)} aria-hidden />;
}

/** A few stacked skeleton rows inside a card — the default list/loading shape. */
export function SkeletonList({ rows = 6, className }: { rows?: number; className?: string }) {
  return (
    <div className={cn('card divide-y divide-line', className)} role="status" aria-busy="true">
      {Array.from({ length: rows }).map((_, i) => (
        <div key={i} className="flex items-center gap-4 px-5 py-4">
          <Skeleton className="h-9 w-9 rounded-control" />
          <div className="flex-1 space-y-2">
            <Skeleton className="h-3.5 w-1/3" />
            <Skeleton className="h-3 w-1/2" />
          </div>
          <Skeleton className="h-3.5 w-16" />
        </div>
      ))}
    </div>
  );
}

/** Grid of KPI-card skeletons for dashboard headers. */
export function SkeletonKpis({ count = 4 }: { count?: number }) {
  return (
    <div className="grid grid-cols-2 gap-3 lg:grid-cols-4" role="status" aria-busy="true">
      {Array.from({ length: count }).map((_, i) => (
        <div key={i} className="card min-h-[120px] space-y-3 p-4 sm:min-h-[150px] sm:p-5">
          <Skeleton className="h-3 w-24" />
          <Skeleton className="h-9 w-2/3" />
          <Skeleton className="h-3 w-1/2" />
        </div>
      ))}
    </div>
  );
}
