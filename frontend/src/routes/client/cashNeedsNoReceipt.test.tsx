// @vitest-environment jsdom
import { describe, expect, it } from 'vitest';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';

/**
 * Cash must not demand a receipt photo.
 *
 * REPORTED FROM USE: choosing "cash" sent the customer to the receipt-upload
 * page and refused to submit without an image. Cash is handed over at the
 * counter — there is often no chek to photograph, and the shop being asked to
 * approve the payment is the shop that took the money.
 *
 * These are source-level assertions rather than a rendered flow. The page needs
 * a router, a query client, an authenticated session, a live installment and a
 * file input to render at all, and a test that mounted all of that would be
 * asserting the harness. What actually has to hold is four small facts about the
 * code, and each of them is the thing that broke:
 *
 *   1. the chosen method reaches the receipt page,
 *   2. the page treats only cash as proof-optional,
 *   3. the submit gate accepts a missing file on that rail,
 *   4. the method is sent to the API.
 *
 * The behavioural half — that the API accepts cash with no file and still
 * refuses card without one — is covered against a real database in
 * `backend/tests/test_cash_payment_needs_no_receipt.py`.
 */
const read = (rel: string) =>
  readFileSync(fileURLToPath(new URL(rel, import.meta.url)), 'utf8');

const payPage = read('./PayPage.tsx');
const receiptPage = read('./ReceiptUploadPage.tsx');

describe('the cash rail does not require a receipt photo', () => {
  it('hands the chosen method to the receipt page', () => {
    // Without this the page cannot tell cash from card, which is why every
    // manual payment was treated — and recorded — as cash.
    expect(payPage).toMatch(/method=\$\{encodeURIComponent\(chosen\)\}/);
  });

  it('treats cash, and only cash, as proof-optional', () => {
    expect(receiptPage).toMatch(/const proofOptional = method === 'cash'/);
  });

  it('lets the submit button enable with no file on the cash rail', () => {
    // The exact gate that blocked the reported flow.
    expect(receiptPage).toMatch(/canSubmit = \(!!file \|\| proofOptional\)/);
  });

  it('still refuses to submit without a file on every other rail', () => {
    expect(receiptPage).toMatch(/if \(!file && !proofOptional\) return;/);
  });

  it('only attaches a file when there is one', () => {
    expect(receiptPage).toMatch(/if \(file\) fd\.append\('file', file\)/);
  });

  it('sends the method so the server can apply the same rule', () => {
    expect(receiptPage).toMatch(/fd\.append\('method', method\)/);
  });

  it('stops telling a cash payer to send a photo', () => {
    // The old copy said "Chek foto yuborish kerak" for every offline method.
    expect(payPage).toMatch(/m\.method === 'cash'/);
    expect(payPage).toMatch(/client\.methodProofHint/);
  });
});

describe('the copy exists in both locales', () => {
  // The project requires every string in uz AND ru; a missing key renders the
  // English-ish default to a user who reads neither.
  const uz = JSON.parse(read('../../i18n/locales/uz.json'));
  const ru = JSON.parse(read('../../i18n/locales/ru.json'));

  for (const key of [
    'methodCashHint',
    'methodProofHint',
    'uploadReceiptCashTitle',
    'uploadReceiptCashHint',
    'receiptOptional',
  ]) {
    it(`${key} is translated in uz and ru`, () => {
      expect(uz.client[key]).toBeTruthy();
      expect(ru.client[key]).toBeTruthy();
      expect(uz.client[key]).not.toBe(ru.client[key]);
    });
  }
});
