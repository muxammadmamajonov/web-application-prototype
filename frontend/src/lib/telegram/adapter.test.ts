// @vitest-environment jsdom
import { describe, it, expect, afterEach } from 'vitest';
import { isVersionAtLeast, isInsideTelegram, platform, colorScheme } from './adapter';

type W = typeof window & { Telegram?: { WebApp?: Record<string, unknown> } };

function mockWebApp(partial: Record<string, unknown>) {
  (window as W).Telegram = { WebApp: { version: '6.0', platform: 'unknown', colorScheme: 'light', ...partial } };
}

afterEach(() => {
  delete (window as W).Telegram;
});

describe('telegram adapter — version gating', () => {
  it('compares numeric versions when the client lacks isVersionAtLeast()', () => {
    mockWebApp({ version: '7.10' });
    expect(isVersionAtLeast('6.1')).toBe(true);
    expect(isVersionAtLeast('7.7')).toBe(true);
    expect(isVersionAtLeast('7.10')).toBe(true);
    expect(isVersionAtLeast('8.0')).toBe(false); // 7.10 < 8.0
  });

  it('treats minor versions numerically (7.10 > 7.9, not string-wise)', () => {
    mockWebApp({ version: '7.10' });
    expect(isVersionAtLeast('7.9')).toBe(true);
  });

  it('defers to the client-provided isVersionAtLeast() when present', () => {
    mockWebApp({ version: '6.0', isVersionAtLeast: (v: string) => v === '8.0' });
    expect(isVersionAtLeast('8.0')).toBe(true);
    expect(isVersionAtLeast('6.1')).toBe(false);
  });

  it('returns false for every gate outside Telegram', () => {
    expect(isVersionAtLeast('6.0')).toBe(false);
  });
});

describe('telegram adapter — detection + accessors', () => {
  it('isInsideTelegram is true only with a real platform / proxy / launch param', () => {
    expect(isInsideTelegram()).toBe(false);
    mockWebApp({ platform: 'ios' });
    expect(isInsideTelegram()).toBe(true);
  });

  it('platform() and colorScheme() read the handle with safe defaults', () => {
    expect(platform()).toBe('unknown');
    expect(colorScheme()).toBe('light');
    mockWebApp({ platform: 'android', colorScheme: 'dark' });
    expect(platform()).toBe('android');
    expect(colorScheme()).toBe('dark');
  });
});
