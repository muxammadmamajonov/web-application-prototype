import { useEffect, useRef } from 'react';
import { getRawTelegramWebApp } from '@/lib/telegram';

/**
 * Coordinator for Telegram's NATIVE back button (top-left; replaces the "Close"
 * pill while shown). The native BackButton is a SINGLETON, but several things may
 * want it at once — a drill-down route, the Menu sheet opened on top of it, a
 * settings detail pane. So handlers form a STACK: the most-recently-pushed owns
 * the button; popping it restores the one beneath. This is what lets every
 * in-app back affordance (overlays included) become the native Back, with no two
 * owners fighting over show/hide.
 *
 * Outside Telegram every push is an inert no-op, so callers stay unconditional.
 */
type BackHandler = () => void;

const stack: BackHandler[] = [];
let bound: BackHandler | null = null;

function sync(): void {
  const bb = getRawTelegramWebApp()?.BackButton;
  if (!bb) return;
  if (bound) {
    bb.offClick(bound);
    bound = null;
  }
  if (stack.length > 0) {
    // Re-read the top of the stack on each press (handlers can change).
    bound = () => stack[stack.length - 1]?.();
    bb.onClick(bound);
    bb.show();
  } else {
    bb.hide();
  }
}

/** Push a back handler; the topmost handler drives the native Back. Returns an
 *  unregister fn that pops it and restores the previous owner. */
export function pushBackHandler(handler: BackHandler): () => void {
  stack.push(handler);
  sync();
  return () => {
    const i = stack.lastIndexOf(handler);
    if (i >= 0) stack.splice(i, 1);
    sync();
  };
}

/**
 * Own the native Back while `active`. The latest `onBack` is read through a ref,
 * so the registered handler never goes stale and we never re-push on every
 * render. Use for route subpages (MiniAppHeader), the Menu sheet, settings
 * detail, wizard steps — anywhere an in-app back belongs.
 */
export function useTelegramBackButton(active: boolean, onBack: BackHandler): void {
  const onBackRef = useRef(onBack);
  onBackRef.current = onBack;

  useEffect(() => {
    if (!active) return;
    return pushBackHandler(() => onBackRef.current());
  }, [active]);
}
