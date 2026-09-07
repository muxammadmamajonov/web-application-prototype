import { describe, it, expect } from 'vitest';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { computeSchedule, type CalcFormula } from './calc';

/**
 * Golden-vector parity test. The schedule math is mirrored in two hand-synced
 * places — this `src/lib/calc.ts` and the backend `app/services/calc.py`. Both
 * this test and its backend twin (`backend/tests/test_calc_parity.py`) load the
 * SAME committed fixture, `calc-golden-vectors.json` at the repo root, and
 * assert their implementation reproduces it exactly. Drift on either side fails
 * CI on that side.
 *
 * Read with node `fs` (not `import`) so the file can live outside the Vite root
 * and be shared verbatim with pytest.
 */
interface CalcInput {
  goodsTotal: number;
  downPayment: number;
  months: number;
  formula: CalcFormula;
  markupPct?: number;
  interestRate?: number;
}
interface Expect {
  total: number;
  financed: number;
  principal: number;
  monthly: number;
}
interface GoldenCase {
  name: string;
  input: CalcInput;
  expect?: Expect;
  expectError?: string;
  frontendPreview?: Expect;
}
interface Golden {
  cases: GoldenCase[];
}

// src/lib/calc.parity.test.ts -> three levels up is the repo root.
const fixturePath = fileURLToPath(new URL('../../../calc-golden-vectors.json', import.meta.url));
const golden = JSON.parse(readFileSync(fixturePath, 'utf-8')) as Golden;

describe('computeSchedule golden-vector parity (locks calc.ts <-> calc.py)', () => {
  it('the fixture covers all four formulas plus an edge', () => {
    const formulas = new Set(golden.cases.map((c) => c.input.formula));
    for (const f of ['none', 'markup', 'annuity', 'differentiated'] as const) {
      expect(formulas.has(f)).toBe(true);
    }
    expect(golden.cases.some((c) => c.expectError)).toBe(true);
  });

  it.each(golden.cases.map((c) => [c.name, c] as const))('%s', (_name, c) => {
    const r = computeSchedule(c.input);

    // Backend raises on a non-positive financed amount; the frontend instead
    // returns a zeroed preview (so the live modal shows zeros, not a crash).
    const want = c.expectError ? c.frontendPreview : c.expect;
    if (!want) throw new Error(`fixture case ${c.name} has neither expect nor frontendPreview`);

    expect(r.total).toBe(want.total);
    expect(r.financed).toBe(want.financed);
    expect(r.principal).toBe(want.principal);
    expect(r.monthly).toBe(want.monthly);
    // markupAmount is derived; keep it consistent with the financing cost.
    expect(r.markupAmount).toBe(want.principal - want.financed);
  });
});
