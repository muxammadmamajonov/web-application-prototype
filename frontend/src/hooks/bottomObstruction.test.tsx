// @vitest-environment jsdom
/**
 * FB-002 review correction — more than one surface can own the bottom edge.
 *
 * Batch 2 shipped a single shared CSS variable written by whichever effect ran
 * last, and only `MobileBottomNav` ever wrote it. The contract wizard's sticky
 * Back/Next footer — the bar in the founder's screenshot — published nothing,
 * so the calendar cleared a navigation the sheet was covering and landed
 * underneath the footer that was actually there.
 *
 * Every publisher now holds its own token. This file pins the six ways that can
 * go wrong: one surface, the other surface, both, one leaving, one resizing,
 * and one going inactive for the soft keyboard.
 */
import { describe, expect, it, beforeEach, afterEach, vi } from 'vitest';
import { cleanup, render, act } from '@testing-library/react';
import {
  bottomCoverage, bottomObstruction, claimBottomObstruction,
  onBottomObstructionChange, releaseBottomObstruction, reportBottomObstruction,
  resetBottomObstruction,
} from '@/lib/bottomObstruction';
import { useBottomObstruction } from '@/hooks/useBottomObstruction';

const VIEWPORT_H = 800;

/** Every element reports itself as a bar of `data-h` pixels pinned to the
 *  bottom edge, unless `data-float` lifts it clear of that edge. */
function mockLayout() {
  vi.spyOn(HTMLElement.prototype, 'getBoundingClientRect').mockImplementation(
    function (this: HTMLElement) {
      const h = Number(this.dataset.h ?? 0);
      const lift = Number(this.dataset.float ?? 0);
      const bottom = VIEWPORT_H - lift;
      return {
        top: bottom - h, bottom, height: h, width: h ? 320 : 0,
        left: 0, right: 320, x: 0, y: bottom - h, toJSON: () => ({}),
      } as DOMRect;
    },
  );
}

function Bar({ h, active = true, float }: { h: number; active?: boolean; float?: number }) {
  const ref = useBottomObstruction<HTMLDivElement>(active);
  return <div ref={ref} data-h={h} data-float={float} />;
}

beforeEach(() => {
  resetBottomObstruction();
  Object.defineProperty(window, 'innerHeight', { value: VIEWPORT_H, configurable: true });
  mockLayout();
});
afterEach(() => { cleanup(); vi.restoreAllMocks(); resetBottomObstruction(); });

describe('the registry', () => {
  it('starts at zero and reports the deepest claim', () => {
    expect(bottomObstruction()).toBe(0);
    const nav = claimBottomObstruction();
    const footer = claimBottomObstruction();
    reportBottomObstruction(nav, 81);
    expect(bottomObstruction()).toBe(81);
    reportBottomObstruction(footer, 68);
    // MAX, not SUM: both bands start at the same bottom edge, so they overlap.
    // Adding them would reserve the same pixels twice.
    expect(bottomObstruction()).toBe(81);
    reportBottomObstruction(footer, 120);
    expect(bottomObstruction()).toBe(120);
  });

  it('a released claim cannot erase a claim that is still active', () => {
    const nav = claimBottomObstruction();
    const footer = claimBottomObstruction();
    reportBottomObstruction(nav, 81);
    reportBottomObstruction(footer, 120);
    releaseBottomObstruction(footer);
    // The bug this replaces: the leaving surface cleared the shared variable
    // and the navigation that was still on screen stopped being accounted for.
    expect(bottomObstruction()).toBe(81);
    releaseBottomObstruction(nav);
    expect(bottomObstruction()).toBe(0);
  });

  it('a stale token cannot write after release', () => {
    const token = claimBottomObstruction();
    reportBottomObstruction(token, 81);
    releaseBottomObstruction(token);
    reportBottomObstruction(token, 999);
    expect(bottomObstruction()).toBe(0);
  });

  it('notifies subscribers only when the effective value moves', () => {
    const seen = vi.fn();
    const off = onBottomObstructionChange(seen);
    const nav = claimBottomObstruction();
    const footer = claimBottomObstruction();
    reportBottomObstruction(nav, 81);
    expect(seen).toHaveBeenCalledTimes(1);
    // Shallower than the deepest claim: nothing an open panel needs to redo.
    reportBottomObstruction(footer, 60);
    expect(seen).toHaveBeenCalledTimes(1);
    reportBottomObstruction(footer, 140);
    expect(seen).toHaveBeenCalledTimes(2);
    off();
    reportBottomObstruction(footer, 200);
    expect(seen).toHaveBeenCalledTimes(2);
  });

  it('mirrors the effective value onto the CSS custom property', () => {
    const token = claimBottomObstruction();
    reportBottomObstruction(token, 81);
    expect(document.documentElement.style.getPropertyValue('--app-bottom-obstruction'))
      .toBe('81px');
    releaseBottomObstruction(token);
    expect(document.documentElement.style.getPropertyValue('--app-bottom-obstruction'))
      .toBe('');
  });
});

