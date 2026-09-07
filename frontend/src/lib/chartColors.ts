/**
 * (#631) Categorical data-visualization palette.
 *
 * Backed by the `--c-chart-*` tokens in globals.css. `chart-1` is the brand
 * accent so single-series charts (Spark line, ProgressRing) stay on-brand;
 * `chart-2..8` are derived from the Okabe-Ito colour-blind-safe set, which is
 * distinguishable under protan/deutan/tritan simulation. Use this array for
 * multi-series donuts/lines so callers stop improvising per-slice colours.
 *
 * Returned as CSS-var strings (not hex) so a theme change in globals.css
 * propagates; CSS custom properties resolve in SVG paint attributes, which
 * ProgressRing already relies on.
 */
const chartColors = [
  'var(--c-chart-1)',
  'var(--c-chart-2)',
  'var(--c-chart-3)',
  'var(--c-chart-4)',
  'var(--c-chart-5)',
  'var(--c-chart-6)',
  'var(--c-chart-7)',
  'var(--c-chart-8)',
] as const;

/** Pick a series colour by index, cycling if there are more series than tokens. */
export const chartColorAt = (i: number): string => chartColors[((i % chartColors.length) + chartColors.length) % chartColors.length];
