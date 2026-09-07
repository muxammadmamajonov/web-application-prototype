import {
  Area, AreaChart, ResponsiveContainer, Tooltip, XAxis, YAxis,
} from 'recharts';
import type { SparkPoint } from './Spark';

/**
 * Recharts implementation of {@link Spark}, in its own module so the ~413 KB
 * `charts` vendor chunk (recharts) is fetched via dynamic import only when a
 * chart actually renders — not eagerly with the first chart-bearing dashboard
 * shell (F-18). Default export so `React.lazy(() => import('./Spark.chart'))`
 * resolves it directly.
 */
export default function SparkChart({ data }: { data: SparkPoint[] }) {
  return (
    <ResponsiveContainer width="100%" height={200}>
      <AreaChart data={data} margin={{ top: 12, right: 12, left: 0, bottom: 0 }}>
        <defs>
          <linearGradient id="muddato-spark" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%"   stopColor="var(--c-chart-1)" stopOpacity={0.28} />
            <stop offset="100%" stopColor="var(--c-chart-1)" stopOpacity={0} />
          </linearGradient>
        </defs>
        <XAxis dataKey="x" axisLine={false} tickLine={false} stroke="var(--c-chart-axis)" fontSize={11} />
        <YAxis hide />
        <Tooltip
          cursor={{ stroke: 'rgba(20,22,28,0.1)' }}
          contentStyle={{
            borderRadius: 12, borderColor: 'rgba(20,22,28,0.08)', background: 'var(--c-surface)', fontSize: 12,
          }}
        />
        <Area
          type="monotone"
          dataKey="y"
          stroke="var(--c-chart-1)"
          strokeWidth={2}
          fill="url(#muddato-spark)"
          dot={{ r: 3, stroke: 'var(--c-chart-1)', fill: 'var(--c-surface)', strokeWidth: 2 }}
          activeDot={{ r: 5 }}
        />
      </AreaChart>
    </ResponsiveContainer>
  );
}
