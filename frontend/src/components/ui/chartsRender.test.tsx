// @vitest-environment jsdom
/**
 * Guard for the chart vendor bump (recharts 2.x -> 3.x, PR #984).
 *
 * `Donut.chart.tsx` and `Spark.chart.tsx` are the ONLY recharts call sites, and
 * both are `React.lazy(() => import(...))` behind a `<Suspense>` in their eager
 * wrappers. The pre-existing tests render the wrappers (`Donut`, `Spark`), so
 * they assert on the legend / centre total / pulse fallback while the lazy
 * chunk never resolves — recharts itself was never executed by the suite. A
 * green `pnpm test` therefore said nothing at all about a recharts MAJOR bump.
 *
 * These tests import the chart modules DIRECTLY (bypassing lazy + Suspense) so
 * recharts actually runs and builds SVG geometry.
 *
 * `ResponsiveContainer` is the one part that cannot work here: it sizes itself
 * from a `ResizeObserver`, which jsdom does not implement, so it renders an
 * empty box and skips every child. It is stubbed to a fixed-size div — that is
 * the measurement shell only. `PieChart`/`Pie`/`Cell` and
 * `AreaChart`/`Area`/`XAxis`/`YAxis`/`Tooltip` are the real recharts
 * components, and they are what a major bump breaks.
 */
import { render } from '@testing-library/react';
import { beforeAll, describe, expect, it, vi } from 'vitest';

import DonutRing from '@/components/ui/Donut.chart';
import SparkChart from '@/components/ui/Spark.chart';

// recharts sizes itself from a ResizeObserver, which jsdom does not implement,
// so ResponsiveContainer reports 0x0 and every child is skipped. Report a fixed
// box instead. Nothing else is stubbed — Pie/Cell/Area/XAxis/Tooltip are the
// real components, and they are what a major bump breaks.
const BOX = { width: 600, height: 300 };

class FixedSizeResizeObserver {
  private cb: ResizeObserverCallback;
  constructor(cb: ResizeObserverCallback) { this.cb = cb; }
  observe(target: Element) {
    this.cb(
      [{ target, contentRect: { ...BOX, top: 0, left: 0, right: 600, bottom: 300, x: 0, y: 0 } } as unknown as ResizeObserverEntry],
      this as unknown as ResizeObserver,
    );
  }
  unobserve() {}
  disconnect() {}
}

beforeAll(() => {
  vi.stubGlobal('ResizeObserver', FixedSizeResizeObserver);
  vi.spyOn(HTMLElement.prototype, 'getBoundingClientRect').mockReturnValue({
    ...BOX, top: 0, left: 0, right: 600, bottom: 300, x: 0, y: 0, toJSON: () => ({}),
  } as DOMRect);
  Object.defineProperty(HTMLElement.prototype, 'offsetWidth', { configurable: true, value: BOX.width });
  Object.defineProperty(HTMLElement.prototype, 'offsetHeight', { configurable: true, value: BOX.height });
});

describe('recharts renders real geometry (vendor major-bump guard)', () => {
  it('DonutRing draws one filled sector path per slice', () => {
    const { container } = render(
      <DonutRing
        slices={[
          { key: 'paid', label: 'Paid', value: 60, color: '#111111' },
          { key: 'due', label: 'Due', value: 30, color: '#222222' },
          { key: 'late', label: 'Late', value: 10, color: '#333333' },
        ]}
      />,
    );

    expect(container.querySelector('svg'), 'recharts produced no <svg>').not.toBeNull();

    // One <path> per slice, each carrying the fill passed through <Cell>.
    const sectors = container.querySelectorAll('path.recharts-sector');
    expect(sectors.length).toBe(3);
    expect([...sectors].map((p) => p.getAttribute('fill'))).toEqual([
      '#111111',
      '#222222',
      '#333333',
    ]);

    // Real geometry, not a zero-size placeholder.
    expect(sectors[0].getAttribute('d')).toMatch(/^M/);
  });

  it('SparkChart draws the area path and its gradient fill', () => {
    const { container } = render(
      <SparkChart
        data={[
          { x: 'Jan', y: 10 },
          { x: 'Feb', y: 40 },
          { x: 'Mar', y: 25 },
        ]}
      />,
    );

    expect(container.querySelector('svg')).not.toBeNull();

    const area = container.querySelector('path.recharts-area-area');
    expect(area, 'recharts produced no area path').not.toBeNull();
    expect(area!.getAttribute('d')).toMatch(/^M/);
    expect(area!.getAttribute('fill')).toBe('url(#muddato-spark)');

    // The gradient the fill points at must exist, or the area renders invisible.
    expect(container.querySelector('linearGradient#muddato-spark')).not.toBeNull();

    // The X axis renders. Deliberately NOT asserting which tick labels are
    // shown: recharts drops ticks that do not fit, and it decides "fit" from
    // measured text width — which jsdom cannot provide and the stub above
    // fakes. Tick *selection* here would test the stub, not recharts.
    expect(container.querySelector('.recharts-xAxis')).not.toBeNull();
  });
});
