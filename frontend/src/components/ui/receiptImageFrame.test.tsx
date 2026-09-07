// @vitest-environment jsdom
/**
 * LX-02 / LX-07 — a receipt image must not be able to move anything.
 *
 * Four surfaces rendered a receipt as a bare `<img>` with a `max-h-*` cap and
 * no intrinsic dimensions. Such an image occupies ~0 height until it decodes
 * and then expands to as much as 60vh, so everything below it is displaced at
 * an unpredictable moment. On `tenant/PaymentReviewPage` the things below it
 * are Approve and Reject, which commit money on a screen reached from a chat
 * notification — the file's own comment says "a mis-tap is the expensive kind".
 *
 * WHAT THIS FILE CAN AND CANNOT PROVE. jsdom has no layout engine: it computes
 * no boxes and resolves no `clamp()`. These are DOM/CSS-CONTRACT tests, not
 * pixel measurements. They pin the four facts a layout engine would combine:
 *
 *   1. one persistent outer node exists from first render;
 *   2. that node carries an unconditional height (not a `min-height` the image
 *      could exceed);
 *   3. the height does not vary with loading / loaded / failed; and
 *   4. the `<img>` is absolutely positioned, so it cannot contribute intrinsic
 *      height to the document no matter what it decodes to.
 *
 * Real rectangles at 320/360/375/390/1280 are measured in a browser and
 * recorded in §20 of the audit; this file is the regression net between runs.
 *
 * WHY FAILURE IS NOT HYPOTHETICAL. `RECEIPT_FILE` accepts `.pdf`, the upload
 * path stores whatever content type arrives, and `/api/receipts/{id}` serves it
 * back with that type — so a customer who uploads a PDF receipt produces a URL
 * that `<img>` can never render. The failure state is reachable in production
 * today, which is why it must keep the frame's geometry and say something
 * truthful rather than showing a broken-image glyph.
 */
import { describe, expect, it, beforeEach, afterEach } from 'vitest';
import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import i18n from '@/i18n';
import { ReceiptImageFrame, RECEIPT_FRAME_HEIGHT_CLASS } from './ReceiptImageFrame';

const SRC = '/api/receipts/blob-1?s=sig';
const ALT = 'Yuborilgan chek';

const frame = () => screen.getByTestId('receipt-frame');
const img = () => frame().querySelector('img') as HTMLImageElement;

/**
 * The reserved-height contract, read the way jsdom can read it.
 *
 * jsdom's CSSOM REJECTS `clamp()`: assigning it to `style.height` leaves the
 * property `''` and writes no style attribute at all. An inline contract would
 * therefore be invisible here — the assertion would pass against nothing. As a
 * Tailwind arbitrary value it is a literal string on the node.
 */
const height = (el: Element) => el.className.split(/\s+/).find((c) => c.startsWith('h-[')) ?? '';

beforeEach(async () => { await i18n.changeLanguage('uz'); });
afterEach(cleanup);

