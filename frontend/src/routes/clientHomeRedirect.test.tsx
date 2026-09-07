// @vitest-environment jsdom
/**
 * M-20 (MUDD-68) — a `ref_<code>` deep link resolves to `home?ref=<code>`
 * (see `deepLinks.ts`), but the `home` → `my` redirect used a bare
 * `<Navigate to="../my" replace />`, whose `to` prop does not carry
 * `location.search`. The referral code vanished on the very first hop, and
 * nothing downstream ever read it. `ClientHomeRedirect` fixes both halves:
 * it captures the code into storage AND keeps it on the URL across the hop.
 */
import { describe, expect, it, afterEach, beforeEach, vi } from 'vitest';
import { cleanup, render, screen } from '@testing-library/react';
import { MemoryRouter, Route, Routes, useLocation } from 'react-router';
import { ClientHomeRedirect } from './router';
import { readReferralCode } from '@/lib/telegram/deepLinks';

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

afterEach(() => cleanup());
beforeEach(() => LS.clear());

function LocationProbe() {
  const { pathname, search } = useLocation();
  return <div data-testid="landed">{pathname}{search}</div>;
}

/** Mirrors production nesting: `ClientShell` owns its own `<Routes>` under a
 *  `/:locale/me/*` parent, which is what makes `../my` resolve to a sibling. */
function renderClientHome(startPath: string) {
  return render(
    <MemoryRouter initialEntries={[startPath]}>
      <Routes>
        <Route
          path="/uz/me/*"
          element={
            <Routes>
              <Route path="home" element={<ClientHomeRedirect />} />
              <Route path="my" element={<LocationProbe />} />
            </Routes>
          }
        />
      </Routes>
    </MemoryRouter>,
  );
}

describe('ClientHomeRedirect', () => {
  it('lands on "my" and keeps the referral code on the URL', () => {
    renderClientHome('/uz/me/home?ref=R8');
    expect(screen.getByTestId('landed').textContent).toBe('/uz/me/my?ref=R8');
  });

  it('captures the referral code into storage before redirecting', () => {
    renderClientHome('/uz/me/home?ref=R8');
    expect(readReferralCode()).toBe('R8');
  });

  it('redirects cleanly with no ref param at all', () => {
    renderClientHome('/uz/me/home');
    expect(screen.getByTestId('landed').textContent).toBe('/uz/me/my');
    expect(readReferralCode()).toBeNull();
  });
});
