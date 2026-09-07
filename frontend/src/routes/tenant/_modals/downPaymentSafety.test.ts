/**
 * Phase 3 remediation R-01 — the post-creation hand-off must never let an
 * ordinary instalment payment masquerade as a down payment.
 *
 * ## Why this is a money-correctness test, not a copy test
 *
 * The backend's money convention (`backend/app/services/calc.py`) is:
 *
 *     financed  = goods_total − down_payment
 *     principal = <schedule derived from financed>          // total repayable
 *     settled   ⟺ paid_amount >= principal                  // installments.py
 *
 * The down payment is therefore consumed *before* the schedule exists. It never
 * appears in `paid_amount`, and it must not: money collected at signing is
 * already reflected in a smaller `principal`.
 *
 * The wizard's success hand-off used to offer "Record down payment", wired to
 * the ordinary payment path. Taking that action would add the down payment to
 * `paid_amount` — a second deduction against an already-reduced principal —
 * retiring the debt early by exactly the down payment, silently, on every
 * contract created with one.
 *
 * These tests pin both halves of the fix: the label no longer claims to record
 * a down payment, and the control the label belongs to opens the real payment
 * form rather than a screen where the form is one click away.
 */
import { describe, expect, it } from 'vitest';
import { readFileSync } from 'node:fs';

const WIZARD = 'src/routes/tenant/_modals/InstallmentModal.tsx';
const LIST = 'src/routes/tenant/InstallmentsPage.tsx';
const CALL_SITES = [
  'src/routes/tenant/InstallmentsPage.tsx',
  'src/routes/tenant/OverviewPage.tsx',
  'src/routes/staff/SalesPage.tsx',
  'src/routes/staff/TodayPage.tsx',
];

const read = (p: string) => readFileSync(p, 'utf-8');

/** Source with comments stripped — assertions are about shipped behaviour. */
const code = (p: string) =>
  read(p).replace(/\/\*[\s\S]*?\*\//g, '').replace(/\/\/.*$/gm, '');


/** The brace-balanced body of the statement beginning at `marker`. */
function blockAfter(src: string, marker: string): string {
  const start = src.indexOf(marker);
  if (start < 0) return '';
  let depth = 0;
  for (let i = src.indexOf('{', start); i < src.length; i++) {
    if (src[i] === '{') depth++;
    else if (src[i] === '}' && --depth === 0) return src.slice(start, i + 1);
  }
  return src.slice(start);
}

describe('the success hand-off never claims to record a down payment', () => {
  it('offers no control whose key or label says "down payment"', () => {
    const src = code(WIZARD);
    expect(src).not.toContain('recordDownPayment');

    // Scoped to the post-creation hand-off on purpose. The wizard legitimately
    // COLLECTS a down payment earlier — it is a contract term that shapes the
    // schedule — so a file-wide ban would forbid correct code. What must never
    // appear is a down-payment claim in the block that runs AFTER the contract
    // exists, because by then the amount is already inside `principal`.
    const handoffBlock = blockAfter(src, 'if (created)');
    expect(handoffBlock, 'the hand-off block exists').toBeTruthy();
    expect(handoffBlock).not.toMatch(/down\s*_?payment/i);

    // The retained action is an ordinary payment.
    expect(handoffBlock).toContain('installments.recordFirstPayment');
  });

  it('keeps no down-payment translation key that a control could resurrect', () => {
    for (const locale of ['uz', 'ru']) {
      const bundle = JSON.parse(read(`src/i18n/locales/${locale}.json`));
      expect(bundle.installments?.recordDownPayment).toBeUndefined();
      expect(bundle.installments?.recordFirstPayment).toBeTruthy();
    }
  });

  it('does not send a down_payment field on the payment path', () => {
    // The contract create payload carries `down_payment` (correct — it shapes
    // the schedule). No payment mutation may.
    const src = code(LIST);
    const recordSection = src.slice(src.indexOf('function RecordPaymentSection'));
    expect(recordSection).not.toContain('down_payment');
  });
});

describe('a control labelled "record a payment" opens the payment form', () => {
  it('the detail modal accepts and honours an openPayment flag', () => {
    const src = code(LIST);
    expect(src).toContain('openPayment?: boolean');
    // …and it actually reaches the form's initial open state.
    expect(src).toContain('initiallyOpen={openPayment}');
    expect(src).toContain('useState(!!initiallyOpen)');
  });

  it('every hand-off call site requests the payment form, not just the screen', () => {
    for (const path of CALL_SITES) {
      const src = code(path);
      const handoff = src.match(/onRecordPayment=\{[^}]*\}/)?.[0];
      expect(handoff, `${path} wires the hand-off`).toBeTruthy();
      // Opening the detail screen alone is exactly the defect this corrects.
      expect(handoff, `${path} must request the payment form`).toContain('setPayForId');
      expect(src, `${path} passes the flag through`).toContain('openPayment=');
    }
  });

  it('the flag is scoped to the contract it was raised for', () => {
    // A stale flag must not force the payment form open on an unrelated
    // contract the user opens later from the list.
    for (const path of CALL_SITES) {
      expect(code(path)).toContain('payForId === detailId');
    }
  });
});
