// @vitest-environment jsdom
/**
 * MUDD-84 — the regression this ticket exists for.
 *
 * Before this fix, the tenant/staff Payments screens called `useTenantPayments()`
 * — one UNFILTERED fetch capped at 200 rows — then filtered by status IN THE
 * BROWSER. On a tenant with more than 200 payments, any rejected/approved/
 * refunded payment older than the 200th was invisible under every status
 * view: the row never reached the browser to be filtered in the first place,
 * not merely slow to load.
 *
 * `useTenantPaymentsPage` fixes this by sending `status_filter` to the server
 * and paging with a stable `offset`, mirroring `useTenantWorklistPages`. This
 * pins the property that actually matters: walking a status to completion
 * reaches every row that status has, not just the newest 200 payments
 * overall.
 */
import { describe, expect, it, beforeEach, vi } from 'vitest';
import { act, renderHook, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

interface FakePayment { id: string; status: 'pending' | 'approved' | 'rejected' | 'refunded' }

// 203 payments in newest-first order (mirrors the server's
// `created_at DESC, id DESC` ordering) — the OLDEST 3, past the old 200-row
// cap, are rejected. The old client-side-200-then-filter approach could never
// see them under the "Rad etilgan" view.
const ALL: FakePayment[] = Array.from({ length: 203 }, (_, i) => ({
  id: `p${i}`,
  status: i >= 200 ? 'rejected' : 'approved',
}));

const get = vi.fn(async (_url: string, config?: { params?: Record<string, unknown> }) => {
  const params = config?.params ?? {};
  const limit = (params.limit as number | undefined) ?? 50;
  const offset = (params.offset as number | undefined) ?? 0;
  const statusFilter = params.status_filter as string | undefined;
  const filtered = statusFilter ? ALL.filter((p) => p.status === statusFilter) : ALL;
  const items = filtered.slice(offset, offset + limit);
  const counts = { pending: 0, approved: 0, rejected: 0, refunded: 0 };
  for (const p of ALL) counts[p.status] += 1;
  return { data: { items, total: filtered.length, counts } };
});
vi.mock('@/lib/api', () => ({ api: { get: (...a: unknown[]) => get(...(a as [string, { params?: Record<string, unknown> }])) } }));

const { useTenantPaymentsPage } = await import('./tenant');

function wrapper({ children }: { children: React.ReactNode }) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return <QueryClientProvider client={qc}>{children}</QueryClientProvider>;
}

beforeEach(() => { get.mockClear(); });

describe('useTenantPaymentsPage', () => {
  it('sends status_filter to the server instead of filtering the response in the browser', async () => {
    const { result } = renderHook(() => useTenantPaymentsPage(50, 'rejected'), { wrapper });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));

    expect(get).toHaveBeenCalledTimes(1);
    const [url, config] = get.mock.calls[0] as [string, { params?: Record<string, unknown> }];
    expect(url).toBe('/tenant/payments');
    expect(config?.params).toMatchObject({ status_filter: 'rejected', limit: 50, offset: 0 });
  });

  it('omits status_filter entirely for the "Hammasi" (all) tab', async () => {
    const { result } = renderHook(() => useTenantPaymentsPage(50, ''), { wrapper });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));

    const [, config] = get.mock.calls[0] as [string, { params?: Record<string, unknown> }];
    expect(config?.params).not.toHaveProperty('status_filter');
  });

  it('RED before the fix / GREEN after: reaches every rejected payment by paging the filter, not just the newest 200 payments overall', async () => {
    const { result } = renderHook(() => useTenantPaymentsPage(50, 'rejected'), { wrapper });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));

    while (result.current.hasNextPage) {
      await act(async () => { await result.current.fetchNextPage(); });
    }

    const items = result.current.data?.pages.flatMap((p) => p.items) ?? [];
    expect(items.map((p) => p.id).sort()).toEqual(['p200', 'p201', 'p202']);
    expect(result.current.data?.pages[0]?.total).toBe(3);
    // Never asked for more than the filtered set actually holds.
    expect(get).toHaveBeenCalledTimes(items.length > 50 ? Math.ceil(items.length / 50) : 1);
  });

  it('switching the status tab refetches from offset 0 rather than filtering the pages already in hand', async () => {
    const { result, rerender } = renderHook(
      ({ status }: { status: string }) => useTenantPaymentsPage(50, status),
      { wrapper, initialProps: { status: 'approved' } },
    );
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    get.mockClear();

    rerender({ status: 'rejected' });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));

    const [, config] = get.mock.calls[0] as [string, { params?: Record<string, unknown> }];
    expect(config?.params).toMatchObject({ status_filter: 'rejected', offset: 0 });
    const items = result.current.data?.pages.flatMap((p) => p.items) ?? [];
    expect(items.every((p) => p.status === 'rejected')).toBe(true);
  });
});
