/**
 * C3-09 — overdue day counts must be calendar arithmetic, not clock arithmetic.
 *
 * The count was `floor((Date.parse(ref + 'T00:00:00') - Date.parse(due +
 * 'T00:00:00')) / 86_400_000)`. Without a `Z` those parse as LOCAL midnight, so
 * a span crossing a daylight-saving transition is 23 or 25 hours per day
 * boundary rather than 24 — and the floor then reports one day too few (spring
 * forward) or, for a longer span, drifts the other way.
 *
 * Uzbekistan does not observe DST, but the *browser* does the parsing and a
 * viewer can be anywhere. Building both ends with `Date.UTC` removes the
 * question entirely: UTC has no transitions, so a difference of ordinals is
 * always exact whole days.
 */
import { describe, expect, it, afterEach, vi } from 'vitest';
import { isRowOverdue, overdueDays } from './overdue';

const overdue = (next_due_date: string | null) => ({ status: 'active', is_overdue: true, next_due_date });

afterEach(() => vi.useRealTimers());

describe('day counts survive a daylight-saving transition', () => {
  // Europe/London springs forward on 2026-03-29 and back on 2026-10-25. Under
  // the old local-midnight subtraction these spans were 23h and 25h short/long.
  const cases: Array<[string, string, string, number]> = [
    ['spring forward, one day across the change', 'Europe/London', '2026-03-28', 1],
    ['spring forward, a week across the change',  'Europe/London', '2026-03-25', 4],
    ['autumn back, one day across the change',    'Europe/London', '2026-10-24', 1],
    ['US spring forward across the change',       'America/New_York', '2026-03-07', 1],
  ];

  it.each(cases)('%s', (_name, tz, due, expected) => {
    // The reference date sits on the far side of the transition.
    const ref = { 'Europe/London': { '2026-03-28': '2026-03-29', '2026-03-25': '2026-03-29', '2026-10-24': '2026-10-25' },
                  'America/New_York': { '2026-03-07': '2026-03-08' } }[tz]![due]!;
    expect(overdueDays(overdue(due), ref), `${tz} ${due} → ${ref}`).toBe(expected);
  });

  it('counts a full DST-crossing month as calendar days', () => {
    // 2026-03-01 → 2026-04-01 is 31 calendar days regardless of any transition.
    expect(overdueDays(overdue('2026-03-01'), '2026-04-01')).toBe(31);
  });

  it('is exact across an autumn transition too', () => {
    expect(overdueDays(overdue('2026-10-01'), '2026-11-01')).toBe(31);
  });
});

describe('ordinary arithmetic still holds', () => {
  it('counts plain whole days', () => {
    expect(overdueDays(overdue('2026-03-01'), '2026-03-05')).toBe(4);
    expect(overdueDays(overdue('2026-03-01'), '2026-03-02')).toBe(1);
  });

  it('is 0 on the due date itself', () => {
    expect(overdueDays(overdue('2026-03-05'), '2026-03-05')).toBe(0);
  });

  it('never goes negative for a future due date', () => {
    expect(overdueDays(overdue('2026-04-01'), '2026-03-05')).toBe(0);
  });

  it('spans a leap day correctly', () => {
    expect(overdueDays(overdue('2028-02-28'), '2028-03-01')).toBe(2);   // 2028 is a leap year
    expect(overdueDays(overdue('2026-02-28'), '2026-03-01')).toBe(1);   // 2026 is not
  });

  it('spans a year boundary', () => {
    expect(overdueDays(overdue('2025-12-30'), '2026-01-02')).toBe(3);
  });
});

describe('invalid and missing dates yield 0, never a wrong number', () => {
  it.each([null, undefined])('a missing due date (%s)', (v) => {
    expect(overdueDays({ status: 'active', is_overdue: true, next_due_date: v as null })).toBe(0);
  });

  it.each(['', '   ', 'not-a-date', '2026-3-1', '01/03/2026', '2026-03', '20260301'])(
    'a malformed due date (%s)', (bad) => {
      expect(overdueDays(overdue(bad), '2026-03-05')).toBe(0);
    });

  it.each(['2026-02-31', '2026-13-01', '2026-00-10', '2026-04-31'])(
    'a well-formed but impossible date (%s) is not rolled forward', (bad) => {
      expect(overdueDays(overdue(bad), '2026-05-05')).toBe(0);
    });

  it('a malformed reference date yields 0', () => {
    expect(overdueDays(overdue('2026-03-01'), 'nonsense')).toBe(0);
    expect(overdueDays(overdue('2026-03-01'), '2026-02-30')).toBe(0);
  });

  it('tolerates a datetime-shaped due value only as an exact date', () => {
    // The worklist sends plain dates; anything else is not silently reinterpreted.
    expect(overdueDays(overdue('2026-03-01T00:00:00Z'), '2026-03-05')).toBe(0);
  });
});

describe('the server verdict still governs whether we count at all', () => {
  it('returns 0 for a row the server did not mark overdue', () => {
    expect(overdueDays({ status: 'active', is_overdue: false, next_due_date: '2026-03-01' }, '2026-03-05')).toBe(0);
  });

  it('counts for a cron-lag row the server DID mark overdue', () => {
    expect(overdueDays({ status: 'active', is_overdue: true, next_due_date: '2026-03-01' }, '2026-03-05')).toBe(4);
  });

  it('falls back to status only when the server did not classify', () => {
    expect(isRowOverdue({ status: 'overdue' })).toBe(true);
    expect(isRowOverdue({ status: 'active' })).toBe(false);
    expect(overdueDays({ status: 'overdue', next_due_date: '2026-03-01' }, '2026-03-05')).toBe(4);
  });
});

describe('without an as-of date it uses the viewer\'s own calendar day', () => {
  it('counts from today, not from the current instant', () => {
    // Late in the local day: a clock-based subtraction would add a fraction and
    // could tip the floor over; a calendar difference cannot.
    vi.useFakeTimers();
    vi.setSystemTime(new Date(2026, 2, 5, 23, 59, 30));   // local 2026-03-05
    expect(overdueDays(overdue('2026-03-01'))).toBe(4);
  });

  it('gives the same answer at the very start of that day', () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date(2026, 2, 5, 0, 0, 1));
    expect(overdueDays(overdue('2026-03-01'))).toBe(4);
  });
});