describe('measuring a surface', () => {
  it('counts a bar pinned to the bottom edge', () => {
    const el = document.createElement('div');
    el.dataset.h = '81';
    expect(bottomCoverage(el)).toBe(81);
  });

  it('ignores a surface that floats clear of the bottom edge', () => {
    // A CENTRED desktop dialog's footer covers nothing at the bottom of the
    // screen, so reserving space for it would push pickers around for nothing.
    const el = document.createElement('div');
    el.dataset.h = '68';
    el.dataset.float = '200';
    expect(bottomCoverage(el)).toBe(0);
  });

  it('counts only the part inside a keyboard-shrunk visual viewport', () => {
    // A fixed bar stays with the LAYOUT viewport, so when the keyboard shrinks
    // the visual one the bar is partly below it. Only the overlap obstructs.
    Object.defineProperty(window, 'visualViewport', {
      value: { offsetTop: 0, offsetLeft: 0, width: 320, height: 500,
               addEventListener() {}, removeEventListener() {} },
      configurable: true,
    });
    const el = document.createElement('div');
    el.dataset.h = '81';           // occupies 719–800, visual bottom is 500
    expect(bottomCoverage(el)).toBe(0);
    Object.defineProperty(window, 'visualViewport', { value: undefined, configurable: true });
  });

  it('is zero for a missing or collapsed element', () => {
    expect(bottomCoverage(null)).toBe(0);
    const empty = document.createElement('div');
    empty.dataset.h = '0';
    expect(bottomCoverage(empty)).toBe(0);
  });
});

describe('surfaces mounted together', () => {
  it('nav only', () => {
    render(<Bar h={81} />);
    expect(bottomObstruction()).toBe(81);
  });

  it('wizard footer only', () => {
    render(<Bar h={68} />);
    expect(bottomObstruction()).toBe(68);
  });

  it('both mounted — the deepest governs', () => {
    render(<><Bar h={81} /><Bar h={68} /></>);
    expect(bottomObstruction()).toBe(81);
  });

  it('one unmounts, the other keeps its claim', () => {
    function Pair({ withFooter }: { withFooter: boolean }) {
      return <><Bar h={81} />{withFooter && <Bar h={120} />}</>;
    }
    const { rerender } = render(<Pair withFooter />);
    expect(bottomObstruction()).toBe(120);
    rerender(<Pair withFooter={false} />);
    // The sheet closed; the navigation underneath it is still on screen.
    expect(bottomObstruction()).toBe(81);
  });

  it('a footer that grows republishes its own height', () => {
    // The real trigger is a ResizeObserver; jsdom has none, so the observer is
    // stubbed and fired to prove the wiring, not the browser's implementation.
    const observers: Array<() => void> = [];
    class RO {
      constructor(private cb: () => void) { observers.push(() => this.cb()); }
      observe() {} disconnect() {} unobserve() {}
    }
    vi.stubGlobal('ResizeObserver', RO);

    function Growing({ h }: { h: number }) { return <Bar h={h} />; }
    const { rerender } = render(<Growing h={68} />);
    expect(bottomObstruction()).toBe(68);
    rerender(<Growing h={132} />);          // a second action wraps to two rows
    act(() => { observers.forEach((fire) => fire()); });
    expect(bottomObstruction()).toBe(132);
    vi.unstubAllGlobals();
  });

  it('a surface hidden for the soft keyboard stops obstructing', () => {
    function Nav({ keyboardOpen }: { keyboardOpen: boolean }) {
      return <><Bar h={81} active={!keyboardOpen} /><Bar h={68} /></>;
    }
    const { rerender } = render(<Nav keyboardOpen={false} />);
    expect(bottomObstruction()).toBe(81);
    rerender(<Nav keyboardOpen />);
    // The nav is gone; the sheet's footer is not, and still counts.
    expect(bottomObstruction()).toBe(68);
    rerender(<Nav keyboardOpen={false} />);
    expect(bottomObstruction()).toBe(81);
  });

  it('re-measures after the frame in which it mounted', async () => {
    // A surface can mount while the viewport is still settling — an orientation
    // change, a safe-area inset applying, Telegram resizing its chrome. Caught
    // in a real browser: the nav mounted during a 1280→375 transition, measured
    // against the old height, and because its OWN box never changed afterwards
    // no ResizeObserver fired to correct it. The band stayed 15px short.
    const frames: FrameRequestCallback[] = [];
    vi.stubGlobal('requestAnimationFrame', (cb: FrameRequestCallback) => { frames.push(cb); return 1; });
    vi.stubGlobal('cancelAnimationFrame', () => {});

    // Mount happens mid-transition: the bar is laid out for the viewport it is
    // about to have, so measured against the current one it reaches nothing.
    Object.defineProperty(window, 'innerHeight', { value: 700, configurable: true });
    render(<Bar h={81} />);
    expect(bottomObstruction()).toBe(0);          // the stale, wrong answer

    // The layout settles one frame later. Nothing about the ELEMENT changed, so
    // only the scheduled re-measure can notice.
    Object.defineProperty(window, 'innerHeight', { value: VIEWPORT_H, configurable: true });
    act(() => { frames.forEach((f) => f(0)); });
    expect(bottomObstruction()).toBe(81);
    vi.unstubAllGlobals();
  });

  it('leaves nothing behind when everything unmounts', () => {
    const { unmount } = render(<><Bar h={81} /><Bar h={68} /></>);
    unmount();
    expect(bottomObstruction()).toBe(0);
    expect(document.documentElement.style.getPropertyValue('--app-bottom-obstruction'))
      .toBe('');
  });
});
