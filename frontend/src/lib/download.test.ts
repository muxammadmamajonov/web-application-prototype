// @vitest-environment jsdom
/**
 * Getting a file out of the app, in every environment it has to work in.
 *
 * The Telegram branch is the one that matters. Seven call sites minted a `blob:`
 * URL and clicked a synthetic anchor; inside the Mini App that silently does
 * nothing — no throw, no download, no message. These tests pin the two
 * behaviours that fix it: a signed https URL is handed straight to the system
 * browser, and without one the user is TOLD rather than left tapping a dead
 * button.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

const openExternalLink = vi.fn();
const isInsideTelegramClient = vi.fn(() => false);
const botDeepLink = vi.fn(() => 'https://t.me/MuddatoBot?start=login');
vi.mock('./telegram', () => ({
  openExternalLink: (...a: unknown[]) => openExternalLink(...a),
  isInsideTelegramClient: () => isInsideTelegramClient(),
  botDeepLink: (...a: unknown[]) => botDeepLink(...a),
}));

const warning = vi.fn();
const error = vi.fn();
const success = vi.fn();
vi.mock('./feedback', () => ({
  toast: {
    warning: (...a: unknown[]) => warning(...a),
    error: (...a: unknown[]) => error(...a),
    success: (...a: unknown[]) => success(...a),
  },
}));

let mockErrorKind = 'unknown';
vi.mock('./errors', () => ({
  apiErrorMessage: () => 'localized-error',
  errorKind: () => mockErrorKind,
}));

const reportError = vi.fn();
vi.mock('./sentry', () => ({ reportError: (...a: unknown[]) => reportError(...a) }));

const apiGet = vi.fn();
const apiPost = vi.fn();
vi.mock('./api', () => ({ api: { get: (...a: unknown[]) => apiGet(...a), post: (...a: unknown[]) => apiPost(...a) } }));

import { downloadFile, downloadGranted, grantDownloadUrl, safeFilename, saveBlob } from './download';

const REQ = { path: '/tenant/export.csv', filename: 'export.csv' };

beforeEach(() => {
  vi.clearAllMocks();
  apiPost.mockReset(); // no default across tests — each Telegram-outcome test sets its own
  isInsideTelegramClient.mockReturnValue(false);
  mockErrorKind = 'unknown';
  apiGet.mockResolvedValue({ data: new Blob(['a,b']) });
  // jsdom implements neither. Without these the save path throws and is caught
  // as a generic failure — which is how the first run of this file reported
  // 'failed' for a branch that works fine in a real browser.
  URL.createObjectURL = vi.fn(() => 'blob:mock');
  URL.revokeObjectURL = vi.fn();
});
afterEach(() => { document.body.innerHTML = ''; });

describe('a signed URL is preferred everywhere', () => {
  it.each([false, true])('hands it to the system browser (inside Telegram: %s)', async (inTg) => {
    isInsideTelegramClient.mockReturnValue(inTg);
    const r = await downloadFile({ ...REQ, signedUrl: 'https://cdn.muddato.uz/x?sig=1' });
    expect(r).toBe('delegated');
    expect(openExternalLink).toHaveBeenCalledWith('https://cdn.muddato.uz/x?sig=1');
  });

  it('does not spend the user’s data fetching bytes it will not use', async () => {
    await downloadFile({ ...REQ, signedUrl: 'https://cdn.muddato.uz/x' });
    expect(apiGet).not.toHaveBeenCalled();
  });
});

describe('inside Telegram without a signed URL', () => {
  beforeEach(() => isInsideTelegramClient.mockReturnValue(true));

  it('tells the user instead of failing silently', async () => {
    const r = await downloadFile(REQ);
    expect(r).toBe('failed');
    expect(warning).toHaveBeenCalledTimes(1);
    // A rendered sentence, not a bare i18n key — `toast` does not translate.
    expect(warning.mock.calls[0][0]).not.toBe('download.openInBrowser');
    expect(String(warning.mock.calls[0][0]).length).toBeGreaterThan(10);
  });

  it('does not download bytes that cannot reach the filesystem', async () => {
    await downloadFile(REQ);
    expect(apiGet).not.toHaveBeenCalled();
  });

  it('refuses an in-memory blob the same way', () => {
    expect(saveBlob(new Blob(['x']), 'a.csv')).toBe('failed');
    expect(warning).toHaveBeenCalledTimes(1);
    expect(document.querySelector('a')).toBeNull();
  });
});

describe('downloadGranted inside Telegram', () => {
  beforeEach(() => isInsideTelegramClient.mockReturnValue(true));

  it('delivers to the chat when the server confirms it', async () => {
    apiPost.mockResolvedValue({ data: { sent: true, state: 'SENT' } });
    const r = await downloadGranted('contract', 'c1', REQ);
    expect(r).toBe('delegated');
    expect(success).toHaveBeenCalledTimes(1);
    expect(apiPost).toHaveBeenCalledWith('/downloads/grant', { kind: 'contract', id: 'c1', deliver: 'telegram' });
  });

  it('falls back to a signed URL when the bot was never started, and still delivers the file', async () => {
    // grant #1 (deliver=telegram) says UNAVAILABLE; grant #2 (plain url) succeeds.
    apiPost
      .mockResolvedValueOnce({ data: { sent: false, state: 'UNAVAILABLE' } })
      .mockResolvedValueOnce({ data: { url: 'https://cdn.muddato.uz/x?sig=1' } });
    const r = await downloadGranted('contract', 'c1', REQ);
    expect(r).toBe('delegated');
    expect(openExternalLink).toHaveBeenCalledWith('https://cdn.muddato.uz/x?sig=1');
    // The dead-end "open a browser" copy must not fire — the file DID arrive.
    expect(warning).not.toHaveBeenCalled();
  });

  it('tells the user to open the bot — not a nonexistent browser button — when both grants fail', async () => {
    apiPost
      .mockResolvedValueOnce({ data: { sent: false, state: 'UNAVAILABLE' } })
      .mockRejectedValueOnce(new Error('grant also failed'));
    const r = await downloadGranted('contract', 'c1', REQ);
    expect(r).toBe('failed');
    expect(warning).toHaveBeenCalledTimes(1);
    const [msg, action] = warning.mock.calls[0];
    expect(String(msg)).not.toMatch(/brauzer|browser/i);
    expect(action?.onAction).toBeTypeOf('function');
    action.onAction();
    expect(botDeepLink).toHaveBeenCalled();
    expect(openExternalLink).toHaveBeenCalledWith('https://t.me/MuddatoBot?start=login');
  });

  it('falls back to a signed URL when rate-limited, and still delivers the file', async () => {
    // The signed-URL grant is a separate endpoint with no shared rate limiter,
    // so a 429 on the chat-send call must not skip the fallback.
    mockErrorKind = 'rateLimited';
    apiPost
      .mockRejectedValueOnce({ response: { status: 429 } })
      .mockResolvedValueOnce({ data: { url: 'https://cdn.muddato.uz/x?sig=3' } });
    const r = await downloadGranted('contract', 'c1', REQ);
    expect(r).toBe('delegated');
    expect(openExternalLink).toHaveBeenCalledWith('https://cdn.muddato.uz/x?sig=3');
    expect(warning).not.toHaveBeenCalled();
  });

  it('shows a rate-limit message when the fallback grant also fails', async () => {
    mockErrorKind = 'rateLimited';
    apiPost.mockRejectedValue({ response: { status: 429 } });
    const r = await downloadGranted('contract', 'c1', REQ);
    expect(r).toBe('failed');
    expect(apiPost).toHaveBeenCalledTimes(2); // fallback grant was attempted
    expect(warning).toHaveBeenCalledTimes(1);
  });

  it('reports a genuine send failure and still tries the signed-URL fallback', async () => {
    mockErrorKind = 'server';
    apiPost
      .mockRejectedValueOnce(new Error('telegram outage'))
      .mockResolvedValueOnce({ data: { url: 'https://cdn.muddato.uz/x?sig=2' } });
    const r = await downloadGranted('contract', 'c1', REQ);
    expect(r).toBe('delegated');
    expect(reportError).toHaveBeenCalled();
  });

  it('gives an honest retry message — not "open a browser" — when everything fails', async () => {
    mockErrorKind = 'server';
    apiPost.mockRejectedValue(new Error('down'));
    const r = await downloadGranted('contract', 'c1', REQ);
    expect(r).toBe('failed');
    expect(String(warning.mock.calls[0][0])).not.toMatch(/brauzer|browser/i);
  });
});

describe('grantDownloadUrl', () => {
  it('reports an unexpected failure rather than swallowing it silently', async () => {
    mockErrorKind = 'server';
    apiPost.mockRejectedValue(new Error('boom'));
    expect(await grantDownloadUrl('contract', 'c1')).toBeNull();
    expect(reportError).toHaveBeenCalled();
  });

  it('does not report an ordinary not-found', async () => {
    mockErrorKind = 'notFound';
    apiPost.mockRejectedValue({ response: { status: 404 } });
    expect(await grantDownloadUrl('contract', 'c1')).toBeNull();
    expect(reportError).not.toHaveBeenCalled();
  });
});

describe('an ordinary browser', () => {
  it('fetches and saves with the requested filename', async () => {
    const clicks: string[] = [];
    const realCreate = document.createElement.bind(document);
    vi.spyOn(document, 'createElement').mockImplementation((tag: string) => {
      const el = realCreate(tag) as HTMLAnchorElement;
      if (tag === 'a') el.click = () => clicks.push(el.download);
      return el;
    });

    const r = await downloadFile(REQ);
    expect(r).toBe('saved');
    expect(apiGet).toHaveBeenCalledWith('/tenant/export.csv', { responseType: 'blob' });
    expect(clicks).toEqual(['export.csv']);
    // The anchor must not be left in the DOM.
    expect(document.querySelector('a')).toBeNull();
    vi.mocked(document.createElement).mockRestore();
  });

  it('surfaces a localized message when the request fails', async () => {
    apiGet.mockRejectedValue(new Error('boom'));
    expect(await downloadFile(REQ)).toBe('failed');
    expect(error).toHaveBeenCalledWith('localized-error');
  });
});

describe('safeFilename', () => {
  it.each([
    ['Ali & Vali', '.pdf', 'Ali & Vali.pdf'],
    ['a/b:c*d?e', '.pdf', 'a-b-c-d-e.pdf'],
    ['  spaced  ', 'csv', 'spaced.csv'],
    ['...hidden', '.pdf', 'hidden.pdf'],
    ['', '.pdf', 'muddato.pdf'],
    ['trailing.', '.pdf', 'trailing.pdf'],
  ])('%s -> %s', (base, ext, want) => {
    expect(safeFilename(base, ext)).toBe(want);
  });

  it('keeps ordinary letters, digits and Cyrillic intact', () => {
    // Guards the control-character class: written as a literal byte range it
    // silently stripped normal characters too.
    expect(safeFilename('Shartnoma 2026 Дилноза', '.pdf'))
      .toBe('Shartnoma 2026 Дилноза.pdf');
  });

  it('strips a newline rather than emitting a two-line filename', () => {
    expect(safeFilename('a\nb', '.pdf')).toBe('a b.pdf');
  });

  it('bounds the length so the OS does not reject it', () => {
    expect(safeFilename('x'.repeat(500), '.pdf').length).toBeLessThanOrEqual(84);
  });
});
