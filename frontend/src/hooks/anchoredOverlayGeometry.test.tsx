// @vitest-environment jsdom
/**
 * FB-002 review correction — the band is a ceiling, not a suggestion.
 *
 * Batch 2 computed `maxHeight = Math.max(MIN_USABLE_HEIGHT, gap)`. When BOTH
 * gaps were smaller than the minimum — a soft keyboard open on a short phone,
 * or a field low in a Telegram viewport — that handed the panel more height
 * than the screen had, and the calendar's last rows went off the bottom again:
 * the exact failure the finding was raised about, reintroduced by the fallback
 * meant to prevent it.
 *
 * Every case here asserts the same four invariants, so a future change cannot
 * satisfy one viewport by breaking another.
 */
import { describe, expect, it, beforeEach, afterEach, vi } from 'vitest';
import { cleanup, render, act } from '@testing-library/react';
import { useAnchoredOverlay, type OverlayPosition } from '@/hooks/useAnchoredOverlay';
import {
  claimBottomObstruction, reportBottomObstruction, resetBottomObstruction,
} from '@/lib/bottomObstruction';

const PANEL_WIDTH = 336;

let triggerRect: Partial<DOMRect> = {};

function setViewport({ w, h, offsetTop = 0 }: { w: number; h: number; offsetTop?: number }) {
  Object.defineProperty(window, 'innerWidth', { value: w, configurable: true });
  Object.defineProperty(window, 'innerHeight', { value: h + offsetTop, configurable: true });
  Object.defineProperty(window, 'visualViewport', {
    value: {
      offsetTop, offsetLeft: 0, width: w, height: h,
      addEventListener() {}, removeEventListener() {},
    },
    configurable: true,
  });
}

function setTrigger(top: number, height = 44, left = 16) {
  triggerRect = { top, bottom: top + height, height, left, right: left + 288, width: 288 };
}

function Host({ open }: { open: boolean }) {
  const { triggerRef, pos } = useAnchoredOverlay(open, PANEL_WIDTH);
  return (
    <>
      <button ref={(el) => { triggerRef.current = el; }} />
      {pos && <i data-testid="pos" data-json={JSON.stringify(pos)} />}
    </>
  );
}

function place(): OverlayPosition {
  const el = document.querySelector('[data-testid="pos"]') as HTMLElement;
  return JSON.parse(el.dataset.json!) as OverlayPosition;
}

/**
 * The panel's own painted box, after the lift a top-side panel applies.
 * `maxHeight` is the worst case, so checking the box at full height checks
 * every shorter one too.
 */
function box(p: OverlayPosition) {
  const top = p.transform ? p.top - p.maxHeight : p.top;
  return { top, bottom: top + p.maxHeight, left: p.left, right: p.left + p.width };
}

function expectInsideBand(p: OverlayPosition, obstruction = 0) {
  const vv = window.visualViewport!;
  const viewTop = vv.offsetTop;
  const viewBottom = viewTop + vv.height - obstruction;
  const b = box(p);
  expect(p.maxHeight).toBeGreaterThanOrEqual(0);
  expect(p.width).toBeGreaterThanOrEqual(0);
  expect(b.top).toBeGreaterThanOrEqual(viewTop);            // never above the viewport
  expect(b.bottom).toBeLessThanOrEqual(viewBottom);         // never under the obstruction
  expect(b.left).toBeGreaterThanOrEqual(0);
  expect(b.right).toBeLessThanOrEqual(vv.width);
}

beforeEach(() => {
  resetBottomObstruction();
  vi.spyOn(HTMLElement.prototype, 'getBoundingClientRect').mockImplementation(
    () => ({ x: 0, y: 0, toJSON: () => ({}), ...triggerRect } as DOMRect),
  );
});
afterEach(() => { cleanup(); vi.restoreAllMocks(); resetBottomObstruction(); });

describe('320 × 568 — the smallest phone the product supports', () => {
  it('places the calendar clear of an 81px navigation', () => {
    setViewport({ w: 320, h: 568 });
    reportBottomObstruction(claimBottomObstruction(), 81);
    setTrigger(420);
    render(<Host open />);
    const p = place();
    expect(p.side).toBe('top');
    expectInsideBand(p, 81);
  });

  it('a field near the top still opens downward', () => {
    setViewport({ w: 320, h: 568 });
    reportBottomObstruction(claimBottomObstruction(), 81);
    setTrigger(60);
    render(<Host open />);
    const p = place();
    expect(p.side).toBe('bottom');
    expectInsideBand(p, 81);
  });
});

