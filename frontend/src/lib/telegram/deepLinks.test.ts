// @vitest-environment jsdom
import { describe, it, expect, beforeEach, vi } from 'vitest';
import { startParam, botMiniAppLink, startParamToPath, captureReferralCode, readReferralCode } from './deepLinks';

// This repo's jsdom environment provides no storage of its own (see
// authLogoutContract.test.ts) — shim it the same way.
const LS = vi.hoisted(() => {
  const store = new Map<string, string>();
  const shim = {
    getItem: (k: string) => (store.has(k) ? store.get(k)! : null),
    setItem: (k: string, v: string) => { store.set(k, v); },
    removeItem: (k: string) => { store.delete(k); },
    clear: () => { store.clear(); },
    key: () => null,
    length: 0,
  };
  Object.defineProperty(globalThis, 'localStorage', {
    value: shim, configurable: true, writable: true,
  });
  return shim;
});

describe('telegram deep-link encode/decode (bot ⇄ Mini App contract)', () => {
  it('round-trips installment action params to the right customer screen', () => {
    const id = 'a1b2c3d4-1111-2222-3333-444455556666';
    expect(startParamToPath(startParam.pay(id), 'uz')).toBe(`/uz/me/installments/${id}/pay`);
    expect(startParamToPath(startParam.payoff(id), 'uz')).toBe(`/uz/me/installments/${id}/payoff`);
    expect(startParamToPath(startParam.receipt(id), 'ru')).toBe(`/ru/me/installments/${id}/receipt`);
    expect(startParamToPath(startParam.installment(id), 'uz')).toBe(`/uz/me/installments/${id}`);
  });

  it('maps the persona home shortcuts', () => {
    expect(startParamToPath('home', 'uz')).toBe('/uz/me/home');
    expect(startParamToPath('my', 'uz')).toBe('/uz/me/my');
    expect(startParamToPath('profile', 'ru')).toBe('/ru/me/profile');
  });

  it('maps invitation + referral params (join goes to the locale-less /i route)', () => {
    expect(startParamToPath(startParam.join('tok_ABC-123'), 'uz')).toBe('/i/tok_ABC-123');
    expect(startParamToPath('join_TOKEN1', 'uz')).toBe('/i/TOKEN1');
    expect(startParamToPath(startParam.referral('R8'), 'uz')).toBe('/uz/me/home?ref=R8');
  });

  it('rejects unknown kinds and malformed ids (no open-ended routing)', () => {
    expect(startParamToPath('bogus_x', 'uz')).toBeNull();
    expect(startParamToPath('pay_', 'uz')).toBeNull(); // empty id
    expect(startParamToPath('pay_../../etc/passwd', 'uz')).toBeNull(); // illegal chars
    expect(startParamToPath('pay_a b', 'uz')).toBeNull(); // space
    expect(startParamToPath('', 'uz')).toBeNull();
    expect(startParamToPath(null, 'uz')).toBeNull();
  });

  it('builds the t.me Mini App launch link', () => {
    expect(botMiniAppLink('MuddatoBot', 'pay_xyz')).toBe('https://t.me/MuddatoBot?startapp=pay_xyz');
    // start params are url-encoded
    expect(botMiniAppLink('MuddatoBot', 'a b')).toBe('https://t.me/MuddatoBot?startapp=a%20b');
  });
});

// M-20: the `home?ref=<code>` a referral deep link resolves to (see
// startParamToPath above) used to be lost entirely — `Navigate to="../my"`
// drops location.search, and nothing read the code before that happened.
describe('referral code capture (M-20)', () => {
  beforeEach(() => LS.clear());

  it('captures the code from a ref query string', () => {
    captureReferralCode('?ref=R8');
    expect(readReferralCode()).toBe('R8');
  });

  it('is a no-op when there is no ref param', () => {
    captureReferralCode('?utm_source=telegram');
    expect(readReferralCode()).toBeNull();
  });

  it('rejects a malformed code rather than storing it verbatim', () => {
    captureReferralCode('?ref=' + encodeURIComponent('../etc/passwd'));
    expect(readReferralCode()).toBeNull();
  });
});
