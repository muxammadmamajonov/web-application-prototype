/** Client-side mirror of the backend `app/services/calc.py:compute_schedule`.
 *
 *  Keeps the installment modal's live preview in lock-step with what the server
 *  will actually charge. `principal` is the total repayable (== monthly*months),
 *  `total` is the goods cash price. interestRate is a fraction (0.025 = 2.5%/mo),
 *  markupPct is a percent (20 = 20%).
 */
export type CalcFormula = 'none' | 'markup' | 'annuity' | 'differentiated';

interface CalcResult {
  total: number;        // goods cash price (sum of line totals)
  financed: number;     // goods price − down payment
  principal: number;    // total repayable (monthly * months)
  monthly: number;      // flat per-month payment (whole so'm)
  markupAmount: number; // principal − financed (financing cost)
}

export function computeSchedule({
  goodsTotal, downPayment, months, formula, markupPct, interestRate,
}: {
  goodsTotal: number;
  downPayment: number;
  months: number;
  formula: CalcFormula;
  markupPct?: number | null;
  interestRate?: number | null;
}): CalcResult {
  const total = Math.max(0, Math.round(goodsTotal));
  const down = Math.max(0, Math.round(downPayment || 0));
  const financed = total - down;
  const n = Math.max(1, Math.trunc(months || 1));
  const markup = Math.max(0, markupPct || 0);
  const rate = Math.max(0, interestRate || 0);

  let repayable: number;
  if (financed <= 0) {
    return { total, financed: Math.max(0, financed), principal: 0, monthly: 0, markupAmount: 0 };
  }
  if (formula === 'markup') {
    repayable = financed * (1 + markup / 100);
  } else if (formula === 'annuity' && rate > 0) {
    const factor = rate / (1 - Math.pow(1 + rate, -n));
    repayable = financed * factor * n;
  } else if (formula === 'differentiated' && rate > 0) {
    const totalInterest = (financed * rate * (n + 1)) / 2;
    repayable = financed + totalInterest;
  } else {
    repayable = financed; // "none", or an interest formula with a zero rate
  }

  const monthly = Math.round(repayable / n);
  const principal = monthly * n; // keep monthly*months == principal exactly
  return { total, financed, principal, monthly, markupAmount: principal - financed };
}

/**
 * Money still owed on a contract: total repayable minus what has been paid,
 * clamped at zero so an overpayment never renders as a negative balance.
 *
 * Two personas look at the same entity — the shop owner's Installments list and
 * a salesperson's Sales list — and both filter, sort and display by this figure.
 * It was computed once per page, which is a money number duplicated across
 * views that must agree. Structurally typed on the two fields it reads, so both
 * row shapes satisfy it without either importing the other's DTO.
 */
export function remainingOf(row: { principal: number; paid_amount: number }): number {
  return Math.max(0, row.principal - row.paid_amount);
}
