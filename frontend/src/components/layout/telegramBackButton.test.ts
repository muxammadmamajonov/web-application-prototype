/**
 * Telegram shows Back — not Close — on anything reached through the Menu.
 *
 * `MiniAppHeader` renders only inside Telegram, and it decides between the
 * native Back button and Telegram's "Close" pill by asking whether the current
 * path is a NAV ROOT. `AppShell` used to hand it every SIDEBAR item as a root,
 * which is wrong on the surface where it matters: the sidebar is the desktop
 * nav, and on mobile the only top-level destinations are the four bottom-bar
 * quick tabs plus the Menu hub.
 *
 * The consequence was not cosmetic. Opening Menu → Profil (or Sozlamalar, or
 * Adminlar) left Telegram showing "Close", so the one obvious control at the
 * top of the screen quit the Mini App entirely instead of returning to the menu
 * the user had just come from.
 *
 * This test is written against the real `_navConfig` exports rather than a
 * fixture, so adding a persona destination or reordering the quick tabs is
 * covered automatically — and it runs per persona, because "every role" is the
 * requirement, not an implementation detail.
 */
import { describe, expect, it } from 'vitest';
import { isDrillDownPath } from '@/lib/nav';
import { bottomNavFor, menuPathFor, sidebarFor } from '@/routes/_navConfig';

const LOCALE = 'uz';
const t = (k: string) => k;

/** The three personas that get an `AppShell` (and therefore a Menu hub). */
const PERSONAS = ['platform', 'tenant', 'staff'] as const;

/** Exactly what `AppShell` now passes to `MiniAppHeader`. */
function navRoots(persona: (typeof PERSONAS)[number]): string[] {
  return [
    ...bottomNavFor(persona, LOCALE, t).map((i) => i.to),
    menuPathFor(persona, LOCALE),
  ];
}

describe.each(PERSONAS)('%s — Telegram back button', (persona) => {
  const roots = navRoots(persona);
  const sidebar = sidebarFor(persona, LOCALE, t);
  const menuPath = menuPathFor(persona, LOCALE);

  it('has a menu hub and at least one quick tab', () => {
    expect(roots.length).toBeGreaterThan(1);
    expect(roots).toContain(menuPath);
  });

  it('keeps Close on the bottom-bar tabs — they are top level', () => {
    for (const item of bottomNavFor(persona, LOCALE, t)) {
      expect(isDrillDownPath(item.to, roots), `${item.to} must stay a root`).toBe(false);
    }
  });

  it('keeps Close on the Menu hub itself', () => {
    expect(isDrillDownPath(menuPath, roots)).toBe(false);
  });

  it('shows Back on every destination reached THROUGH the menu', () => {
    // Everything in the sidebar that is not a quick tab is, on mobile, only
    // reachable via the Menu — so each one must offer a way back to it.
    const throughMenu = sidebar.map((i) => i.to).filter((to) => !roots.includes(to));

    // Asserted non-empty per persona so this cannot pass vacuously. Every
    // persona has at least Profile behind its Menu — staff only since the route
    // was added; before that a clerk had no way to reach their own language
    // setting, details, or (on web) logout at all.
    expect(throughMenu.length, `${persona} has no menu-only destination`).toBeGreaterThan(0);

    const stuck = throughMenu.filter((to) => !isDrillDownPath(to, roots));

    expect(stuck,
      `these show Telegram's Close instead of Back, so the only control at the ` +
      `top of the screen quits the app instead of returning to the Menu:\n${stuck.join('\n')}`,
    ).toEqual([]);
  });
});

describe('the specific destinations reported as broken', () => {
  // Named explicitly because these are the ones a person actually hit.
  it.each([
    ['platform', '/uz/platform/profile'],
    ['platform', '/uz/platform/settings'],
    ['platform', '/uz/platform/admins'],
    ['tenant', '/uz/tenant/profile'],
    ['tenant', '/uz/tenant/settings'],
    ['staff', '/uz/staff/profile'],
  ] as const)('%s %s shows Back', (persona, path) => {
    expect(isDrillDownPath(path, navRoots(persona))).toBe(true);
  });
});

describe('the regression this replaced', () => {
  it('every-sidebar-item-is-a-root would have shown Close on Profile', () => {
    // The old behaviour, reconstructed: with the full sidebar as roots, a
    // menu-only destination is itself a root and never gets Back. Keeping this
    // here documents WHY the root set is the bottom bar and not the sidebar.
    const oldRoots = [
      ...sidebarFor('platform', LOCALE, t).map((i) => i.to),
      menuPathFor('platform', LOCALE),
    ];
    expect(isDrillDownPath('/uz/platform/profile', oldRoots)).toBe(false);
    expect(isDrillDownPath('/uz/platform/profile', navRoots('platform'))).toBe(true);
  });
});
