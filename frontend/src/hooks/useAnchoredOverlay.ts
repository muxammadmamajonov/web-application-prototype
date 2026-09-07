import { useCallback, useEffect, useLayoutEffect, useRef, useState } from 'react';
import { bottomObstruction, onBottomObstructionChange } from '@/lib/bottomObstruction';

/**
 * Place a floating panel next to its trigger, inside the space that is actually
 * usable — never behind the bottom navigation, a sticky footer, or a soft
 * keyboard.
 *
 * An absolutely-positioned popover is laid out against its scroll container and
 * knows nothing about what is painted over the bottom of the screen. On a short
 * phone that puts the lower rows of a calendar past the fold and underneath the
 * fixed nav, where `elementFromPoint` returns null and a tap cannot reach them.
 *
 * The usable BAND is measured, not assumed:
 *
 *   top    — the visual viewport's offset, so a pinch-zoomed or scrolled
 *            Telegram WebView is handled by the same arithmetic;
 *   bottom — the visual viewport's height (which SHRINKS when the soft keyboard
 *            opens) minus whatever bottom-anchored surfaces have measured
 *            themselves as covering. Nothing here hard-codes a device or a nav.
 *
 * THE BAND IS A HARD CEILING. A preferred height decides which SIDE to use; it
 * never licenses a panel taller than the room that exists. When neither side
 * can hold a usable panel — a keyboard-shrunk viewport, a field that fills what
 * is left of the screen — the panel takes the whole band and scrolls inside it.
 * A panel that overlaps its own trigger is recoverable; one placed off-screen
 * is not.
 */

export type OverlaySide = 'top' | 'bottom';

export interface OverlayPosition {
  /** `position: fixed` coordinates, in visual-viewport pixels. */
  left: number;
  top: number;
  maxHeight: number;
  width: number;
  side: OverlaySide;
  /**
   * Set for a panel placed ABOVE its trigger, where `top` is the trigger's own
   * edge and the panel is lifted by its own height. That keeps it hugging the
   * field whatever its content measures, without a second layout pass — and
   * because `maxHeight` is the gap, lifting can never carry it out of the band.
   */
  transform?: string;
}

/** Breathing room between the trigger and the panel, and at the band's edges. */
const GAP = 8;
const EDGE = 8;
/** Below this a panel is too cramped to work in; prefer the other side. */
const MIN_USABLE_HEIGHT = 180;
/** Roughly a whole month grid with its header. Above this there is nothing to
 *  gain from the other side, so the reading direction wins. */
const PREFERRED_HEIGHT = 360;

const clamp = (n: number, lo: number, hi: number) => Math.min(Math.max(n, lo), hi);

export function useAnchoredOverlay(open: boolean, preferredWidth: number) {
  const triggerRef = useRef<HTMLElement | null>(null);
  const [pos, setPos] = useState<OverlayPosition | null>(null);

  const measure = useCallback(() => {
    const el = triggerRef.current;
    if (!el) return;
    const rect = el.getBoundingClientRect();
    const vv = window.visualViewport;
    // The VISUAL viewport, not the layout one: it is what shrinks when the soft
    // keyboard opens, and what Telegram changes as its chrome moves.
    const viewTop = vv?.offsetTop ?? 0;
    const viewLeft = vv?.offsetLeft ?? 0;
    const viewWidth = vv?.width ?? window.innerWidth;
    const viewHeight = vv?.height ?? window.innerHeight;

    const bandTop = viewTop + EDGE;
    const bandBottom = viewTop + viewHeight - bottomObstruction() - EDGE;
    const band = Math.max(0, bandBottom - bandTop);

    // Gaps are clamped into the band before anything else looks at them, so a
    // trigger scrolled off either end cannot produce a negative measurement.
    const below = clamp(bandBottom - rect.bottom - GAP, 0, band);
    const above = clamp(rect.top - GAP - bandTop, 0, band);

    // Below is the reading direction, so it wins when it can show the whole
    // panel or when it is simply the larger gap. It does NOT win merely by
    // being usable: 191px below against 572px above is a calendar the person
    // has to scroll for no reason.
    let side: OverlaySide = below >= PREFERRED_HEIGHT || below >= above ? 'bottom' : 'top';
    let maxHeight = Math.floor(side === 'bottom' ? below : above);
    let top: number;
    let transform: string | undefined;

    if (maxHeight < MIN_USABLE_HEIGHT) {
      // Neither side can hold a usable panel. Take the band itself — still
      // inside the viewport, still clear of the obstruction, scrolling
      // internally — rather than a sliver, or an overflow off the screen.
      side = 'bottom';
      maxHeight = Math.floor(band);
      top = bandTop;
    } else if (side === 'bottom') {
      top = rect.bottom + GAP;
    } else {
      top = rect.top - GAP;
      transform = 'translateY(-100%)';
    }

    const width = Math.max(0, Math.min(preferredWidth, viewWidth - EDGE * 2));
    const left = Math.max(
      viewLeft + EDGE,
      Math.min(rect.left, viewLeft + viewWidth - width - EDGE),
    );
    // Final guard: whatever the branch decided, the panel's own box stays in
    // the band. For the lifted case `top` is the trigger edge, and the lift is
    // bounded by `maxHeight`, which is the gap above it.
    top = transform
      ? clamp(top, bandTop + maxHeight, bandBottom)
      : clamp(top, bandTop, Math.max(bandTop, bandBottom - maxHeight));

    setPos({ left, top, maxHeight, width, side, transform });
  }, [preferredWidth]);

  // Before paint, so the panel never appears in the wrong place for a frame.
  useLayoutEffect(() => {
    if (!open) { setPos(null); return; }
    measure();
  }, [open, measure]);

  useEffect(() => {
    if (!open) return;
    const vv = window.visualViewport;
    // `scroll` is captured: the trigger may sit inside a scrolling modal body,
    // and a fixed panel does not move with it on its own.
    window.addEventListener('scroll', measure, true);
    window.addEventListener('resize', measure);
    vv?.addEventListener('resize', measure);
    vv?.addEventListener('scroll', measure);
    // A footer that grows, or a nav that appears, changes the band under a
    // panel that is already open.
    const offObstruction = onBottomObstructionChange(measure);
    return () => {
      window.removeEventListener('scroll', measure, true);
      window.removeEventListener('resize', measure);
      vv?.removeEventListener('resize', measure);
      vv?.removeEventListener('scroll', measure);
      offObstruction();
    };
  }, [open, measure]);

  return { triggerRef, pos, remeasure: measure };
}
