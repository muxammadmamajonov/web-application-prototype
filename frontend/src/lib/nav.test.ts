import { describe, it, expect } from 'vitest';
import { isSubpagePath, isDrillDownPath } from './nav';

// Realistic client roots, incl. the home which the others nest under.
const roots = ['/uz/me', '/uz/me/installments', '/uz/me/profile'];

describe('isSubpagePath (#33 — hide bottom bar on subpages)', () => {
  it('a known root tab is NOT a subpage (bar stays)', () => {
    expect(isSubpagePath('/uz/me', roots)).toBe(false);
    expect(isSubpagePath('/uz/me/installments', roots)).toBe(false);
    expect(isSubpagePath('/uz/me/installments/', roots)).toBe(false); // trailing slash
  });

  it('a drill-down deeper than a root IS a subpage (bar hides)', () => {
    expect(isSubpagePath('/uz/me/installments/abc123', roots)).toBe(true);
    expect(isSubpagePath('/uz/me/installments/abc123/pay', roots)).toBe(true);
  });

  it('the persona home is not flagged even though roots nest under it', () => {
    // /uz/me/installments starts with "/uz/me/" but is itself a root → not a subpage.
    expect(isSubpagePath('/uz/me/installments', roots)).toBe(false);
  });

  it('fail-safe: an unmatched path keeps the bar', () => {
    expect(isSubpagePath('/uz/tenant/overview', roots)).toBe(false);
  });
});

// Client top-level tabs/pages (full paths). Drill-downs (installment/pay/...)
// sit BESIDE these under /uz/me, not under any single tab.
const clientRoots = ['/uz/me/home', '/uz/me/my', '/uz/me/profile', '/uz/me/privacy'];

describe('isDrillDownPath (Telegram native Back vs Close)', () => {
  it('top-level tabs are NOT drill-downs → keep "Close"', () => {
    for (const r of clientRoots) expect(isDrillDownPath(r, clientRoots)).toBe(false);
    expect(isDrillDownPath('/uz/me/home/', clientRoots)).toBe(false); // trailing slash
  });

  it('sibling drill-downs ARE flagged even though they nest under no tab', () => {
    expect(isDrillDownPath('/uz/me/installments/abc123', clientRoots)).toBe(true);
    expect(isDrillDownPath('/uz/me/installments/abc123/pay', clientRoots)).toBe(true);
    expect(isDrillDownPath('/uz/me/installments/abc123/payoff', clientRoots)).toBe(true);
    expect(isDrillDownPath('/uz/me/installments/abc123/receipt', clientRoots)).toBe(true);
  });

  it('admin shells (every page is a top-level root) never show Back', () => {
    const adminRoots = ['/uz/platform/overview', '/uz/platform/tenants', '/uz/platform/payments'];
    for (const r of adminRoots) expect(isDrillDownPath(r, adminRoots)).toBe(false);
    // An unknown admin path under the same base would be treated as a drill-down,
    // but the admin shells expose only known flat routes, so this never occurs.
  });

  it('a path under a different persona base is not flagged', () => {
    expect(isDrillDownPath('/uz/platform/overview', clientRoots)).toBe(false);
  });
});
