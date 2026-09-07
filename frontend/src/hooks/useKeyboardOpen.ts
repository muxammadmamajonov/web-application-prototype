import { useEffect, useState } from 'react';

/**
 * True while the on-screen keyboard is open on mobile. Detected via the
 * VisualViewport API: when the soft keyboard appears the visual viewport
 * shrinks well below the layout viewport. We use this to hide the fixed
 * bottom navigation, which otherwise floats up over the content (iOS) or
 * sits awkwardly above the keyboard while typing.
 */
export function useKeyboardOpen(thresholdPx = 140): boolean {
  const [open, setOpen] = useState(false);

  useEffect(() => {
    const vv = window.visualViewport;
    if (!vv) return;
    const onResize = () => {
      const hidden = window.innerHeight - vv.height;
      setOpen(hidden > thresholdPx);
    };
    onResize();
    vv.addEventListener('resize', onResize);
    return () => vv.removeEventListener('resize', onResize);
  }, [thresholdPx]);

  return open;
}
