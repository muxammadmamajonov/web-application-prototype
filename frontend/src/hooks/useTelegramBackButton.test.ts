// @vitest-environment jsdom
import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { pushBackHandler } from './useTelegramBackButton';

type W = typeof window & { Telegram?: { WebApp?: Record<string, unknown> } };

function mockBackButton() {
  let bound: (() => void) | null = null;
  const bb = {
    isVisible: false,
    show: vi.fn(() => { bb.isVisible = true; }),
    hide: vi.fn(() => { bb.isVisible = false; }),
    onClick: vi.fn((cb: () => void) => { bound = cb; }),
    offClick: vi.fn(() => { bound = null; }),
    press: () => bound?.(), // simulate a native Back tap
  };
  return bb;
}

let bb: ReturnType<typeof mockBackButton>;

beforeEach(() => {
  bb = mockBackButton();
  (window as W).Telegram = { WebApp: { version: '7.0', platform: 'ios', BackButton: bb } };
});
afterEach(() => { delete (window as W).Telegram; });

describe('telegram back-button stack coordinator', () => {
  it('shows on first push, the TOPMOST handler fires, restores on pop, hides when empty', () => {
    const h1 = vi.fn();
    const h2 = vi.fn();

    const pop1 = pushBackHandler(h1);
    expect(bb.show).toHaveBeenCalled();
    bb.press();
    expect(h1).toHaveBeenCalledTimes(1);

    // An overlay (e.g. the Menu sheet) pushes on top → it now owns Back.
    const pop2 = pushBackHandler(h2);
    bb.press();
    expect(h2).toHaveBeenCalledTimes(1);
    expect(h1).toHaveBeenCalledTimes(1); // h1 did NOT fire — h2 is on top

    // Closing the overlay restores the underlying handler.
    pop2();
    bb.press();
    expect(h1).toHaveBeenCalledTimes(2);

    // Last pop → nothing owns Back → hidden (Telegram shows "Close" again).
    pop1();
    expect(bb.hide).toHaveBeenCalled();
  });

  it('is an inert no-op outside Telegram', () => {
    delete (window as W).Telegram;
    const h = vi.fn();
    const pop = pushBackHandler(h); // must not throw
    pop();
    expect(h).not.toHaveBeenCalled();
  });
});
