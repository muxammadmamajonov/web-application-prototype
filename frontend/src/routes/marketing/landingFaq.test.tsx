// @vitest-environment jsdom
/**
 * QA-011b / QA-014 — the landing FAQ is a disclosure widget, so each trigger
 * must publish its state (`aria-expanded`) and own its answer
 * (`aria-controls` → the answer's `id`), and the state it publishes must match
 * what is actually rendered on first paint.
 *
 * Both defects came from the same place. The answer was mounted conditionally,
 * so there was no stable element for `aria-controls` to point at; and the open
 * item was tracked by a literal `0` that matched none of the 1-based ids, so
 * "first item open" silently rendered as "all closed". The fix seeds the open
 * item FROM the id list, which is why `qs[0]` is asserted rather than `1`.
 *
 * Everything here is read from the rendered DOM after real clicks — an
 * `aria-expanded` that disagrees with what is on screen is the exact defect.
 */
import { describe, expect, it, beforeEach, afterEach, vi } from 'vitest';
import { render, screen, cleanup, fireEvent } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import i18n from '@/i18n';

vi.mock('@/components/layout/Logo', () => ({ Logo: () => <span>Muddato</span> }));
vi.mock('@/lib/queries', async (orig) => ({
  ...(await orig<Record<string, unknown>>()),
  usePublicPlans: () => ({ data: [], isLoading: false, isError: false, error: null, refetch: vi.fn() }),
}));

import LandingPage from './LandingPage';

const LOCALES = ['uz', 'ru'] as const;
type Lng = typeof LOCALES[number];
const IDS = [1, 2, 3, 4, 5, 6] as const;

function renderLanding(lng: Lng) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={[`/${lng}`]}>
        <Routes><Route path={`/${lng}`} element={<LandingPage />} /></Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

/** The six FAQ triggers, found by their question copy so the query is not
 *  coupled to markup. Returned in bundle order. */
function triggers(lng: Lng): HTMLElement[] {
  const t = i18n.getFixedT(lng);
  return IDS.map((i) => screen.getByRole('button', { name: t(`landing.faq.q${i}.q`) as string }));
}

/** The answer element a trigger claims to control. */
function panelOf(btn: HTMLElement): HTMLElement {
  const id = btn.getAttribute('aria-controls');
  expect(id, 'the trigger names an answer container').toBeTruthy();
  const el = document.getElementById(id!);
  expect(el, `aria-controls="${id}" resolves to an element`).toBeTruthy();
  return el!;
}

const isShown = (el: HTMLElement) => !el.hasAttribute('hidden');
const expanded = (btn: HTMLElement) => btn.getAttribute('aria-expanded') === 'true';

describe.each(LOCALES)('the landing FAQ disclosure in %s', (lng) => {
  beforeEach(async () => { cleanup(); await i18n.changeLanguage(lng); });
  afterEach(async () => { cleanup(); await i18n.changeLanguage('uz'); });

  it('gives every trigger a state and a unique answer to control', () => {
    renderLanding(lng);
    const btns = triggers(lng);
    expect(btns).toHaveLength(6);
    const ids = btns.map((b) => {
      expect(['true', 'false'], 'aria-expanded is published').toContain(b.getAttribute('aria-expanded'));
      return panelOf(b).id;
    });
    expect(new Set(ids).size, 'no two triggers control the same answer').toBe(6);
  });

  it('opens the first item on first paint, and says so', () => {
    renderLanding(lng);
    const btns = triggers(lng);
    expect(btns.filter(expanded)).toHaveLength(1);
    expect(expanded(btns[0]!), 'the FIRST item is the open one').toBe(true);
    // ARIA and the render must agree — the whole point of QA-011b + QA-014.
    btns.forEach((b) => expect(isShown(panelOf(b))).toBe(expanded(b)));
    expect(panelOf(btns[0]!).textContent).toBe(i18n.getFixedT(lng)('landing.faq.q1.a'));
  });

  it('collapses to nothing open when the open item is clicked again', () => {
    renderLanding(lng);
    const btns = triggers(lng);
    fireEvent.click(btns[0]!);
    expect(btns.filter(expanded)).toHaveLength(0);
    btns.forEach((b) => expect(isShown(panelOf(b))).toBe(false));
  });

  it('moves the open state when a different item is clicked', () => {
    renderLanding(lng);
    const btns = triggers(lng);
    fireEvent.click(btns[3]!);
    expect(expanded(btns[0]!)).toBe(false);
    expect(expanded(btns[3]!)).toBe(true);
    expect(btns.filter(expanded)).toHaveLength(1);
    btns.forEach((b) => expect(isShown(panelOf(b))).toBe(expanded(b)));
  });

  it('reopens correctly after a full close → open cycle', () => {
    renderLanding(lng);
    const btns = triggers(lng);
    fireEvent.click(btns[0]!);   // close the initially-open one
    fireEvent.click(btns[5]!);   // open the last
    fireEvent.click(btns[5]!);   // close it again
    fireEvent.click(btns[5]!);   // and back open
    expect(expanded(btns[5]!)).toBe(true);
    expect(isShown(panelOf(btns[5]!))).toBe(true);
    expect(btns.filter(expanded)).toHaveLength(1);
  });

  it('introduces no duplicate element ids on the page', () => {
    const { container } = renderLanding(lng);
    const ids = Array.from(container.querySelectorAll('[id]')).map((e) => e.id);
    expect(new Set(ids).size, `duplicate id among: ${ids.join(', ')}`).toBe(ids.length);
  });
});
