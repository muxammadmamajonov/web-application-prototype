// @vitest-environment jsdom
/**
 * H17 — `useTenantPayments()` and `usePlatformPayments()` were called with no
 * `limit` at all, so the fetch silently rode the backend's own default
 * (`limit: LimitQ = 50`) — real rows past the 50th were invisible on a busy
 * shop's approval queue, not merely slow to load, since `DataTable` paginates
 * client-side over whatever the hook returned. Both hooks now request the
 * backend's hard cap (`le=200`) explicitly and forward the query's
 * `AbortSignal` (M-17) to axios.
 */
import { describe, expect, it, beforeEach, vi } from 'vitest';
import { renderHook, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

const get = vi.fn(async (_url: string, _config?: unknown) => ({ data: { items: [], total: 0 } }));
vi.mock('@/lib/api', () => ({ api: { get: (...a: unknown[]) => get(...(a as [string, unknown?])) } }));

const { useTenantPayments } = await import('./tenant');
const { usePlatformPayments } = await import('./platform');

function wrapper({ children }: { children: React.ReactNode }) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return <QueryClientProvider client={qc}>{children}</QueryClientProvider>;
}

beforeEach(() => { get.mockClear(); });

describe('useTenantPayments requests the backend\'s bounded max and forwards AbortSignal', () => {
  it('calls GET /tenant/payments with limit: 200 and a signal', async () => {
    const { result } = renderHook(() => useTenantPayments(), { wrapper });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));

    expect(get).toHaveBeenCalledTimes(1);
    const [url, config] = get.mock.calls[0] as [string, { params?: Record<string, unknown>; signal?: AbortSignal }];
    expect(url).toBe('/tenant/payments');
    expect(config?.params).toMatchObject({ limit: 200 });
    expect(config?.signal).toBeInstanceOf(AbortSignal);
  });
});

describe('usePlatformPayments requests the backend\'s bounded max and forwards AbortSignal', () => {
  it('calls GET /platform/payments with limit: 200 and a signal', async () => {
    const { result } = renderHook(() => usePlatformPayments(), { wrapper });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));

    expect(get).toHaveBeenCalledTimes(1);
    const [url, config] = get.mock.calls[0] as [string, { params?: Record<string, unknown>; signal?: AbortSignal }];
    expect(url).toBe('/platform/payments');
    expect(config?.params).toMatchObject({ limit: 200 });
    expect(config?.signal).toBeInstanceOf(AbortSignal);
  });

  it('still forwards an explicit status_filter alongside the bounded limit', async () => {
    const { result } = renderHook(() => usePlatformPayments('pending'), { wrapper });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));

    const [, config] = get.mock.calls[0] as [string, { params?: Record<string, unknown> }];
    expect(config?.params).toMatchObject({ status_filter: 'pending', limit: 200 });
  });
});
