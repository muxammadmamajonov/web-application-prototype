// @vitest-environment jsdom
/** Command palette (Cmd/Ctrl+K): opens on the shortcut, filters by query, runs
 *  the highlighted command on Enter, closes on Escape. */
import { describe, it, expect, vi, afterEach } from 'vitest';
import { render, screen, cleanup, fireEvent } from '@testing-library/react';
import '@testing-library/jest-dom/vitest';

// Inline-default t() so the component renders without the full i18n runtime.
vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (_k: string, d?: string) => d ?? _k }),
}));

import { CommandPalette, type Command } from './CommandPalette';

afterEach(cleanup);

describe('CommandPalette', () => {
  it('is hidden until Cmd/Ctrl+K opens it', () => {
    render(<CommandPalette commands={[{ key: 'a', label: 'Mijozlar', perform: vi.fn() }]} />);
    expect(screen.queryByRole('dialog')).toBeNull();
    fireEvent.keyDown(window, { key: 'k', metaKey: true });
    expect(screen.getByRole('dialog')).toBeInTheDocument();
  });

  it('filters by query and runs the highlighted command on Enter', () => {
    const goCustomers = vi.fn();
    const commands: Command[] = [
      { key: 'customers', label: 'Mijozlar', perform: goCustomers },
      { key: 'payments', label: "To'lovlar", perform: vi.fn() },
    ];
    render(<CommandPalette commands={commands} />);
    fireEvent.keyDown(window, { key: 'k', ctrlKey: true });
    const input = screen.getByRole('dialog').querySelector('input')!;
    fireEvent.change(input, { target: { value: 'mij' } });
    expect(screen.getByText('Mijozlar')).toBeInTheDocument();
    expect(screen.queryByText("To'lovlar")).toBeNull();
    fireEvent.keyDown(input, { key: 'Enter' });
    expect(goCustomers).toHaveBeenCalledOnce();
    // Running a command closes the palette.
    expect(screen.queryByRole('dialog')).toBeNull();
  });

  it('closes on Escape without running anything', () => {
    const perform = vi.fn();
    render(<CommandPalette commands={[{ key: 'a', label: 'A', perform }]} />);
    fireEvent.keyDown(window, { key: 'k', metaKey: true });
    const input = screen.getByRole('dialog').querySelector('input')!;
    fireEvent.keyDown(input, { key: 'Escape' });
    expect(screen.queryByRole('dialog')).toBeNull();
    expect(perform).not.toHaveBeenCalled();
  });
});
