// @vitest-environment jsdom
/** (#174) The double-submit guard: a `loading` Button is disabled, so a second
 *  click can't fire while an async action is in flight. */
import { describe, it, expect, vi, afterEach } from 'vitest';
import { render, screen, cleanup, fireEvent } from '@testing-library/react';
import '@testing-library/jest-dom/vitest';

import { Button } from './Button';

afterEach(cleanup);

describe('Button double-submit guard (#174)', () => {
  it('is disabled + aria-busy and does NOT fire onClick while loading', () => {
    const onClick = vi.fn();
    render(<Button loading onClick={onClick}>Save</Button>);
    const btn = screen.getByRole('button', { name: /save/i });
    expect(btn).toBeDisabled();
    expect(btn).toHaveAttribute('aria-busy', 'true');
    fireEvent.click(btn);          // disabled → the DOM never dispatches the click
    expect(onClick).not.toHaveBeenCalled();
  });

  it('fires onClick exactly once when not loading', () => {
    const onClick = vi.fn();
    render(<Button onClick={onClick}>Save</Button>);
    fireEvent.click(screen.getByRole('button', { name: /save/i }));
    expect(onClick).toHaveBeenCalledTimes(1);
  });

  it('respects an explicit disabled prop', () => {
    const onClick = vi.fn();
    render(<Button disabled onClick={onClick}>Save</Button>);
    fireEvent.click(screen.getByRole('button', { name: /save/i }));
    expect(onClick).not.toHaveBeenCalled();
  });
});
