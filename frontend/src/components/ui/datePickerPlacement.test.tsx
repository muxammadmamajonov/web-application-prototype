// @vitest-environment jsdom
/**
 * FB-002 / FB-008 — the calendar must be reachable, and selecting a day must end
 * the interaction.
 *
 * MEASURED ON THE RUNNING APP at 320×568 before the fix: the last calendar row
 * rendered at y=881–925 against a 568px viewport with the fixed bottom nav at
 * y=487. The row was 357px below the fold, `elementFromPoint` returned null, and
 * a tap could not reach it — which is also why the calendar "would not close":
 * the tap never landed on a day, so nothing committed and nothing dismissed.
 *
 * One root cause, two findings. The panel is now portalled and positioned
 * against the VISUAL viewport minus whatever a bottom-anchored surface declares
 * through `--app-bottom-obstruction`, so no device-specific constant appears
 * anywhere.
 *
 * jsdom has no layout engine, so the geometry itself is measured in a browser
 * and recorded in the pass report. What this file pins is the arithmetic and the
 * state machine.
 */
import { describe, expect, it, beforeEach, afterEach, vi } from 'vitest';
import { cleanup, render, screen, fireEvent, act } from '@testing-library/react';
import i18n from '@/i18n';
import { DatePicker } from '@/components/ui/DatePicker';
import {
  claimBottomObstruction, reportBottomObstruction, resetBottomObstruction,
} from '@/lib/bottomObstruction';

function Host({ initial = '', onPick }: { initial?: string; onPick?: (v: string) => void }) {
  const [v, setV] = (globalThis as unknown as { __rs: typeof import('react').useState }).__rs(initial);
  return <DatePicker value={v} onChange={(iso) => { setV(iso); onPick?.(iso); }} />;
}

// Keep the host tiny without importing useState twice.
import { useState } from 'react';
(globalThis as unknown as { __rs: unknown }).__rs = useState;

const panel = () => document.querySelector('[data-testid="date-picker-panel"]');
const trigger = () => document.getElementById('date') as HTMLButtonElement;
const days = () =>
  [...panel()!.querySelectorAll('button[role="gridcell"]:not([disabled])')] as HTMLButtonElement[];

/** Pretend a bottom-anchored surface of `px` is on screen. */
function declareObstruction(px: number) {
  reportBottomObstruction(claimBottomObstruction(), px);
}

beforeEach(async () => {
  await i18n.changeLanguage('uz');
  resetBottomObstruction();
});
afterEach(() => { cleanup(); resetBottomObstruction(); });

describe('the panel escapes its container', () => {
  it('renders in a portal, not inside the field', () => {
    const { container } = render(<Host />);
    fireEvent.click(trigger());
    expect(panel()).not.toBeNull();
    // Outside the component's own subtree: an ancestor with `overflow:hidden`
    // or a lower stacking context can no longer clip or cover it.
    expect(container.contains(panel())).toBe(false);
    expect(document.body.contains(panel())).toBe(true);
  });

  it('is fixed to the viewport and above the bottom navigation', () => {
    render(<Host />);
    fireEvent.click(trigger());
    const el = panel() as HTMLElement;
    // jsdom applies no stylesheet, so the utility class is the assertion —
    // the rendered `position: fixed` is measured in a browser instead.
    expect(el.className).toMatch(/\bfixed\b/);
    // The nav is z-50 and a bottom sheet is z-80; portalled to <body> the panel
    // is outside their stacking contexts and has to out-rank BOTH.
    expect(el.className).toMatch(/z-\[111\]/);
  });

  it('always carries an explicit max height, so a short screen scrolls it', () => {
    render(<Host />);
    fireEvent.click(trigger());
    const el = panel() as HTMLElement;
    expect(el.style.maxHeight).toMatch(/^\d+px$/);
    expect(el.className).toMatch(/overflow-y-auto/);
  });

  it('carries no device-specific pixel constant in its source', async () => {
    const { readFileSync } = await import('node:fs');
    const { resolve } = await import('node:path');
    for (const f of ['DatePicker.tsx', '../../hooks/useAnchoredOverlay.ts',
                     '../../hooks/useBottomObstruction.ts', '../../lib/bottomObstruction.ts']) {
      const src = readFileSync(resolve(__dirname, f), 'utf8');
      // Viewport heights and nav heights people hard-code.
      for (const magic of ['568', '667', '812', '844', '896', '81px', '64px']) {
        expect(src).not.toContain(magic);
      }
    }
  });
});

