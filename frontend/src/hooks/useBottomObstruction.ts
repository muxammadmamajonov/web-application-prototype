import { useLayoutEffect, useRef } from 'react';
import {
  bottomCoverage, claimBottomObstruction,
  releaseBottomObstruction, reportBottomObstruction,
} from '@/lib/bottomObstruction';

/**
 * Declare that this element covers the bottom of the screen.
 *
 * A surface pinned to the bottom — the mobile navigation, a sheet's sticky
 * action footer — paints over anything a floating panel puts there. The panel
 * cannot see it, so the surface says so itself.
 *
 * Each caller holds its own token, so two bottom surfaces mounted at once both
 * count and neither erases the other on the way out. Nothing here knows a
 * device or a height: the coverage is measured from the live element against
 * the visual viewport.
 *
 * `active` is for a surface that is mounted but not covering anything — the nav
 * hidden for the soft keyboard, a dialog that is closed.
 */
export function useBottomObstruction<T extends HTMLElement>(active = true) {
  const ref = useRef<T | null>(null);

  useLayoutEffect(() => {
    if (!active) return;
    const token = claimBottomObstruction();
    const publish = () => reportBottomObstruction(token, bottomCoverage(ref.current));

    publish();
    // Coverage depends on the VIEWPORT as much as on the element, and a surface
    // can mount while the viewport is still settling — an orientation change, a
    // safe-area inset applying, Telegram resizing its chrome. The first
    // measurement then races the layout, and because the element's own box
    // never changes afterwards no observer fires to correct it. One re-measure
    // after the next frame closes that window.
    const frame = typeof requestAnimationFrame === 'function'
      ? requestAnimationFrame(publish) : null;
    // The band also moves with the safe-area inset and the keyboard, so it is
    // observed rather than measured once.
    const ro = typeof ResizeObserver !== 'undefined' ? new ResizeObserver(publish) : null;
    if (ro && ref.current) ro.observe(ref.current);
    const vv = window.visualViewport;
    window.addEventListener('resize', publish);
    vv?.addEventListener('resize', publish);
    vv?.addEventListener('scroll', publish);
    return () => {
      if (frame !== null) cancelAnimationFrame(frame);
      ro?.disconnect();
      window.removeEventListener('resize', publish);
      vv?.removeEventListener('resize', publish);
      vv?.removeEventListener('scroll', publish);
      releaseBottomObstruction(token);
    };
  }, [active]);

  return ref;
}
