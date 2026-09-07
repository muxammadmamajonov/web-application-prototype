/**
 * (MUDD-114) The hint that makes a second currency visible instead of summed.
 *
 * The behaviour that matters most is the NEGATIVE one: a single-currency shop —
 * which is almost every shop — must get `undefined`, so its dashboard is
 * byte-identical to before this shipped. A hint that rendered "+ 0 USD" would
 * be a regression dressed as a fix.
 */
import { describe, expect, it } from 'vitest';
import { secondaryCurrencyHint } from './secondaryCurrency';

describe('secondaryCurrencyHint', () => {
  it('is silent for a single-currency shop', () => {
    expect(secondaryCurrencyHint({ UZS: 84_000_000 }, 'UZS')).toBeUndefined();
  });

  it('is silent when the map is missing (a pre-MUDD-114 cached response)', () => {
    expect(secondaryCurrencyHint(undefined, 'UZS')).toBeUndefined();
  });

  it('is silent when the primary currency is unknown', () => {
    expect(secondaryCurrencyHint({ UZS: 1, USD: 2 }, undefined)).toBeUndefined();
  });

  it('names what the headline figure leaves out', () => {
    expect(secondaryCurrencyHint({ UZS: 84_000_000, USD: 4_500 }, 'UZS'))
      .toBe('+ 4 500 USD');
  });

  it('drops a zero bucket rather than printing "+ 0"', () => {
    expect(secondaryCurrencyHint({ UZS: 10, USD: 0 }, 'UZS')).toBeUndefined();
  });

  it('reports the UZS remainder for a shop whose primary is USD', () => {
    expect(secondaryCurrencyHint({ UZS: 84_000_000, USD: 4_500 }, 'USD'))
      .toBe('+ 84 000 000 UZS');
  });

  /**
   * (MUDD-121) The first SIGNED field to reach this helper is
   * `gross_profit_by_currency` — revenue minus cost, with no floor. A USD line
   * sold below cost (or carrying a mistyped `cost_price`) makes it negative,
   * and a `> 0` filter would drop that currency off the card entirely while
   * the CSV export — which explodes the same map by KEY — still prints it.
   * One report, two answers, is worse than the wrong number this lane exists
   * to fix. A loss is stated with its own sign.
   */
  it('states a negative secondary currency instead of dropping it', () => {
    expect(secondaryCurrencyHint({ UZS: 100, USD: -50 }, 'UZS'))
      .toBe('- 50 USD');
  });

  it('leaves a negative PRIMARY to the headline figure it belongs to', () => {
    expect(secondaryCurrencyHint({ UZS: -100, USD: 50 }, 'UZS'))
      .toBe('+ 50 USD');
  });

  it('keeps each currency on its own sign when they disagree', () => {
    expect(secondaryCurrencyHint({ UZS: 1, EUR: -3, USD: 2 }, 'UZS'))
      .toBe('- 3 EUR · + 2 USD');
  });

  it('is stable regardless of key order', () => {
    const a = secondaryCurrencyHint({ USD: 1, UZS: 2, EUR: 3 }, 'UZS');
    const b = secondaryCurrencyHint({ EUR: 3, UZS: 2, USD: 1 }, 'UZS');
    expect(a).toBe(b);
    expect(a).toBe('+ 3 EUR · + 1 USD');
  });
});
