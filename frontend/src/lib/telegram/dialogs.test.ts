// @vitest-environment jsdom
import { describe, it, expect, afterEach } from 'vitest';
import { nativeConfirm, nativeAlert } from './dialogs';

type W = typeof window & { Telegram?: { WebApp?: Record<string, unknown> } };

afterEach(() => {
  delete (window as W).Telegram;
});

describe('telegram native dialogs — fallback signalling', () => {
  it('returns null outside Telegram so the caller renders its own dialog', () => {
    expect(nativeConfirm({ message: 'x' })).toBeNull();
    expect(nativeAlert('x')).toBeNull();
  });

  it('returns null on a client too old for popups (< 6.2)', () => {
    (window as W).Telegram = { WebApp: { version: '6.0', platform: 'ios', showPopup: () => {} } };
    expect(nativeConfirm({ message: 'x' })).toBeNull();
  });

  it('resolves true/false from the native popup button id', async () => {
    let captured: { buttons?: { id?: string }[] } | null = null;
    (window as W).Telegram = {
      WebApp: {
        version: '7.0',
        platform: 'ios',
        showPopup: (params: { buttons?: { id?: string }[] }, cb?: (id: string) => void) => {
          captured = params;
          cb?.('ok'); // simulate the user tapping the confirm button
        },
      },
    };
    const result = nativeConfirm({ message: 'Pay now?', destructive: true });
    expect(result).not.toBeNull();
    await expect(result).resolves.toBe(true);
    // destructive option becomes a destructive native button
    expect(captured!.buttons?.[0]).toMatchObject({ id: 'ok', type: 'destructive' });
  });

  it('resolves false when the cancel button is pressed', async () => {
    (window as W).Telegram = {
      WebApp: {
        version: '7.0',
        platform: 'ios',
        showPopup: (_p: unknown, cb?: (id: string) => void) => cb?.('cancel'),
      },
    };
    await expect(nativeConfirm({ message: 'x' })).resolves.toBe(false);
  });
});