describe('the reserved box', () => {
  it('exists before the image has loaded', () => {
    render(<ReceiptImageFrame src={SRC} alt={ALT} />);
    expect(frame()).toBeTruthy();
    expect(height(frame())).toBe(RECEIPT_FRAME_HEIGHT_CLASS);
  });

  it('is a fixed height, not a floor the image can grow past', () => {
    render(<ReceiptImageFrame src={SRC} alt={ALT} />);
    // `min-height` alone is exactly the defect: the image still expands past it.
    expect(height(frame()), 'the reserved box must set `height`').toBeTruthy();
    expect(frame().className.split(/\s+/).some((c) => c.startsWith('min-h-')),
      'a min-height would let a tall receipt push the controls anyway').toBe(false);
    expect(frame().style.minHeight).toBe('');
    // Bounded at both ends: a floor for a short viewport, a ceiling so a tall
    // desktop window does not hand 60vh to one photograph.
    expect(RECEIPT_FRAME_HEIGHT_CLASS).toMatch(/^h-\[clamp\([^,]+,[^,]+,[^)]+\)\]$/);
  });

  it('keeps one and the same node through loading → loaded', () => {
    render(<ReceiptImageFrame src={SRC} alt={ALT} />);
    const node = frame();
    const before = height(node);

    fireEvent.load(img());

    expect(screen.getAllByTestId('receipt-frame')).toHaveLength(1);
    expect(frame(), 'the frame was remounted, so its box was rebuilt').toBe(node);
    expect(height(frame())).toBe(before);
  });

  it('keeps one and the same node through loading → failed', () => {
    render(<ReceiptImageFrame src={SRC} alt={ALT} />);
    const node = frame();
    const before = height(node);

    fireEvent.error(img());

    expect(screen.getAllByTestId('receipt-frame')).toHaveLength(1);
    expect(frame()).toBe(node);
    expect(height(frame())).toBe(before);
  });

  it('the height contract does not read the loading state at all', () => {
    render(<ReceiptImageFrame src={SRC} alt={ALT} />);
    const heights = [height(frame())];
    fireEvent.load(img());
    heights.push(height(frame()));

    cleanup();
    render(<ReceiptImageFrame src={SRC} alt={ALT} />);
    fireEvent.error(img());
    heights.push(height(frame()));

    expect(new Set(heights).size, `height varied across states: ${heights.join(' | ')}`).toBe(1);
  });

  it('takes the image out of the document flow so it cannot add height', () => {
    render(<ReceiptImageFrame src={SRC} alt={ALT} />);
    const cls = img().className.split(/\s+/);
    // Absolutely positioned inside the reserved box: whatever the natural size
    // turns out to be, the document height is the box's, full stop.
    expect(cls, 'the image must not participate in intrinsic height').toContain('absolute');
    expect(cls).toContain('inset-0');
    expect(frame().className.split(/\s+/)).toContain('relative');
  });

  it('never crops the evidence and never distorts it', () => {
    render(<ReceiptImageFrame src={SRC} alt={ALT} />);
    const cls = img().className.split(/\s+/);
    expect(cls, 'a receipt must never be cropped').toContain('object-contain');
    expect(cls).not.toContain('object-cover');
    expect(cls).not.toContain('object-fill');
  });

  it('cannot overflow horizontally', () => {
    render(<ReceiptImageFrame src={SRC} alt={ALT} />);
    const cls = frame().className.split(/\s+/);
    expect(cls).toContain('w-full');
    expect(cls).toContain('overflow-hidden');
    expect(cls.some((c) => c.startsWith('overflow-x-')),
      'the frame must not introduce a horizontal scroller').toBe(false);
    expect(frame().style.width, 'a fixed px width would overflow a 320px viewport').toBe('');
  });
});

describe('loading and failure', () => {
  it('announces loading politely, scoped to the image', () => {
    render(<ReceiptImageFrame src={SRC} alt={ALT} />);
    const status = frame().querySelector('[role="status"]');
    expect(status, 'the loading state is not announced').toBeTruthy();
    expect(status?.getAttribute('aria-live')).toBe('polite');
    expect(status?.textContent).toContain(i18n.t('receipt.imageLoading') as string);
    // Scoped: the region lives inside the frame, so a receipt decoding does not
    // re-announce the page or the modal around it.
    expect(frame().contains(status!)).toBe(true);
  });

  it('hides the image until it has actually loaded, then shows it', () => {
    render(<ReceiptImageFrame src={SRC} alt={ALT} />);
    expect(img().className).toContain('invisible');

    fireEvent.load(img());

    expect(img().className).not.toContain('invisible');
    expect(frame().textContent).not.toContain(i18n.t('receipt.imageLoading') as string);
  });

  it('replaces the browser broken-image glyph with localized text', () => {
    render(<ReceiptImageFrame src={SRC} alt={ALT} />);
    fireEvent.error(img());

    expect(screen.getByText(i18n.t('receipt.imageFailed') as string)).toBeTruthy();
    // The <img> stays hidden, so no broken-image icon shows through.
    expect(img().className).toContain('invisible');
  });

  it('does not blame the network for what may be a decode or access failure', () => {
    render(<ReceiptImageFrame src={SRC} alt={ALT} />);
    fireEvent.error(img());
    const text = frame().textContent ?? '';
    // A PDF receipt reaches this state with a perfectly healthy network.
    expect(text).not.toMatch(/internet|интернет|tarmoq|сет[ьи]|network|offline/i);
  });

  it('does not auto-retry after a failure', () => {
    render(<ReceiptImageFrame src={SRC} alt={ALT} />);
    const before = img().getAttribute('src');
    fireEvent.error(img());
    // Same src, no cache-buster, no second request scheduled.
    expect(img().getAttribute('src')).toBe(before);
  });

  it('shows no percentage and no progress bar', () => {
    render(<ReceiptImageFrame src={SRC} alt={ALT} />);
    expect(frame().textContent ?? '').not.toMatch(/\d\s*%/);
    expect(frame().querySelector('progress,[role="progressbar"]')).toBeNull();
  });

  it('can stay silent for a local preview that decodes instantly', () => {
    render(<ReceiptImageFrame src="blob:local" alt={ALT} announceLoading={false} />);
    const status = frame().querySelector('[role="status"]');
    // The region still exists (so a later failure is announced) but says nothing
    // about routine local decoding.
    expect(status?.textContent?.trim() ?? '').toBe('');
    expect(height(frame()), 'silence must not cost the reserved box').toBe(RECEIPT_FRAME_HEIGHT_CLASS);

    fireEvent.error(img());
    expect(screen.getByText(i18n.t('receipt.imageFailed') as string)).toBeTruthy();
  });
});

