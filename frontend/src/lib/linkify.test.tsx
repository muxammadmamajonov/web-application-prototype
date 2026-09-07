// @vitest-environment jsdom
/**
 * MUDD-71 H3 — `linkifySupport` used to hold the third copy of the support
 * handle: a literal `https://t.me/muddatosupport`, lowercase, disagreeing with
 * `SUPPORT_USERNAME` ('MuddatoSupport') that every other surface used. It now
 * takes the handle, so an operator's configured value reaches the mention text
 * inside translated copy too — not just the standalone links.
 */
import { describe, expect, it, afterEach } from 'vitest';
import '@testing-library/jest-dom/vitest';
import { render, screen, cleanup } from '@testing-library/react';
import { linkifySupport } from './linkify';

afterEach(() => cleanup());

describe('linkifySupport', () => {
  it('links the configured handle, not a baked-in one', () => {
    render(<p>{linkifySupport('Savol bo‘lsa @MuddatoSupport ga yozing.', 'MuddatoHelp')}</p>);
    const a = screen.getByRole('link');
    expect(a).toHaveAttribute('href', 'https://t.me/MuddatoHelp');
    expect(a).toHaveTextContent('@MuddatoHelp');
  });

  it('defaults to the build-time constant, so callers that pass nothing are unchanged', () => {
    render(<p>{linkifySupport('Yozing: @MuddatoSupport')}</p>);
    expect(screen.getByRole('link')).toHaveAttribute('href', 'https://t.me/MuddatoSupport');
  });

  it('preserves the surrounding text verbatim', () => {
    const { container } = render(<p>{linkifySupport('avval @MuddatoSupport keyin', 'X')}</p>);
    expect(container.textContent).toBe('avval @X keyin');
  });

  it('leaves text without a mention untouched and adds no link', () => {
    const { container } = render(<p>{linkifySupport('hech qanday mention yo‘q', 'X')}</p>);
    expect(container.querySelector('a')).toBeNull();
    expect(container.textContent).toBe('hech qanday mention yo‘q');
  });
});
