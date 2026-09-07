import { describe, it, expect, vi } from 'vitest';

// format.ts imports @/i18n at module load. Stub it so the real i18next init
// (browser LanguageDetector) doesn't run under vitest's node environment, and so
// currency/locale-dependent output is deterministic. (#174)
vi.mock('@/i18n', () => ({
  default: {
    t: (key: string) => (key === 'billing.currency' ? "so'm" : key),
    language: 'uz',
  },
}));

import { money, moneyWithCurrency, shortMoney, pct, localToday, initials, displayName } from './format';

describe('money', () => {
  it('groups thousands with spaces', () => {
    expect(money(1240000)).toBe('1 240 000');
    expect(money('1240000')).toBe('1 240 000');
    expect(money(0)).toBe('0');
  });
  it('truncates fractional UZS (no minor units)', () => {
    expect(money(1000.99)).toBe('1 000');
  });
  it('returns the em-dash for null / undefined / non-finite', () => {
    expect(money(null)).toBe('—');
    expect(money(undefined)).toBe('—');
    expect(money('abc')).toBe('—');
    expect(money(Number.NaN)).toBe('—');
  });
});

describe('moneyWithCurrency', () => {
  it('appends the currency it is given', () => {
    // The currency is an argument, not an ambient lookup. It used to read the
    // single hardcoded `billing.currency`, which is how a shop trading in USD
    // saw UZS on every screen while its own contracts said USD.
    expect(moneyWithCurrency(1240000, 'USD')).toBe('1 240 000 USD');
    expect(moneyWithCurrency(null, 'USD')).toBe('— USD');
  });

  it('falls back to the platform default, never to a blank unit', () => {
    // A money figure with no unit at all is worse than one labelled with the
    // default — it is unreadable rather than merely imprecise.
    expect(moneyWithCurrency(1240000)).toBe('1 240 000 UZS');
  });
});

describe('shortMoney', () => {
  it('abbreviates millions and thousands', () => {
    expect(shortMoney(1_500_000)).toBe('1.5M');
    expect(shortMoney(1_000_000)).toBe('1M');
    expect(shortMoney(12_000)).toBe('12K');
    expect(shortMoney(999)).toBe('999');
  });
});

describe('pct', () => {
  it('formats a percentage with fixed digits', () => {
    expect(pct(12.345)).toBe('12.3%');
    expect(pct(50, 0)).toBe('50%');
  });
});

describe('localToday', () => {
  it('formats the LOCAL date as YYYY-MM-DD (zero-padded)', () => {
    expect(localToday(new Date(2026, 0, 5))).toBe('2026-01-05');
    expect(localToday(new Date(2026, 10, 9))).toBe('2026-11-09');
  });
});

describe('initials', () => {
  it('takes up to `max` uppercased initials', () => {
    expect(initials('Ali Valiyev')).toBe('AV');
    expect(initials('madonna')).toBe('M');
    expect(initials('a b c', 2)).toBe('AB');
  });
  it('returns ? for empty / null / blank', () => {
    expect(initials('')).toBe('?');
    expect(initials(null)).toBe('?');
    expect(initials('   ')).toBe('?');
  });
  it('reads through a Telegram handle instead of showing the @', () => {
    // `displayName` returns "@johndoe" for a user with no first/last name, and
    // that string is fed straight to <Avatar name=…>.
    expect(initials('@johndoe')).toBe('J');
  });
});

describe('displayName', () => {
  it('prefers the full name', () => {
    expect(displayName({ first: 'Ali', last: 'Valiyev', username: 'ali_v' })).toBe('Ali Valiyev');
  });
  it('uses whichever name part exists', () => {
    expect(displayName({ first: 'Ali', last: null, username: 'ali_v' })).toBe('Ali');
    expect(displayName({ first: null, last: 'Valiyev', username: 'ali_v' })).toBe('Valiyev');
    expect(displayName({ first: '   ', last: null, username: 'ali_v' })).toBe('@ali_v');
  });
  it('falls back to the @handle when there is no name', () => {
    expect(displayName({ first: null, last: null, username: 'johndoe' })).toBe('@johndoe');
    expect(displayName({ username: 'johndoe' })).toBe('@johndoe');
  });
  it('ends at the em-dash when there is nothing at all', () => {
    expect(displayName({ first: null, last: null, username: null })).toBe('—');
    expect(displayName({})).toBe('—');
  });
  it('never leaks a Telegram id — that was the `tg 12345` / `#12345` variant', () => {
    const nameless = { first: null, last: null, username: null, id: 12345 };
    expect(displayName(nameless)).toBe('—');
    expect(displayName(nameless)).not.toMatch(/\d/);
    expect(displayName({ first: null, last: null, username: 'johndoe', id: 12345 })).not.toMatch(/\d/);
  });
  it('takes an explicit fallback for names composed into a larger caption', () => {
    expect(displayName({ first: null, last: null, username: null }, { fallback: '' })).toBe('');
    // The override changes ONLY the tail — the chain above it is unchanged.
    expect(displayName({ first: null, last: null, username: 'johndoe' }, { fallback: '' })).toBe('@johndoe');
  });
});
