// @vitest-environment jsdom
/**
 * L-1 (MUDD-70) — `_navConfig.test.ts` (29 tests) only asserts membership in
 * the config ARRAY; none of them click anything. That leaves the failure
 * mode QA actually reported — a sidebar entry that highlights and renders
 * fine but does nothing when tapped (a broken `to`, a swallowed click, dead
 * `pointer-events`) — completely uncovered. This renders the real `Sidebar`
 * with the real tenant nav config, clicks "Mijozlar" (Customers), and
 * asserts the route the app is actually on changed.
 */
import { describe, expect, it, afterEach } from 'vitest';
import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { MemoryRouter, Route, Routes, useLocation } from 'react-router';
import i18n from '@/i18n';
import { Sidebar } from './Sidebar';
import { sidebarFor } from '@/routes/_navConfig';

afterEach(() => cleanup());

/** Renders the current URL as text so a click's effect is assertable without
 *  reaching into the router's internals. */
function LocationProbe() {
  const { pathname } = useLocation();
  return <div data-testid="current-path">{pathname}</div>;
}

function renderTenantSidebar(startPath: string) {
  const items = sidebarFor('tenant', 'uz', i18n.getFixedT('uz'));
  return render(
    <MemoryRouter initialEntries={[startPath]}>
      <Sidebar items={items} />
      <Routes>
        <Route path="*" element={<LocationProbe />} />
      </Routes>
    </MemoryRouter>,
  );
}

describe('sidebar navigation — real clicks, not just config membership', () => {
  it('clicking "Mijozlar" actually navigates to the customers route', () => {
    renderTenantSidebar('/uz/tenant/overview');
    expect(screen.getByTestId('current-path').textContent).toBe('/uz/tenant/overview');

    const t = i18n.getFixedT('uz');
    const link = screen.getByRole('link', { name: t('nav.customers') });
    expect(link.getAttribute('href')).toBe('/uz/tenant/customers');

    fireEvent.click(link);

    expect(screen.getByTestId('current-path').textContent).toBe('/uz/tenant/customers');
  });

  it('every unlocked tenant sidebar entry is a real, clickable link to its own route', () => {
    renderTenantSidebar('/uz/tenant/overview');
    const items = sidebarFor('tenant', 'uz', i18n.getFixedT('uz'));
    for (const it of items.filter((i) => !i.locked)) {
      const link = screen.getByRole('link', { name: it.label });
      expect(link.getAttribute('href'), `${it.key} sidebar entry`).toBe(it.to);
      fireEvent.click(link);
      expect(screen.getByTestId('current-path').textContent, `clicking ${it.key}`).toBe(it.to);
    }
  });
});