describe('390 × 844 — the founder\'s phone', () => {
  it('prefers the side that shows the whole month, not merely a usable one', () => {
    setViewport({ w: 390, h: 844 });
    reportBottomObstruction(claimBottomObstruction(), 81);
    setTrigger(580);
    render(<Host open />);
    const p = place();
    // 175px below against 564px above: usable is not a reason to scroll.
    expect(p.side).toBe('top');
    expect(p.maxHeight).toBeGreaterThan(360);
    expectInsideBand(p, 81);
  });

  it('hugs the trigger when placed above, whatever the content measures', () => {
    setViewport({ w: 390, h: 844 });
    setTrigger(600);
    render(<Host open />);
    const p = place();
    expect(p.side).toBe('top');
    // Lifted by its own height, so a short panel does not float near the top of
    // the screen with half a viewport between it and its own field.
    expect(p.transform).toBe('translateY(-100%)');
    expect(p.top).toBe(600 - 8);
  });
});

describe('the keyboard-reduced viewport', () => {
  it('uses the visual viewport, not the layout one', () => {
    // 844 tall, keyboard takes 380: the visual viewport is 464 and the layout
    // one still claims 844. Measuring the wrong one puts the panel under the
    // keyboard, which is where this whole finding started.
    setViewport({ w: 390, h: 464 });
    setTrigger(300);
    render(<Host open />);
    expectInsideBand(place());
  });

  it('stays inside the band when the keyboard AND a footer are present', () => {
    setViewport({ w: 390, h: 400 });
    reportBottomObstruction(claimBottomObstruction(), 68);
    setTrigger(200);
    render(<Host open />);
    expectInsideBand(place(), 68);
  });
});

describe('when neither side is big enough', () => {
  it('never claims more height than the band actually has', () => {
    // The regression: a 260px band with 26px below and 100px above used to
    // return maxHeight 180 — larger than either gap, and larger than the room
    // left once the obstruction was subtracted.
    setViewport({ w: 360, h: 320 });
    reportBottomObstruction(claimBottomObstruction(), 60);
    setTrigger(110);
    render(<Host open />);
    const p = place();
    expect(p.maxHeight).toBeLessThanOrEqual(320 - 60 - 16);
    expectInsideBand(p, 60);
  });

  it('takes the whole band and scrolls inside it rather than overflowing', () => {
    setViewport({ w: 360, h: 300 });
    reportBottomObstruction(claimBottomObstruction(), 60);
    setTrigger(100);
    render(<Host open />);
    const p = place();
    // Overlapping the trigger is recoverable; being off-screen is not.
    expect(p.maxHeight).toBe(300 - 60 - 16);
    expect(p.top).toBe(8);
    expectInsideBand(p, 60);
  });

  it('survives a viewport with no usable space at all', () => {
    setViewport({ w: 200, h: 40 });
    reportBottomObstruction(claimBottomObstruction(), 30);
    setTrigger(5);
    render(<Host open />);
    const p = place();
    expect(p.maxHeight).toBeGreaterThanOrEqual(0);
    expect(p.top).toBeGreaterThanOrEqual(0);
    expect(p.width).toBeGreaterThanOrEqual(0);
    expect(p.left).toBeGreaterThanOrEqual(0);
  });
});

describe('a trigger that is not fully on screen', () => {
  it('handles a field scrolled off the top', () => {
    setViewport({ w: 390, h: 844 });
    setTrigger(-120);
    render(<Host open />);
    const p = place();
    expect(p.side).toBe('bottom');
    expectInsideBand(p);
  });

  it('handles a field scrolled below the fold', () => {
    setViewport({ w: 390, h: 844 });
    reportBottomObstruction(claimBottomObstruction(), 81);
    setTrigger(900);
    render(<Host open />);
    expectInsideBand(place(), 81);
  });

  it('keeps a field near the right edge on screen', () => {
    setViewport({ w: 390, h: 844 });
    setTrigger(200, 44, 340);
    render(<Host open />);
    const p = place();
    expect(p.left).toBeGreaterThanOrEqual(8);
    expect(p.left + p.width).toBeLessThanOrEqual(382);
  });

  it('offsets everything by a scrolled visual viewport', () => {
    setViewport({ w: 390, h: 600, offsetTop: 120 });
    setTrigger(400);
    render(<Host open />);
    const p = place();
    expect(box(p).top).toBeGreaterThanOrEqual(120);
    expectInsideBand(p);
  });
});

describe('an open panel reacts to the band moving', () => {
  it('re-measures when a footer appears under it', () => {
    setViewport({ w: 390, h: 844 });
    setTrigger(600);
    render(<Host open />);
    const before = place();
    expect(before.side).toBe('top');

    const token = claimBottomObstruction();
    act(() => { reportBottomObstruction(token, 200); });
    const after = place();
    // The panel is above the trigger, so its own box need not move — but the
    // band did, and the hook must have recomputed against it.
    expectInsideBand(after, 200);

    setTrigger(100);
    act(() => { reportBottomObstruction(token, 400); });
    expectInsideBand(place(), 400);
  });

  it('re-measures on a visual-viewport resize', () => {
    setViewport({ w: 390, h: 844 });
    setTrigger(600);
    render(<Host open />);
    expect(place().side).toBe('top');

    setViewport({ w: 390, h: 844 });
    setTrigger(100);
    act(() => { window.dispatchEvent(new Event('resize')); });
    expect(place().side).toBe('bottom');
  });
});
