// @vitest-environment jsdom
/**
 * A document request button that cannot fire twice.
 *
 * REPRODUCED IN THE FOUNDER BETA: requesting a contract inside Telegram
 * renders a PDF server-side, so on mobile data the button looked untouched for
 * several seconds. Tapping again is the natural thing to do, and every tap
 * produced another document in the chat.
 *
 * The other half is the honesty rule: `sent` may only appear after the server
 * confirms delivery. Showing it optimistically sends the user hunting through
 * their chat for a file that never arrived — worse than an error, because they
 * blame themselves.
 */
import { describe, expect, it, vi, beforeEach, afterEach } from 'vitest';
import { act, renderHook, waitFor } from '@testing-library/react';

const downloadGranted = vi.fn();
vi.mock('@/lib/download', () => ({ downloadGranted: (...a: unknown[]) => downloadGranted(...a) }));

const toastError = vi.fn();
vi.mock('@/lib/feedback', () => ({ toast: { error: (m: string) => toastError(m) } }));

const { useDocumentSend } = await import('./useDocumentSend');

const REQ = { path: '/x/contract.pdf', filename: 'MDD-2026-000001-shartnoma.pdf' };

describe('useDocumentSend', () => {
  beforeEach(() => {
    downloadGranted.mockReset();
    toastError.mockReset();
  });
  afterEach(() => vi.restoreAllMocks());

  it('starts idle', () => {
    const { result } = renderHook(() => useDocumentSend('contract', 'c1', REQ));
    expect(result.current.state).toBe('idle');
    expect(result.current.busy).toBe(false);
  });

  it('goes sending → sent when the server confirms delivery', async () => {
    let settle: (v: string) => void = () => {};
    downloadGranted.mockReturnValue(new Promise<string>((r) => { settle = r; }));

    const { result } = renderHook(() => useDocumentSend('contract', 'c1', REQ));
    act(() => { void result.current.send(); });
    await waitFor(() => expect(result.current.state).toBe('sending'));
    expect(result.current.busy).toBe(true);

    await act(async () => { settle('delegated'); });
    await waitFor(() => expect(result.current.state).toBe('sent'));
    expect(result.current.busy).toBe(false);
  });

  it('ignores a second tap while the first is in flight', async () => {
    // THE defect. Two taps in the same frame both read the old state, so the
    // state value alone cannot prevent this — only a synchronous ref can.
    let settle: (v: string) => void = () => {};
    downloadGranted.mockReturnValue(new Promise<string>((r) => { settle = r; }));

    const { result } = renderHook(() => useDocumentSend('contract', 'c1', REQ));
    await act(async () => {
      void result.current.send();
      void result.current.send();
      void result.current.send();
    });
    expect(downloadGranted).toHaveBeenCalledTimes(1);

    await act(async () => { settle('delegated'); });
    await waitFor(() => expect(result.current.state).toBe('sent'));
    expect(downloadGranted).toHaveBeenCalledTimes(1);
  });

  it('does not claim "sent" when delivery failed', async () => {
    downloadGranted.mockResolvedValue('failed');
    const { result } = renderHook(() => useDocumentSend('contract', 'c1', REQ));
    await act(async () => { await result.current.send(); });
    expect(result.current.state).toBe('failed');
  });

  it('surfaces a thrown error rather than showing sent', async () => {
    downloadGranted.mockRejectedValue(new Error('network'));
    const { result } = renderHook(() => useDocumentSend('contract', 'c1', REQ));
    await act(async () => { await result.current.send(); });
    expect(result.current.state).toBe('failed');
    expect(toastError).toHaveBeenCalled();
  });

  it('allows a retry after a failure', async () => {
    downloadGranted.mockResolvedValueOnce('failed').mockResolvedValueOnce('delegated');
    const { result } = renderHook(() => useDocumentSend('contract', 'c1', REQ));
    await act(async () => { await result.current.send(); });
    expect(result.current.state).toBe('failed');

    act(() => result.current.reset());
    expect(result.current.state).toBe('idle');

    await act(async () => { await result.current.send(); });
    expect(result.current.state).toBe('sent');
    expect(downloadGranted).toHaveBeenCalledTimes(2);
  });

  it('treats a browser save as arrival too', async () => {
    // Outside Telegram the file lands in the downloads folder. Same honesty
    // rule, different mechanism.
    downloadGranted.mockResolvedValue('saved');
    const { result } = renderHook(() => useDocumentSend('contract', 'c1', REQ));
    await act(async () => { await result.current.send(); });
    expect(result.current.state).toBe('sent');
  });

  it('cannot be reset out from under an in-flight request', async () => {
    let settle: (v: string) => void = () => {};
    downloadGranted.mockReturnValue(new Promise<string>((r) => { settle = r; }));
    const { result } = renderHook(() => useDocumentSend('contract', 'c1', REQ));
    act(() => { void result.current.send(); });
    await waitFor(() => expect(result.current.state).toBe('sending'));

    act(() => result.current.reset());
    expect(result.current.state).toBe('sending');   // still busy — not reopened

    await act(async () => { settle('delegated'); });
    await waitFor(() => expect(result.current.state).toBe('sent'));
  });
});
