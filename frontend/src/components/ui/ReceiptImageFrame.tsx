import { useEffect, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { ImageOff, Loader2 } from 'lucide-react';
import { cn } from '@/lib/cn';

/**
 * The reserved box every receipt image renders into.
 *
 * THE DEFECT THIS EXISTS FOR (LX-02/LX-07). Four surfaces rendered a receipt as
 * a bare `<img>` with a `max-h-*` cap and no intrinsic dimensions. Such an image
 * occupies almost no height until it decodes and then expands to as much as
 * 60vh, so everything below it jumps at a moment nobody controls. On
 * `PaymentReviewPage` the things below it are Approve and Reject — a money
 * decision on a screen opened from a chat notification.
 *
 * WHY A FIXED HEIGHT AND NOT `min-height`, AND NOT AN ASPECT BOX.
 *
 *   • `min-height` is not a fix: a tall receipt still grows past the floor and
 *     still moves the buttons. It only makes the jump smaller.
 *   • An aspect box needs intrinsic dimensions. The API does not carry them —
 *     `receipt_image_url` is an opaque signed capability URL and the proxy
 *     returns bytes, not metadata — so any ratio here would be invented, and an
 *     invented ratio combined with a `max-height` cap breaks the reservation at
 *     whatever viewport makes the cap bite.
 *   • A single bounded height depends only on the viewport, never on the image,
 *     so it is by construction identical before and after decode.
 *
 * The image is `absolute inset-0`: out of flow entirely, so whatever it decodes
 * to, the document height is this box's and nothing else's. `object-contain`
 * keeps the whole receipt visible and undistorted — evidence must never be
 * cropped.
 *
 * WHY FAILURE IS A REAL STATE, NOT A COURTESY. `RECEIPT_FILE` accepts `.pdf`,
 * the upload path stores the content type it is given, and `/api/receipts/{id}`
 * serves it back with that type. A customer who uploads a PDF receipt therefore
 * produces a URL that no `<img>` can ever render. That is reachable in
 * production today, on a perfectly healthy network — which is why the failure
 * copy names no cause, and why the two drawers keep their "Open" link.
 */

/**
 * The reserved height, as one bounded expression shared by all four surfaces.
 *
 * Chosen from the geometry the surfaces already had, not guessed: the review
 * screen capped at `60vh`, the two drawers at `50vh`, the upload preview at
 * `420px`.
 *
 *   • 56vh sits between the two viewport caps it replaces;
 *   • the 240px floor keeps a receipt legible on a 320x568 phone, where 56vh is
 *     318px, rather than collapsing on a short viewport;
 *   • the 480px ceiling stops a tall desktop window from handing 60vh (up to
 *     ~800px) to one photograph, and keeps the box inside the modal shell's
 *     `max-h-[90vh]` body with its header and sticky footer.
 *
 * A Tailwind arbitrary value, not an inline `style`: jsdom's CSSOM rejects
 * `clamp()` outright — assigning it leaves `style.height` as `''` and writes no
 * attribute at all — so an inline contract would be invisible to the very tests
 * that have to defend it. As a class it is real CSS in the browser and a
 * readable string in jsdom, which is also how the rest of this repo pins
 * geometry it cannot measure (see `paymentTouchTargets.test.tsx`).
 */
export const RECEIPT_FRAME_HEIGHT_CLASS = 'h-[clamp(240px,56vh,480px)]';

export function ReceiptImageFrame({ src, alt, className, announceLoading = true }: {
  readonly src: string;
  /** Describe the evidence. `''` only where the same information sits beside it. */
  readonly alt: string;
  readonly className?: string;
  /**
   * `false` for a local object URL, which decodes in a frame or two — narrating
   * that to a screen reader is noise, not information. Failure is still
   * announced either way.
   */
  readonly announceLoading?: boolean;
}) {
  const { t } = useTranslation();

  // Keyed to `src`, not a bare boolean. When the customer replaces a chosen
  // photo the URL changes, and derived state resets itself — there is no effect
  // that can forget to, and no window where the new image wears the old one's
  // "loaded" or "failed".
  const [loadedSrc, setLoadedSrc] = useState<string | null>(null);
  const [failedSrc, setFailedSrc] = useState<string | null>(null);
  const state = failedSrc === src ? 'failed' : loadedSrc === src ? 'loaded' : 'loading';

  const imgRef = useRef<HTMLImageElement>(null);
  useEffect(() => {
    // A cached image can finish before React attaches its handlers, and then
    // neither `load` nor `error` ever fires — the placeholder would sit forever
    // on top of a perfectly decoded receipt. `complete` is the only way to see
    // that from here.
    const el = imgRef.current;
    if (!el || !el.complete) return;
    if (el.naturalWidth > 0) setLoadedSrc(src);
    else setFailedSrc(src);
  }, [src]);

  const message = state === 'failed'
    ? t('receipt.imageFailed', 'Chek rasmini koʻrsatib boʻlmadi')
    : state === 'loading' && announceLoading
      ? t('receipt.imageLoading', 'Chek rasmi yuklanmoqda…')
      : '';

  return (
    <div
      data-testid="receipt-frame"
      // `relative` anchors the image and the overlay; `overflow-hidden` means
      // nothing inside can widen the page on a 320px screen.
      className={cn(
        'relative w-full overflow-hidden rounded-card border border-line bg-cream',
        RECEIPT_FRAME_HEIGHT_CLASS,
        className,
      )}
    >
      <img
        ref={imgRef}
        src={src}
        alt={alt}
        onLoad={() => setLoadedSrc(src)}
        onError={() => setFailedSrc(src)}
        // `invisible` rather than unmounted: visibility:hidden also suppresses
        // the browser's broken-image glyph, and keeping the node mounted means
        // a failure does not silently re-request the bytes.
        className={cn(
          'absolute inset-0 h-full w-full object-contain',
          state !== 'loaded' && 'invisible',
        )}
      />
      {/*
        Scoped to this image. A live region around the page or the modal would
        re-announce the whole surface every time a receipt decoded. Present from
        first paint and empty when there is nothing to say, because a region
        inserted at the moment of the change is not reliably announced.
        `pointer-events-none` keeps it off any control layered over the frame.
      */}
      <div
        role="status"
        aria-live="polite"
        className="pointer-events-none absolute inset-0 grid place-content-center justify-items-center gap-2 px-4 text-center text-caption text-muted"
      >
        {state === 'loading' && <Loader2 size={18} className="animate-spin" aria-hidden="true" />}
        {state === 'failed' && <ImageOff size={20} aria-hidden="true" />}
        {message && <span>{message}</span>}
      </div>
    </div>
  );
}
