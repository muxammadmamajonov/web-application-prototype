import { Cell, Pie, PieChart, ResponsiveContainer } from 'recharts';
import { chartColorAt } from '@/lib/chartColors';
import type { DonutSlice } from './Donut';

/**
 * Recharts ring for {@link Donut}, code-split so the `charts` vendor chunk
 * (recharts) loads via dynamic import only when the ring renders (F-18). The
 * legend + centre total in Donut.tsx stay eager (plain DOM), so only the ring
 * waits on the chunk.
 */
export default function DonutRing({ slices }: { slices: DonutSlice[] }) {
  return (
    <ResponsiveContainer>
      <PieChart>
        <Pie
          data={slices}
          dataKey="value"
          nameKey="label"
          innerRadius="68%"
          outerRadius="100%"
          startAngle={90}
          endAngle={-270}
          stroke="none"
          isAnimationActive={false}
        >
          {slices.map((s, i) => <Cell key={s.key} fill={s.color ?? chartColorAt(i)} />)}
        </Pie>
      </PieChart>
    </ResponsiveContainer>
  );
}
