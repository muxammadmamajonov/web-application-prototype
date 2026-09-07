// @vitest-environment jsdom
/**
 * FB-017 — the money field a customer types a payment into.
 *
 * REPRODUCED ON A REAL DEVICE (founder beta, 2026-08-09). Three separate
 * problems in the same field, all of them about *editing* rather than about the
 * number itself:
 *
 *   1. the suggested instalment could not be cleared — see
 *      `payAmountSuggestion.test.tsx`, which owns that half;
 *   2. deleting to empty left a stubborn "0" the next keystroke appended to;
 *   3. the caret jumped to the end after every keystroke, because the display
 *      is regrouped on each change and a controlled input loses the selection.
 *
 * (3) is the one that quietly corrupts amounts: editing the middle of
 * "1 000 000" put every subsequent digit at the end, so the person watched a
 * number they did not type appear and had to start over.
 */
import { describe, expect, it, afterEach, vi } from 'vitest';
import { render, screen, cleanup, fireEvent } from '@testing-library/react';
import { useState } from 'react';
import { MoneyInput, formatMoney, MONEY_MAX } from '@/components/ui/FormFields';

afterEach(cleanup);

/** A controlled host, exactly like every real caller. */
function Host({ initial = 0, max, onValue }: { initial?: number; max?: number; onValue?: (n: number) => void }) {
  const [v, setV] = useState(initial);
  return (
    <MoneyInput
      aria-label="amount"
      value={v}
      max={max}
      onChange={(n) => { setV(n); onValue?.(n); }}
    />
  );
}

const field = () => screen.getByLabelText('amount') as HTMLInputElement;

/** Type as a browser would: the whole new value, with the caret where it lands.
 *  Assigning `el.value` first would defeat React's value tracker and the change
 *  handler would never run — the value has to arrive with the event. */
function typeInto(el: HTMLInputElement, value: string, caret = value.length) {
  fireEvent.change(el, { target: { value, selectionStart: caret, selectionEnd: caret } });
}

describe('what the field shows', () => {
  it('groups thousands as you type', () => {
    render(<Host />);
    typeInto(field(), '1000000');
    expect(field().value).toBe('1 000 000');
  });

  it('accepts a formatted paste', () => {
    render(<Host />);
    for (const pasted of ['1 000 000', '1,000,000', '1000000 UZS']) {
      typeInto(field(), pasted);
      expect(field().value).toBe('1 000 000');
    }
  });

  it('renders zero as empty, so the field can actually be cleared', () => {
    render(<Host initial={5} />);
    expect(field().value).toBe('5');
    typeInto(field(), '');
    expect(field().value).toBe('');
  });

  it('the next digit after clearing replaces the zero rather than joining it', () => {
    const onValue = vi.fn();
    render(<Host initial={9} onValue={onValue} />);
    typeInto(field(), '');            // → 0, shown as ''
    typeInto(field(), '7');
    expect(field().value).toBe('7');
    expect(onValue).toHaveBeenLastCalledWith(7);
  });
});

describe('the caret survives regrouping', () => {
  it('stays after the digit just typed in the middle of a grouped number', () => {
    render(<Host initial={1000000} />);
    const el = field();
    expect(el.value).toBe('1 000 000');
    // Insert "5" after the leading "1": "15 000 000", caret must sit after the 5.
    typeInto(el, '15 000 000', 2);
    expect(el.value).toBe('15 000 000');
    expect(el.selectionStart).toBe(2);   // two digits consumed, no separator yet
  });

  it('stays put when a separator appears to the left of the caret', () => {
    render(<Host initial={100} />);
    const el = field();
    typeInto(el, '1000', 4);             // becomes "1 000"
    expect(el.value).toBe('1 000');
    // Four digits precede the caret; in "1 000" that is index 5.
    expect(el.selectionStart).toBe(5);
  });

  it('goes to the start when everything before the caret was deleted', () => {
    render(<Host initial={1000} />);
    const el = field();
    typeInto(el, '000', 0);
    expect(el.selectionStart).toBe(0);
  });

  it('is left alone when the value changes from OUTSIDE the field', () => {
    // A quick-pick chip setting the amount must not yank the caret about — the
    // restore only runs for a change the field itself produced.
    function Chips() {
      const [v, setV] = useState(0);
      return (
        <>
          <MoneyInput aria-label="amount" value={v} onChange={setV} />
          <button type="button" onClick={() => setV(250000)}>monthly</button>
        </>
      );
    }
    render(<Chips />);
    const el = field();
    const spy = vi.spyOn(el, 'setSelectionRange');
    fireEvent.click(screen.getByText('monthly'));
    expect(el.value).toBe('250 000');
    expect(spy).not.toHaveBeenCalled();
    spy.mockRestore();
  });
});

describe('what the field refuses', () => {
  it('drops letters and punctuation instead of showing NaN', () => {
    const onValue = vi.fn();
    render(<Host onValue={onValue} />);
    typeInto(field(), 'abc-12x3');
    expect(field().value).toBe('123');
    expect(onValue).toHaveBeenLastCalledWith(123);
  });

  it('never emits a fraction — money here is whole units', () => {
    const onValue = vi.fn();
    render(<Host onValue={onValue} />);
    typeInto(field(), '1234.56');
    expect(onValue).toHaveBeenLastCalledWith(123456);
    expect(Number.isInteger(onValue.mock.lastCall![0])).toBe(true);
  });

  it('cannot be pushed past the caller\'s ceiling', () => {
    const onValue = vi.fn();
    render(<Host max={500000} onValue={onValue} />);
    typeInto(field(), '99999999');
    expect(onValue).toHaveBeenLastCalledWith(500000);
    expect(field().value).toBe('500 000');
  });

  it('cannot be pushed past the global cap either', () => {
    const onValue = vi.fn();
    render(<Host onValue={onValue} />);
    typeInto(field(), '9999999999999');
    expect(onValue).toHaveBeenLastCalledWith(MONEY_MAX);
  });

  it('a negative sign is simply not a digit', () => {
    const onValue = vi.fn();
    render(<Host onValue={onValue} />);
    typeInto(field(), '-500');
    expect(onValue).toHaveBeenLastCalledWith(500);
  });
});

describe('formatMoney', () => {
  it('renders zero as empty and groups with a space, not a comma', () => {
    expect(formatMoney(0)).toBe('');
    expect(formatMoney(1000000)).toBe('1 000 000');
  });

  // M-18: formatMoney used to group via its own `n.toLocaleString('en-US')`,
  // independent of the canonical `lib/format#money` — no `Math.trunc`, so a
  // fractional amount rendered its decimals instead of the whole-number
  // grouping every other money surface uses. Delegating to `money()` closes
  // that gap; this pins the truncation so it can't silently regress back.
  it('truncates fractional input the same way the canonical money() formatter does', () => {
    expect(formatMoney(1000.5)).toBe('1 000');
  });
});
