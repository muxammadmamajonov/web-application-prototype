// @vitest-environment jsdom
/**
 * Phase 4 — the three narrow-viewport findings, pinned at the component level.
 *
 * All three were *measured* in a real browser at 360px (UI-003 9.24px labels,
 * UI-004 a +12px Russian overflow, UI-013 39 truncated elements in ru). jsdom
 * has no layout engine, so it cannot re-measure any of that — the browser pass
 * is what proves the pixels. What these tests lock is the MECHANISM each fix
 * depends on, which is exactly what a future refactor would quietly undo:
 *
 *   UI-003  the label may not be single-line-truncated, and must sit on the
 *           11px floor via `.nav-label` rather than a sub-11px clamp.
 *   UI-004  the row must be able to shrink (`min-w-0`) while the trigger keeps
 *           its full visible label (`shrink-0` + `whitespace-nowrap`).
 *   UI-013  neither RowCard line may be `truncate`, and every segment handed to
 *           it must still be in the DOM.
 *
 * The product rule that no labelled control may become icon-only, and that no
 * RowCard segment may be dropped to make room, is asserted directly here.
 */
import { describe, it, expect, afterEach, vi } from 'vitest';
import { render, screen, cleanup, within } from '@testing-library/react';
import { MemoryRouter } from 'react-router';
import { Home, Users } from 'lucide-react';

// Echo the string default so labels are deterministic without booting i18next.
// `initReactI18next` must still be exported: MobileBottomNav pulls in
// `lib/format`, which imports the real i18n bootstrap.
vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (key: string, def?: unknown) => (typeof def === 'string' ? def : key),
    i18n: { language: 'ru' },
  }),
  initReactI18next: { type: '3rdParty', init: () => {} },
}));

import { MobileBottomNav } from '@/components/layout/MobileBottomNav';
import { FilterBar, FilterButton } from './FilterDrawer';
import { RowCard } from './RowCard';

const cls = (el: Element | null) => (el?.className ?? '').split(/\s+/);

afterEach(cleanup);

describe('UI-003 — bottom-nav labels are readable and complete', () => {
  /** The longest real labels in the product, in both locales. */
  const LONG = [
    { key: 'home', to: '/uz/tenant/overview', label: 'Bosh sahifa', icon: Home },
    { key: 'cust', to: '/uz/tenant/customers', label: 'Рассрочки', icon: Users },
  ];
  const renderNav = () => render(
    <MemoryRouter initialEntries={['/uz/tenant/overview']}>
      <MobileBottomNav items={LONG} allItems={[]} />
    </MemoryRouter>,
  );

  it('renders every label in full — copy is never shortened to fit', () => {
    renderNav();
    for (const { label } of LONG) expect(screen.getByText(label)).toBeTruthy();
  });

  it('does not truncate the label to a single line', () => {
    renderNav();
    for (const { label } of LONG) {
      expect(cls(screen.getByText(label)), `"${label}" must wrap, not clip`).not.toContain('truncate');
    }
  });

  it('sizes the label through .nav-label, which sits on the 11px floor', () => {
    renderNav();
    for (const { label } of LONG) expect(cls(screen.getByText(label))).toContain('nav-label');
  });

  it('keeps a visible text label on every tab — never icon-only', () => {
    renderNav();
    const tabs = screen.getAllByRole('link');
    expect(tabs.length).toBe(LONG.length);
    for (const tab of tabs) expect(tab.textContent?.trim().length ?? 0).toBeGreaterThan(0);
  });
});

describe('UI-004 — the filter toolbar fits a 360px Russian viewport', () => {
  const renderBar = (count: number) => render(
    <FilterBar>
      <div data-testid="search" className="w-full min-w-0" />
      <FilterButton count={count} onClick={() => {}} label="Фильтр" />
    </FilterBar>,
  );

  it('lets the row shrink instead of forcing the page wider', () => {
    const { container } = renderBar(1);
    expect(cls(container.firstElementChild), 'FilterBar needs min-w-0').toContain('min-w-0');
  });

  it('keeps the trigger at its natural width with its label intact', () => {
    renderBar(1);
    const btn = screen.getByRole('button');
    expect(cls(btn)).toContain('shrink-0');
    expect(cls(btn)).toContain('whitespace-nowrap');
    expect(btn.textContent).toContain('Фильтр');
  });

  it('still shows the count badge — the badge is what caused the overflow', () => {
    renderBar(3);
    expect(within(screen.getByRole('button')).getByText('3')).toBeTruthy();
  });

  it('does not collapse to an icon-only control when the badge is present', () => {
    renderBar(2);
    // "Фильтр2" — the label survives alongside the badge.
    expect(screen.getByRole('button').textContent).toContain('Фильтр');
  });
});

describe('UI-013 — RowCard reflows instead of clipping', () => {
  // The real Russian row that lost its money figure at 360px.
  const TITLE = 'Мадина Юсупова';
  const META = 'MDD-2026-0001 · 0/6 · осталось 6 000 000 сум';

  it('keeps both lines out of single-line truncation', () => {
    render(<RowCard initial="М" title={TITLE} subtitle={META} />);
    expect(cls(screen.getByText(TITLE)), 'the name must wrap, not clip').not.toContain('truncate');
    expect(cls(screen.getByText(META)), 'the meta line must wrap, not clip').not.toContain('truncate');
  });

  it('renders every metadata segment — none may be dropped or hidden', () => {
    render(<RowCard initial="М" title={TITLE} subtitle={META} />);
    const meta = screen.getByText(META);
    for (const segment of ['MDD-2026-0001', '0/6', '6 000 000']) {
      expect(meta.textContent, `"${segment}" must survive`).toContain(segment);
    }
  });

  it('allows a long unbroken token to break rather than push the row wide', () => {
    render(<RowCard initial="М" title={TITLE} subtitle={META} />);
    expect(cls(screen.getByText(META))).toContain('break-words');
  });

  it('still renders the trailing and badge slots alongside the wrapped text', () => {
    render(
      <RowCard
        initial="М" title={TITLE} subtitle={META}
        leftBadge={<span>badge</span>} trailing={<span>trail</span>}
      />,
    );
    expect(screen.getByText('badge')).toBeTruthy();
    expect(screen.getByText('trail')).toBeTruthy();
    expect(screen.getByText(META)).toBeTruthy();
  });
});
