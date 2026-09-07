import { useEffect, useRef, type RefObject } from 'react';

const FOCUSABLE =
  'a[href],button:not([disabled]),textarea:not([disabled]),input:not([disabled]),select:not([disabled]),[tabindex]:not([tabindex="-1"])';

/** Dialog a11y bundle (#161): body-scroll lock, Esc-to-close, Tab focus-trap,
 *  initial focus into the panel, and focus-RETURN to the previously-focused
 *  element on close. Call it unconditionally (it no-ops while `open` is false),
 *  and attach `panelRef` to the dialog panel. */
export function useFocusTrap(
  panelRef: RefObject<HTMLElement | null>,
  open: boolean,
  onClose: () => void,
): void {
  // Keep the latest onClose without making it an effect dep — an inline-arrow
  // onClose (the common case) would otherwise re-run the effect every render
  // and steal focus from whatever is being typed.
  const onCloseRef = useRef(onClose);
  onCloseRef.current = onClose;

  useEffect(() => {
    if (!open) return;
    const restoreTo = document.activeElement as HTMLElement | null;
    const prevOverflow = document.body.style.overflow;
    document.body.style.overflow = 'hidden';

    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') { onCloseRef.current(); return; }
      if (e.key !== 'Tab') return;
      const nodes = panelRef.current?.querySelectorAll<HTMLElement>(FOCUSABLE);
      if (!nodes || nodes.length === 0) return;
      const first = nodes[0]!;
      const last = nodes[nodes.length - 1]!;
      const active = document.activeElement;
      if (e.shiftKey && active === first) { e.preventDefault(); last.focus(); }
      else if (!e.shiftKey && active === last) { e.preventDefault(); first.focus(); }
    };
    window.addEventListener('keydown', onKey);
    // Move focus into the dialog on open (after paint).
    const id = window.setTimeout(() => {
      panelRef.current?.querySelector<HTMLElement>(FOCUSABLE)?.focus();
    }, 0);

    return () => {
      document.body.style.overflow = prevOverflow;
      window.removeEventListener('keydown', onKey);
      window.clearTimeout(id);
      restoreTo?.focus?.();
    };
  }, [open, panelRef]);
}
