import { describe, it, expect } from 'vitest';
import { routeTitleKeyFor, isLandingPath } from './routeTitle';

describe('routeTitleKeyFor (centered Mini-App header + document.title)', () => {
  it('resolves a top-level dashboard page from its slug', () => {
    expect(routeTitleKeyFor('/uz/platform/overview')).toBe('nav.overview');
    expect(routeTitleKeyFor('/ru/tenant/customers')).toBe('nav.customers');
  });

  it('skips dynamic ids and uses the deepest RECOGNISED segment', () => {
    // /me/installments/:id → ":id" is unknown, falls back to "installments".
    expect(routeTitleKeyFor('/uz/me/installments/abc123')).toBe('nav.installments');
    // /me/installments/:id/pay → "pay" is recognised and deepest.
    expect(routeTitleKeyFor('/uz/me/installments/abc123/pay')).toBe('routeTitle.pay');
    expect(routeTitleKeyFor('/uz/me/installments/abc123/payoff')).toBe('routeTitle.payoff');
  });

  it('returns null for unrecognised paths (caller falls back)', () => {
    expect(routeTitleKeyFor('/uz/totally/unknown')).toBeNull();
    expect(routeTitleKeyFor('/')).toBeNull();
  });

  it('isLandingPath flags only the locale roots', () => {
    expect(isLandingPath('/')).toBe(true);
    expect(isLandingPath('/uz')).toBe(true);
    expect(isLandingPath('/ru')).toBe(true);
    expect(isLandingPath('/uz/platform/overview')).toBe(false);
  });
});
