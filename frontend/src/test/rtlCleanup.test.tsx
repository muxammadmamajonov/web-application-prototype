// @vitest-environment jsdom
/**
 * Guards the global `afterEach(cleanup)` in `src/test/setup.ts`.
 *
 * Without it, a rendered tree survives its own test and is still mounted when
 * jsdom is torn down; React 19's deferred scheduler task then dereferences a
 * `window` that no longer exists and vitest fails the run on an unhandled
 * error while reporting every test as passed. Removing `setupFiles` turns the
 * second assertion here red deterministically, so the flake can't come back
 * unnoticed.
 */
import { describe, expect, it } from 'vitest';
import { render } from '@testing-library/react';

describe('global RTL cleanup', () => {
  it('mounts a tree into document.body', () => {
    render(<div data-testid="mounted">x</div>);
    expect(document.body.querySelectorAll('[data-testid="mounted"]')).toHaveLength(1);
  });

  it('has unmounted the previous test’s tree before this one starts', () => {
    expect(document.body.querySelectorAll('[data-testid="mounted"]')).toHaveLength(0);
  });
});