describe('placement uses the space that is actually free', () => {
  const setViewport = (h: number) => {
    Object.defineProperty(window, 'innerHeight', { value: h, configurable: true });
    Object.defineProperty(window, 'innerWidth', { value: 320, configurable: true });
  };

  it('opens below the trigger when there is room', () => {
    setViewport(900);
    vi.spyOn(HTMLElement.prototype, 'getBoundingClientRect').mockReturnValue(
      { top: 100, bottom: 144, left: 16, right: 300, width: 284, height: 44, x: 16, y: 100, toJSON: () => ({}) } as DOMRect,
    );
    render(<Host />);
    fireEvent.click(trigger());
    expect((panel() as HTMLElement).dataset.side).toBe('bottom');
    vi.restoreAllMocks();
  });

  it('flips above when the bottom surface leaves too little room', () => {
    setViewport(568);
    declareObstruction(81);
    vi.spyOn(HTMLElement.prototype, 'getBoundingClientRect').mockReturnValue(
      { top: 420, bottom: 464, left: 16, right: 300, width: 284, height: 44, x: 16, y: 420, toJSON: () => ({}) } as DOMRect,
    );
    render(<Host />);
    fireEvent.click(trigger());
    const el = panel() as HTMLElement;
    expect(el.dataset.side).toBe('top');
    // …and it never reaches into the obstructed strip.
    expect(Number.parseInt(el.style.top, 10)).toBeGreaterThanOrEqual(0);
    vi.restoreAllMocks();
  });

  it('subtracts the declared obstruction from the space below', () => {
    // The trigger sits high, so both runs stay on the same side and the only
    // difference is the obstruction.
    setViewport(900);
    vi.spyOn(HTMLElement.prototype, 'getBoundingClientRect').mockReturnValue(
      { top: 60, bottom: 104, left: 16, right: 300, width: 284, height: 44, x: 16, y: 60, toJSON: () => ({}) } as DOMRect,
    );
    render(<Host />);
    fireEvent.click(trigger());
    const free = panel() as HTMLElement;
    const freeHeight = Number.parseInt(free.style.maxHeight, 10);
    expect(free.dataset.side).toBe('bottom');
    cleanup();

    declareObstruction(200);
    render(<Host />);
    fireEvent.click(trigger());
    const squeezed = panel() as HTMLElement;
    expect(squeezed.dataset.side).toBe('bottom');
    expect(Number.parseInt(squeezed.style.maxHeight, 10)).toBe(freeHeight - 200);
    vi.restoreAllMocks();
  });

  it('flips rather than squeezing when the obstruction leaves it unusable', () => {
    setViewport(900);
    vi.spyOn(HTMLElement.prototype, 'getBoundingClientRect').mockReturnValue(
      { top: 600, bottom: 644, left: 16, right: 300, width: 284, height: 44, x: 16, y: 600, toJSON: () => ({}) } as DOMRect,
    );
    declareObstruction(200);
    render(<Host />);
    fireEvent.click(trigger());
    const el = panel() as HTMLElement;
    // 900 − 644 − 8 − 200 = 48px below, versus 592px above.
    expect(el.dataset.side).toBe('top');
    expect(Number.parseInt(el.style.maxHeight, 10)).toBeGreaterThan(400);
    vi.restoreAllMocks();
  });

  it('never runs off the left or right edge', () => {
    setViewport(900);
    vi.spyOn(HTMLElement.prototype, 'getBoundingClientRect').mockReturnValue(
      { top: 100, bottom: 144, left: 290, right: 574, width: 284, height: 44, x: 290, y: 100, toJSON: () => ({}) } as DOMRect,
    );
    render(<Host />);
    fireEvent.click(trigger());
    const el = panel() as HTMLElement;
    const left = Number.parseInt(el.style.left, 10);
    const width = Number.parseInt(el.style.width, 10);
    expect(left).toBeGreaterThanOrEqual(0);
    expect(left + width).toBeLessThanOrEqual(320);
    vi.restoreAllMocks();
  });
});

