// @vitest-environment jsdom
/**
 * One toast design across the product (#17).
 *
 * The previous design tinted the whole panel per status — four different
 * background/foreground pairs to keep legible, and the danger pairing sat at the
 * bottom of the contrast range. These tests pin the replacement: a single
 * neutral surface for every status, with colour confined to the icon and the
 * accent bar.
 *
 * They are written against rendered output rather than the class string, so the
 * panel can be restyled freely as long as the invariant holds: status must not
 * change the surface, and every status must stay announced correctly.
 */
import { describe, expect, it, beforeEach, afterEach } from 'vitest';
import { render, cleanup, act, screen } from '@testing-library/react';
import { FeedbackHost, toast, type ToastKind } from './feedback';

const KINDS: ToastKind[] = ['success', 'error', 'warning', 'info'];

function panelFor(kind: ToastKind): HTMLElement {
  const el = document.querySelector(`[data-toast-kind="${kind}"]`);
  expect(el, `no toast rendered for ${kind}`).toBeTruthy();
  return el as HTMLElement;
}

beforeEach(() => render(<FeedbackHost />));
afterEach(cleanup);

describe('every status shares one surface', () => {
  it.each(KINDS)('%s renders on the neutral panel, not a tinted one', (kind) => {
    act(() => { toast[kind](`${kind} message`); });
    const cls = panelFor(kind).className;
    expect(cls).toContain('bg-surface');
    // The old per-status tints must not come back.
    for (const tint of ['bg-success-soft', 'bg-info-soft', 'c-danger-soft', 'bg-warn-soft']) {
      expect(cls, `${kind} still tints the panel with ${tint}`).not.toContain(tint);
    }
  });

  it('all four panels carry an identical class list', () => {
    // The strongest form of "status does not change the surface": if any status
    // needed a different utility, this fails.
    act(() => { KINDS.forEach((k) => toast[k](`${k}!`)); });
    const classes = KINDS.map((k) => panelFor(k).className);
    expect(new Set(classes).size, `panels diverged:\n${classes.join('\n')}`).toBe(1);
  });

  it.each(KINDS)('%s differs only by its accent colour', (kind) => {
    act(() => { toast[kind](`${kind} message`); });
    // Set inline because Tailwind cannot emit a class per runtime token.
    expect(panelFor(kind).style.borderInlineStartColor).toBeTruthy();
  });
});

describe('the message stays readable', () => {
  it.each(KINDS)('%s shows its text', (kind) => {
    act(() => { toast[kind](`hello ${kind}`); });
    expect(screen.getByText(`hello ${kind}`)).toBeTruthy();
  });

  it('does not put the message inside the coloured element', () => {
    // Colour belongs on the icon, not on the text the user has to read.
    act(() => { toast.error('boom'); });
    const text = screen.getByText('boom');
    expect(text.style.color).toBe('');
  });
});

describe('announcements', () => {
  it('interrupts only for errors', () => {
    act(() => { toast.error('failed'); });
    const p = panelFor('error');
    expect(p.getAttribute('role')).toBe('alert');
    expect(p.getAttribute('aria-live')).toBe('assertive');
  });

  it.each(['success', 'warning', 'info'] as ToastKind[])('%s waits its turn', (kind) => {
    // Assertive yanks a screen-reader user out of what they are reading. A
    // warning matters but is not worth that; only a failed action is.
    act(() => { toast[kind]('note'); });
    const p = panelFor(kind);
    expect(p.getAttribute('role')).toBe('status');
    expect(p.getAttribute('aria-live')).toBe('polite');
  });

  it.each(KINDS)('%s is announced as one unit', (kind) => {
    act(() => { toast[kind]('note'); });
    expect(panelFor(kind).getAttribute('aria-atomic')).toBe('true');
  });
});

describe('the warning state exists at all', () => {
  it('is callable — it was missing before, so callers had to misuse error', () => {
    expect(typeof toast.warning).toBe('function');
    act(() => { toast.warning('careful'); });
    expect(screen.getByText('careful')).toBeTruthy();
  });
});
