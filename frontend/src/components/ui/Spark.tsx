import { Suspense, lazy } from 'react';

export interface SparkPoint { x: string; y: number }

// (F-18) recharts lives in a separate ~413 KB vendor chunk. Loading it behind a
// dynamic import means the chunk is fetched only when a Spark actually mounts,
// not bundled into the dashboard shell — material on the flaky mobile / Telegram
// WebView audience.
const SparkChart = lazy(() => import('./Spark.chart'));

/** Recurring revenue line chart — soft orange fill, monthly x ticks. */
export function Spark({ data }: { data: SparkPoint[] }) {
  return (
    <Suspense fallback={<div className="h-[200px] w-full animate-pulse rounded-control bg-black/5" />}>
      <SparkChart data={data} />
    </Suspense>
  );
}