describe('one source of open/selected state', () => {
  it('selecting a day commits the value AND closes', () => {
    const onPick = vi.fn();
    render(<Host onPick={onPick} />);
    fireEvent.click(trigger());
    const d = days()[5];
    const label = d.getAttribute('aria-label');
    fireEvent.click(d);
    expect(onPick).toHaveBeenCalledTimes(1);
    expect(panel()).toBeNull();
    expect(trigger().textContent).toContain(label!.split(' ')[0]);
  });

  it('month navigation does NOT close', () => {
    render(<Host />);
    fireEvent.click(trigger());
    fireEvent.click(screen.getByLabelText(i18n.t('a11y.prevMonth')));
    expect(panel()).not.toBeNull();
    fireEvent.click(screen.getByLabelText(i18n.t('a11y.nextMonth')));
    expect(panel()).not.toBeNull();
  });

  it('clicking outside closes without changing the committed value', () => {
    const onPick = vi.fn();
    render(<Host initial="2026-03-10" onPick={onPick} />);
    const before = trigger().textContent;
    fireEvent.click(trigger());
    act(() => { fireEvent.mouseDown(document.body); });
    expect(panel()).toBeNull();
    expect(onPick).not.toHaveBeenCalled();
    expect(trigger().textContent).toBe(before);
  });

  it('Escape closes and hands focus back to the trigger', () => {
    render(<Host />);
    fireEvent.click(trigger());
    act(() => { fireEvent.keyDown(document, { key: 'Escape' }); });
    expect(panel()).toBeNull();
    expect(document.activeElement).toBe(trigger());
  });

  it('Escape during IME composition is not a dismissal', () => {
    render(<Host />);
    fireEvent.click(trigger());
    act(() => { fireEvent.keyDown(document, { key: 'Escape', isComposing: true }); });
    expect(panel()).not.toBeNull();
    act(() => { fireEvent.keyDown(document, { key: 'Escape', keyCode: 229 }); });
    expect(panel()).not.toBeNull();
  });

  it('reopening shows the committed date, whatever month was browsed last', () => {
    render(<Host initial="2026-03-10" />);
    fireEvent.click(trigger());
    fireEvent.click(screen.getByLabelText(i18n.t('a11y.prevMonth')));
    fireEvent.click(screen.getByLabelText(i18n.t('a11y.prevMonth')));
    act(() => { fireEvent.mouseDown(document.body); });

    fireEvent.click(trigger());
    const selected = panel()!.querySelector('[aria-selected="true"]');
    expect(selected).not.toBeNull();
    expect(selected!.getAttribute('aria-label')).toContain('10');
    // …and the keyboard lands on it, not on the first of the month.
    expect(panel()!.querySelector('button[role="gridcell"][tabindex="0"]')).toBe(selected);
  });

  it('announces itself, so a screen reader knows what opened', () => {
    render(<Host />);
    expect(trigger().getAttribute('aria-haspopup')).toBe('dialog');
    expect(trigger().getAttribute('aria-expanded')).toBe('false');
    fireEvent.click(trigger());
    expect(trigger().getAttribute('aria-expanded')).toBe('true');
    expect(panel()!.getAttribute('role')).toBe('dialog');
    expect(panel()!.getAttribute('aria-label')).toBeTruthy();
  });
});
