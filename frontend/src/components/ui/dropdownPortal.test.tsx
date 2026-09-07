// @vitest-environment jsdom
/**
 * H19 — <Select>, <MultiSelect>, <TimePicker>, <DateRangePicker> rendered
 * their open dropdown as an `absolute` sibling of the trigger, inside
 * whichever ancestor happened to hold the field. `Modal.tsx` wraps its whole
 * dialog card in `overflow-hidden` (:112) and its body in `overflow-y-auto`
 * (:133) — a dropdown opened on a field inside a modal was clipped by one of
 * those two ancestors, the same defect `DatePicker`/`RowActionMenu`/
 * `DetailDrawer` were already fixed for.
 *
 * jsdom has no layout engine, so a clip cannot be observed visually. What IS
 * observable, and is exactly what the fix changes, is the DOM location: before
 * the fix the panel is a descendant of the modal's dialog node; after the fix
 * it is portalled to `document.body`, outside that subtree, with
 * `position: fixed` so it paints above the clipping ancestor regardless.
 */
import { describe, expect, it, afterEach } from 'vitest';
import { render, cleanup, fireEvent } from '@testing-library/react';
import i18n from '@/i18n';
import { Modal } from '@/components/ui/Modal';
import { Select } from '@/components/ui/Select';
import { MultiSelect } from '@/components/ui/MultiSelect';
import { TimePicker } from '@/components/ui/TimePicker';
import { DateRangePicker } from '@/components/ui/DateRangePicker';

const OPTIONS = [
  { value: 'a', label: 'A' },
  { value: 'b', label: 'B' },
];

afterEach(cleanup);

function dialog() { return document.querySelector('[role="dialog"]') as HTMLElement; }

/** Renders `field` inside a real Modal (the clipping ancestor) and returns the
 *  trigger button, wrapped so it's easy to find regardless of the field. */
function renderInModal(field: React.ReactElement) {
  render(
    <Modal open title="Test" onClose={() => {}}>
      <div data-testid="field-host">{field}</div>
    </Modal>,
  );
  return document.querySelector('[data-testid="field-host"] button') as HTMLButtonElement;
}

describe.each([
  ['Select', () => <Select value="" onChange={() => {}} options={OPTIONS} />, 'select-panel'],
  ['MultiSelect', () => <MultiSelect value={[]} onChange={() => {}} options={OPTIONS} />, 'multiselect-panel'],
  ['TimePicker', () => <TimePicker value="" onChange={() => {}} />, 'time-picker-panel'],
  ['DateRangePicker', () => <DateRangePicker from="" to="" onChange={() => {}} />, 'date-range-picker-panel'],
] as const)('%s inside a Modal (H19)', (_name, Field, testId) => {
  it('portals its open panel to document.body, out of the modal\'s overflow-hidden card', async () => {
    await i18n.changeLanguage('uz');
    const trigger = renderInModal(<Field />);
    fireEvent.click(trigger);
    const panel = document.querySelector(`[data-testid="${testId}"]`) as HTMLElement;
    expect(panel, 'no portalled panel was rendered').toBeTruthy();
    // The regression: pre-fix, the panel was a DOM descendant of the dialog.
    expect(dialog().contains(panel)).toBe(false);
    // Direct child of <body>, not nested under anything else that could clip it.
    expect(panel.parentElement).toBe(document.body);
  });

  it('renders the panel with position: fixed so a clipping ancestor cannot cut it off', async () => {
    await i18n.changeLanguage('uz');
    const trigger = renderInModal(<Field />);
    fireEvent.click(trigger);
    const panel = document.querySelector(`[data-testid="${testId}"]`) as HTMLElement;
    expect(panel.className).toMatch(/\bfixed\b/);
  });

  it('closes on outside click even though the panel lives outside the field\'s own subtree', async () => {
    await i18n.changeLanguage('uz');
    const trigger = renderInModal(<Field />);
    fireEvent.click(trigger);
    expect(document.querySelector(`[data-testid="${testId}"]`)).toBeTruthy();
    fireEvent.mouseDown(document.body);
    expect(document.querySelector(`[data-testid="${testId}"]`)).toBeNull();
  });
});

/**
 * H19, second pass — portalling ALONE is not the fix.
 *
 * `DatePicker`/`DateRangePicker`/`TimePicker` place their portalled panel with
 * the shared `useAnchoredOverlay`, which subtracts the bottom nav / soft
 * keyboard (`bottomObstruction`), flips above when there is no room below,
 * clamps horizontally and caps the height to the band that is actually
 * reachable. `Select`/`MultiSelect` first shipped this fix with a local
 * `rect.bottom + 8` instead — which lifts the panel OUT of the modal's clip and
 * then drops it straight through the bottom of a 360x640 Telegram screen, under
 * the fixed nav where a tap cannot reach it. Trading one unreachable panel for
 * another is not a fix.
 *
 * jsdom reports 0 for every rect, so the arithmetic itself cannot be exercised
 * here. What IS observable is that all four panels carry the shape only the
 * shared hook produces: a measured `max-height` and its own scroll container.
 * That is what makes the local re-measure impossible to reintroduce silently.
 */
describe.each([
  ['Select', () => <Select value="" onChange={() => {}} options={OPTIONS} />, 'select-panel'],
  ['MultiSelect', () => <MultiSelect value={[]} onChange={() => {}} options={OPTIONS} />, 'multiselect-panel'],
  ['TimePicker', () => <TimePicker value="" onChange={() => {}} />, 'time-picker-panel'],
  ['DateRangePicker', () => <DateRangePicker from="" to="" onChange={() => {}} />, 'date-range-picker-panel'],
] as const)('%s is placed by the shared viewport-aware hook', (_name, Field, testId) => {
  it('caps the panel to a measured band height instead of letting it run off-screen', async () => {
    await i18n.changeLanguage('uz');
    const trigger = renderInModal(<Field />);
    fireEvent.click(trigger);
    const panel = document.querySelector(`[data-testid="${testId}"]`) as HTMLElement;
    // `useAnchoredOverlay` always returns a maxHeight; a hand-rolled
    // `{ top, left, width }` placement has nothing to write here.
    expect(panel.style.maxHeight, 'panel has no measured height cap').not.toBe('');
  });

  it('scrolls inside that band rather than overflowing it', async () => {
    await i18n.changeLanguage('uz');
    const trigger = renderInModal(<Field />);
    fireEvent.click(trigger);
    const panel = document.querySelector(`[data-testid="${testId}"]`) as HTMLElement;
    expect(panel.className).toMatch(/overflow-y-auto/);
  });
});