describe('a replaced source', () => {
  it('resets to loading when src changes after a success', () => {
    const { rerender } = render(<ReceiptImageFrame src="blob:a" alt={ALT} />);
    fireEvent.load(img());
    expect(img().className).not.toContain('invisible');

    rerender(<ReceiptImageFrame src="blob:b" alt={ALT} />);

    // Stale "loaded" here would show the NEW image's box holding the OLD
    // image's state — the second receipt appears already decoded.
    expect(img().className).toContain('invisible');
    expect(frame().textContent).toContain(i18n.t('receipt.imageLoading') as string);
  });

  it('resets to loading when src changes after a failure', () => {
    const { rerender } = render(<ReceiptImageFrame src="blob:a" alt={ALT} />);
    fireEvent.error(img());
    expect(screen.getByText(i18n.t('receipt.imageFailed') as string)).toBeTruthy();

    rerender(<ReceiptImageFrame src="blob:b" alt={ALT} />);

    expect(screen.queryByText(i18n.t('receipt.imageFailed') as string)).toBeNull();
    expect(height(frame())).toBe(RECEIPT_FRAME_HEIGHT_CLASS);
  });

  it('keeps the reserved box across the swap', () => {
    const { rerender } = render(<ReceiptImageFrame src="blob:a" alt={ALT} />);
    const node = frame();
    rerender(<ReceiptImageFrame src="blob:b" alt={ALT} />);
    expect(frame()).toBe(node);
    expect(height(frame())).toBe(RECEIPT_FRAME_HEIGHT_CLASS);
  });
});

describe('alt text', () => {
  it('carries the caller\'s description for evidence images', () => {
    render(<ReceiptImageFrame src={SRC} alt={ALT} />);
    expect(img().getAttribute('alt')).toBe(ALT);
  });

  it('accepts an empty alt for a decorative preview', () => {
    render(<ReceiptImageFrame src={SRC} alt="" />);
    expect(img().getAttribute('alt')).toBe('');
  });
});

describe.each(['uz', 'ru'] as const)('localized in %s', (lng) => {
  beforeEach(async () => { await i18n.changeLanguage(lng); });

  it('renders both states from the locale bundle, not a hardcoded fallback', () => {
    render(<ReceiptImageFrame src={SRC} alt={ALT} />);
    const loading = i18n.getResource(lng, 'translation', 'receipt.imageLoading') as string;
    const failed = i18n.getResource(lng, 'translation', 'receipt.imageFailed') as string;
    expect(loading, `receipt.imageLoading missing from ${lng}.json`).toBeTruthy();
    expect(failed, `receipt.imageFailed missing from ${lng}.json`).toBeTruthy();

    expect(frame().textContent).toContain(loading);
    fireEvent.error(img());
    expect(frame().textContent).toContain(failed);
  });
});
