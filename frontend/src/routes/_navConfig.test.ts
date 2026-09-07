/**
 * Phase 3 §15 — MVP scope-leakage guard.
 *
 * Phase 2 deferred (LATER) or removed a set of capabilities. Their page source
 * stays in the repo, but no navigation surface the MVP renders — sidebar, mobile
 * bottom bar, or Menu hub — may offer a way into one. These tests fail loudly if
 * a deferred destination is ever re-added to a persona's navigation.
 */
import { describe, expect, it } from 'vitest';
import { QUICK_KEYS, bottomNavFor, menuItemsFor, sidebarFor } from './_navConfig';
import type { Persona } from '@/types';

const t = (k: string) => k;
const PERSONAS: Persona[] = ['platform', 'tenant', 'staff', 'client'];

/** Nav keys Phase 2 classified LATER or REMOVE, per persona. */
const DEFERRED_KEYS: Record<Persona, string[]> = {
  platform: ['users', 'broadcasts', 'ads', 'referrals', 'catalog'],
  // (#15) Reports joins the deferred set: Overview already carries the
  // figures a shop reads daily, so a second place to read them splits
  // attention. The PAGE and its endpoints are retained — this is about
  // reachability, not deletion.
  tenant:   ['broadcasts', 'reports'],
  staff:    ['broadcasts', 'settings'],
  client:   [],
};

/** Destinations the MVP must keep offering, per persona. */
const RETAINED_KEYS: Record<Persona, string[]> = {
  platform: ['tenants', 'payments', 'plans', 'admins', 'audit', 'settings', 'profile'],
  tenant:   ['overview', 'customers', 'installments', 'payments', 'staff', 'billing', 'import', 'settings', 'profile'],
  staff:    ['today', 'customers', 'installments', 'payments'],
  client:   ['home', 'profile'],
};

describe.each(PERSONAS)('navigation scope — %s', (persona) => {
  const sidebar = sidebarFor(persona === 'client' ? 'client' : persona, 'uz', t);
  const bottom  = bottomNavFor(persona, 'uz', t);
  const menu    = menuItemsFor(persona, 'uz', t);

  it('sidebar offers no deferred destination', () => {
    const keys = sidebar.map((i) => i.key);
    for (const d of DEFERRED_KEYS[persona]) expect(keys).not.toContain(d);
  });

  it('mobile bottom bar offers no deferred destination', () => {
    const keys = bottom.map((i) => i.key);
    for (const d of DEFERRED_KEYS[persona]) expect(keys).not.toContain(d);
  });

  it('menu hub offers no deferred destination', () => {
    const keys = menu.map((i) => i.key);
    for (const d of DEFERRED_KEYS[persona]) expect(keys).not.toContain(d);
  });

  it('sidebar still offers every retained destination', () => {
    const keys = sidebar.map((i) => i.key);
    for (const r of RETAINED_KEYS[persona]) expect(keys).toContain(r);
  });

  it('renders no permanently locked row', () => {
    // (UX-016) A locked row is a dead click — the MVP presents none.
    expect(sidebar.filter((i) => i.locked)).toHaveLength(0);
    expect(menu.filter((i) => i.locked)).toHaveLength(0);
  });

  it('every quick tab resolves to a retained destination', () => {
    // (UX-061) The bar has only four slots; none may be spent on deferred scope.
    const sidebarKeys = new Set(sidebar.map((i) => i.key));
    for (const k of QUICK_KEYS[persona]) {
      expect(DEFERRED_KEYS[persona]).not.toContain(k);
      expect(sidebarKeys.has(k)).toBe(true);
    }
    expect(bottom.length).toBeGreaterThan(0);
    expect(bottom.length).toBeLessThanOrEqual(4);
  });

  it('quick tabs and menu hub partition the sidebar without overlap or loss', () => {
    const quick = new Set(QUICK_KEYS[persona]);
    const menuKeys = new Set(menu.map((i) => i.key));
    for (const item of sidebar) {
      // Each retained destination is reachable exactly once: as a quick tab or
      // as a hub row — never both, never neither.
      expect(quick.has(item.key) !== menuKeys.has(item.key)).toBe(true);
    }
  });
});

describe('customer navigation', () => {
  it('home points at the retained customer home, not the deferred welcome hub', () => {
    const home = sidebarFor('client', 'uz', t).find((i) => i.key === 'home');
    expect(home?.to).toBe('/uz/me/my');
  });
});
