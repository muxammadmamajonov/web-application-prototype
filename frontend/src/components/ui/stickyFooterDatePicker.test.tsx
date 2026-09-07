// @vitest-environment jsdom
/**
 * FB-002 review correction — the surface in the founder's screenshot.
 *
 * The contract wizard is a bottom sheet: on a phone its sticky Back/Next footer
 * IS the bottom of the screen. Batch 2 taught only `MobileBottomNav` to declare
 * itself, so a calendar opened on step 4 cleared a navigation the sheet was
 * already covering and landed underneath the footer that was actually there —
 * the finding was reported closed while the reported surface still failed.
 *
 * This file reproduces that exact arrangement: sheet open, footer visible,
 * field low in the body, calendar opened. jsdom has no layout engine, so the
 * rects are supplied and the arithmetic is what is pinned; the rendered
 * geometry is measured in a browser and recorded in the pass report.
 */
import { describe, expect, it, beforeEach, afterEach, vi } from 'vitest';
import { cleanup, render, fireEvent } from '@testing-library/react';
import i18n from '@/i18n';
import { Modal } from '@/components/ui/Modal';
import { DatePicker } from '@/components/ui/DatePicker';
import { Button } from '@/components/ui/Button';
import { bottomObstruction, resetBottomObstruction } from '@/lib/bottomObstruction';

/** iPhone 12/13/14 logical size — the founder's phone. */
const VIEW_W = 390;
const VIEW_H = 844;
const FOOTER_H = 68;
/** The date field sits low in the sheet body, as it does on the wizard's step. */
const TRIGGER_TOP = 600;

function mockLayout({ footer = FOOTER_H } = {}) {
  vi.spyOn(HTMLElement.prototype, 'getBoundingClientRect').mockImplementation(
    function (this: HTMLElement) {
      const rect = (top: number, height: number, left = 16, width = 358) => ({
        top, bottom: top + height, height, left, right: left + width, width,
        x: left, y: top, toJSON: () => ({}),
      }) as DOMRect;
      // The sheet's action bar, pinned to the bottom edge of the screen.
      if (this.dataset.testid === 'modal-footer') return rect(VIEW_H - footer, footer, 0, VIEW_W);
      if (this.id === 'date') return rect(TRIGGER_TOP, 44);
      return rect(0, 0, 0, 0);
    },
  );
}

function Wizard({ footer = true }: { footer?: boolean }) {
  return (
    <Modal
      open
      title="Shartnoma"
      onClose={() => {}}
      footer={footer ? <Button variant="primary" size="md">Keyingi</Button> : undefined}
    >
      <DatePicker value="" onChange={() => {}} />
    </Modal>
  );
}

const panel = () => document.querySelector('[data-testid="date-picker-panel"]') as HTMLElement;
const trigger = () => document.getElementById('date') as HTMLButtonElement;

/** The panel's painted box: a top-side panel is lifted by its own height. */
function panelBox() {
  const el = panel();
  const maxHeight = Number.parseInt(el.style.maxHeight, 10);
  const declaredTop = Number.parseInt(el.style.top, 10);
  const top = el.style.transform ? declaredTop - maxHeight : declaredTop;
  return { top, bottom: top + maxHeight, maxHeight };
}

beforeEach(async () => {
  await i18n.changeLanguage('uz');
  resetBottomObstruction();
  Object.defineProperty(window, 'innerWidth', { value: VIEW_W, configurable: true });
  Object.defineProperty(window, 'innerHeight', { value: VIEW_H, configurable: true });
  mockLayout();
});
afterEach(() => { cleanup(); vi.restoreAllMocks(); resetBottomObstruction(); });

describe('a calendar inside a sheet with a sticky action footer', () => {
  it('the footer declares its own coverage', () => {
    render(<Wizard />);
    expect(bottomObstruction()).toBe(FOOTER_H);
  });

  it('the calendar clears the footer completely', () => {
    render(<Wizard />);
    fireEvent.click(trigger());
    const box = panelBox();
    const footerTop = VIEW_H - FOOTER_H;
    // Not one pixel of the panel may reach into the action bar: that strip is
    // where taps were being swallowed.
    expect(box.bottom).toBeLessThanOrEqual(footerTop);
    expect(box.top).toBeGreaterThanOrEqual(0);
    expect(box.maxHeight).toBeGreaterThan(0);
  });

  it('the last calendar row is inside the panel, not below it', () => {
    render(<Wizard />);
    fireEvent.click(trigger());
    const el = panel();
    const days = el.querySelectorAll('button[role="gridcell"]');
    expect(days.length).toBeGreaterThan(27);
    // The panel scrolls its own content; nothing escapes into the footer strip.
    expect(el.className).toMatch(/overflow-y-auto/);
    expect(el.style.maxHeight).toMatch(/^\d+px$/);
  });

  it('outranks the sheet it was opened from', () => {
    render(<Wizard />);
    fireEvent.click(trigger());
    // Portalled to <body>, the panel left the dialog's stacking context. A
    // z-index below the sheet's z-80 paints the calendar BEHIND the form that
    // opened it — visible in a browser, invisible to jsdom, so it is asserted.
    const z = (cls: string) => Number(/z-\[(\d+)\]/.exec(cls)?.[1] ?? 0);
    expect(z(panel().className)).toBeGreaterThan(80);
  });

  it('a taller footer pushes the calendar further up', () => {
    vi.restoreAllMocks();
    mockLayout({ footer: 160 });
    render(<Wizard />);
    fireEvent.click(trigger());
    expect(bottomObstruction()).toBe(160);
    expect(panelBox().bottom).toBeLessThanOrEqual(VIEW_H - 160);
  });

  it('a sheet with no footer obstructs nothing', () => {
    render(<Wizard footer={false} />);
    expect(bottomObstruction()).toBe(0);
  });

  it('closing the sheet releases the claim', () => {
    const { unmount } = render(<Wizard />);
    expect(bottomObstruction()).toBe(FOOTER_H);
    unmount();
    expect(bottomObstruction()).toBe(0);
  });

  it('selecting a day still commits and closes here', () => {
    const picked: string[] = [];
    render(
      <Modal open title="Shartnoma" onClose={() => {}} footer={<Button size="md">Keyingi</Button>}>
        <DatePicker value="" onChange={(iso) => picked.push(iso)} />
      </Modal>,
    );
    fireEvent.click(trigger());
    const day = panel().querySelector('button[role="gridcell"]:not([disabled])') as HTMLButtonElement;
    fireEvent.click(day);
    expect(picked).toHaveLength(1);
    expect(document.querySelector('[data-testid="date-picker-panel"]')).toBeNull();
  });
});
