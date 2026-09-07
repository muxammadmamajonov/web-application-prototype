/**
 * How much of the bottom of the screen is currently covered.
 *
 * Several surfaces can be pinned to the bottom edge at once — the mobile
 * navigation, a bottom-sheet's sticky action footer, a client payment bar — and
 * a floating panel has no way to see any of them. Each surface therefore
 * measures itself and reports its coverage here; the panel asks this module how
 * much room is left.
 *
 * WHY A REGISTRY AND NOT ONE VARIABLE. With a single shared value, whichever
 * effect happened to run last won, and a surface unmounting cleared a
 * measurement that still belonged to another one. Every publisher now holds its
 * own token: it can only ever overwrite or release its own entry.
 *
 * WHY MAX AND NOT SUM. A contribution is "pixels of the visual viewport's
 * bottom edge that this surface covers", measured from the bottom upwards.
 * Bottom-pinned surfaces stack in z, not vertically, so their bands overlap
 * from the same edge — the deepest one governs. Adding them would reserve space
 * twice for the same pixels.
 *
 * The effective value is mirrored onto `--app-bottom-obstruction` so stylesheets
 * can use it too, but the registry is the source of truth.
 */

const VAR = '--app-bottom-obstruction';

/** Sub-pixel slack when asking whether a surface reaches the bottom edge. */
const REACH_TOLERANCE = 1;

export type ObstructionToken = symbol;

const entries = new Map<ObstructionToken, number>();
const listeners = new Set<() => void>();
let effective = 0;

/** The deepest band any active surface covers, in CSS pixels. */
export function bottomObstruction(): number {
  return effective;
}

function recompute() {
  let next = 0;
  for (const px of entries.values()) if (px > next) next = px;
  if (next === effective) return;
  effective = next;
  if (typeof document !== 'undefined') {
    const root = document.documentElement;
    if (next > 0) root.style.setProperty(VAR, `${Math.round(next)}px`);
    else root.style.removeProperty(VAR);
  }
  // A panel positioned against the old value is now wrong; it re-measures here
  // rather than waiting for the next scroll or resize that may never come.
  for (const fn of listeners) fn();
}

export function claimBottomObstruction(): ObstructionToken {
  const token = Symbol('bottom-obstruction');
  entries.set(token, 0);
  return token;
}

/** Report this surface's own coverage. Never touches another surface's entry. */
export function reportBottomObstruction(token: ObstructionToken, px: number) {
  if (!entries.has(token)) return;
  const next = Number.isFinite(px) && px > 0 ? px : 0;
  if (entries.get(token) === next) return;
  entries.set(token, next);
  recompute();
}

/** Give up this surface's claim. Whatever else is mounted keeps its own. */
export function releaseBottomObstruction(token: ObstructionToken) {
  if (entries.delete(token)) recompute();
}

export function onBottomObstructionChange(fn: () => void): () => void {
  listeners.add(fn);
  return () => { listeners.delete(fn); };
}

/**
 * How much of the visual viewport's bottom edge this element covers.
 *
 * Measured, never assumed, so the same code serves a 4-inch phone and a desktop
 * dialog. Two cases it has to get right:
 *
 *   - a surface that floats above the bottom edge (a CENTRED desktop dialog's
 *     footer) covers nothing there and contributes zero;
 *   - a fixed surface sits with the LAYOUT viewport, so when the soft keyboard
 *     shrinks the visual viewport the surface is partly below it. Only the
 *     overlapping part counts.
 */
export function bottomCoverage(el: Element | null | undefined): number {
  if (!el || typeof window === 'undefined') return 0;
  const rect = el.getBoundingClientRect();
  if (rect.height <= 0 || rect.width <= 0) return 0;

  const vv = window.visualViewport;
  const viewTop = vv?.offsetTop ?? 0;
  const viewHeight = vv?.height ?? window.innerHeight;
  const viewBottom = viewTop + viewHeight;

  if (rect.bottom < viewBottom - REACH_TOLERANCE) return 0;
  const covered = viewBottom - Math.max(rect.top, viewTop);
  return Math.max(0, Math.min(covered, viewHeight));
}

/** Test seam: drop every claim. Not used by application code. */
export function resetBottomObstruction() {
  entries.clear();
  listeners.clear();
  effective = 0;
  if (typeof document !== 'undefined') document.documentElement.style.removeProperty(VAR);
}
