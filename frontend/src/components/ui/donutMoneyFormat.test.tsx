// @vitest-environment jsdom
/**
 * M-18 — `Donut`'s centre total used its own inline
 * `sum.toLocaleString('ru-RU').replace(...)`, a third independent grouping
 * implementation beside `lib/format#money` and `FormFields#formatMoney`, and
 * (unlike `money()`) it never `Math.trunc`ed. Delegating to `money()` closes
 * that gap; this pins the rendered total against the canonical formatter so a
 * future edit can't reintroduce a local `toLocaleString` call.
 */
import '@testing-library/jest-dom/vitest';
import { describe, expect, it } from 'vitest';
import { render, screen } from '@testing-library/react';
import { Donut } from '@/components/ui/Donut';
import { money } from '@/lib/format';

describe('Donut centre total', () => {
  it('renders the sum of slice values via the canonical money() formatter', () => {
    render(
      <Donut
        slices={[
          { key: 'a', label: 'A', value: 1_240_000 },
          { key: 'b', label: 'B', value: 760_000 },
        ]}
      />,
    );
    expect(screen.getByText(money(2_000_000))).toBeInTheDocument();
  });

  it('truncates a fractional explicit `total` the same way money() does', () => {
    render(<Donut slices={[{ key: 'a', label: 'A', value: 1 }]} total={1000.9} />);
    expect(screen.getByText(money(1000.9))).toBeInTheDocument();
    expect(screen.queryByText('1 000.9')).not.toBeInTheDocument();
    expect(screen.queryByText('1 000,9')).not.toBeInTheDocument();
  });
});
