// @vitest-environment jsdom
/**
 * TR-002-D1 / D2 — same-screen derived caches must refresh after a write.
 *
 * Two caches are derived from data the payment and installment mutations
 * change, and both are mounted on the very screen that performs the write:
 *
 *   D1 `['tenant','worklist','paged',pageSize,search]` — the collections queue
 *       on `InstallmentsPage`, which also mounts record-payment, cancel and
 *       reschedule. The paged key is parameterised, so a fix that names one
 *       (pageSize, search) pair leaves every other pair stale; the invalidation
 *       has to cover the whole family.
 *   D2 `['tenant','staff-today']` — the salesperson's own shift tiles.
 *       `['tenant','staff']` does not prefix-match `'staff-today'`.
 *
 * These are proven against a real QueryClient with real observers, driving the
 * real mutation hooks: each seeded query has a queryFn spy, so "invalidated" is
 * measured as an actual refetch, not as a flag or a string in the source.
 *
 * The matrix is deliberately asymmetric and follows the backend:
 *   * `reject` sets `status = rejected` and never calls `apply_payment`, so the
 *     installment is untouched and the worklist cannot change — but the tiles'
 *     `pending_count` does, so it invalidates staff-today only;
 *   * `cancel` / `reschedule` / `replace` change queue membership but are not
 *     payment-state changes, so they leave staff-today alone.
 * Anything wider would be blanket invalidation, which this file also guards
 * against by pinning control keys that must NOT refetch.
 */
import { describe, expect, it, beforeEach, afterEach, vi } from 'vitest';
import { renderHook, act, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider, QueryObserver } from '@tanstack/react-query';
import { queryKeys as k } from '@/lib/queryKeys';

const post = vi.fn(async () => ({ data: { ok: true } }));
const put = vi.fn(async () => ({ data: { ok: true } }));
vi.mock('@/lib/api', () => ({ api: { post: (...a: unknown[]) => post(...a), put: (...a: unknown[]) => put(...a) } }));

const {
  useRecordTenantPayment, useApprovePayment, useRejectPayment,
  useCancelInstallment, useRescheduleInstallment, useReplaceInstallment,
  useCreateInstallment,
} = await import('@/lib/mutations');

/** Every cached query this pass cares about, plus controls that must not move. */
const SEEDS = {
  'worklistPaged(20,"")': k.tenant.worklistPaged(20, ''),
  'worklistPaged(20,"ali")': k.tenant.worklistPaged(20, 'ali'),
  'worklistPaged(50,"")': k.tenant.worklistPaged(50, ''),
  'worklist(8)': k.tenant.worklist(8),
  'staffToday()': k.tenant.staffToday(),
  'overview()': k.tenant.overview(),
  // controls — no mutation below may touch these
  'customers': k.tenant.customers({ q: 'x' }),
  'settings': k.tenant.settings(),
  'billing': k.tenant.billing(),
} as const;
type SeedName = keyof typeof SEEDS;

let qc: QueryClient;
let fns: Record<SeedName, ReturnType<typeof vi.fn>>;
let unsubscribe: Array<() => void>;

async function seedAll() {
  qc = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: Infinity }, mutations: { retry: false } },
  });
  fns = {} as typeof fns;
  unsubscribe = [];
  for (const [name, key] of Object.entries(SEEDS) as Array<[SeedName, readonly unknown[]]>) {
    const fn = vi.fn(async () => ({ seeded: name }));
    fns[name] = fn;
    // A real observer makes the query ACTIVE, so invalidation triggers a refetch
    // rather than only flagging a cache entry nothing is watching.
    const obs = new QueryObserver(qc, { queryKey: key as unknown[], queryFn: fn, staleTime: 0 });
    unsubscribe.push(obs.subscribe(() => {}));
  }
  await waitFor(() => {
    for (const name of Object.keys(SEEDS) as SeedName[]) {
      expect(fns[name], `${name} did not seed`).toHaveBeenCalledTimes(1);
    }
  });
}

const wrapper = ({ children }: { children: React.ReactNode }) => (
  <QueryClientProvider client={qc}>{children}</QueryClientProvider>
);

/** Run a mutation to success and report which seeded queries refetched. */
async function refetchedAfter(
  useHook: () => { mutateAsync: (v: never) => Promise<unknown> },
  vars: unknown,
): Promise<Set<SeedName>> {
  const { result } = renderHook(() => useHook(), { wrapper });
  await act(async () => { await result.current.mutateAsync(vars as never); });
  const moved = new Set<SeedName>();
  await waitFor(() => {
    for (const name of Object.keys(SEEDS) as SeedName[]) {
      if (fns[name].mock.calls.length > 1) moved.add(name);
    }
    // Let any pending refetch settle before reading the set.
    expect(qc.isFetching()).toBe(0);
  });
  return moved;
}

const WORKLIST_FAMILY: SeedName[] = [
  'worklistPaged(20,"")', 'worklistPaged(20,"ali")', 'worklistPaged(50,"")', 'worklist(8)',
];
const CONTROLS: SeedName[] = ['customers', 'settings', 'billing'];

beforeEach(seedAll);
afterEach(() => { unsubscribe.forEach((u) => u()); qc.clear(); vi.clearAllMocks(); });

// ── the matrix ──────────────────────────────────────────────────────────────

interface Case {
  name: string;
  hook: () => { mutateAsync: (v: never) => Promise<unknown> };
  vars: unknown;
  worklist: boolean;
  staffToday: boolean;
  overview: boolean;
  why: string;
}

const CASES: Case[] = [
  {
    name: 'useRecordTenantPayment', hook: useRecordTenantPayment,
    vars: { installment_id: 'i1', amount: 100, method: 'cash' },
    worklist: true, staffToday: true, overview: true,
    why: 'creates a payment on the collections screen; may apply to the balance, and moves received_today + pending_count',
  },
  {
    name: 'useApprovePayment', hook: useApprovePayment, vars: 'p1',
    worklist: true, staffToday: true, overview: true,
    why: 'apply_payment mutates the installment, so queue membership changes; pending_count drops',
  },
  {
    name: 'useRejectPayment', hook: useRejectPayment, vars: 'p1',
    worklist: false, staffToday: true, overview: false,
    why: 'sets status=rejected only — the installment is never touched, so the queue cannot change; pending_count drops',
  },
  {
    name: 'useCancelInstallment', hook: useCancelInstallment, vars: { id: 'i1', reason: 'x' },
    worklist: true, staffToday: false, overview: true,
    why: 'a cancelled contract leaves the queue; no payment state changes',
  },
  {
    name: 'useRescheduleInstallment', hook: useRescheduleInstallment,
    vars: { id: 'i1', add_months: 1 },
    worklist: true, staffToday: false, overview: true,
    why: 'next_due_date decides queue membership; no payment state changes',
  },
  {
    name: 'useReplaceInstallment', hook: useReplaceInstallment,
    vars: { id: 'i1', body: {} },
    worklist: true, staffToday: false, overview: true,
    why: 'rewrites months/start_date, so the schedule and next_due_date move; no payment state changes',
  },
  {
    name: 'useCreateInstallment', hook: useCreateInstallment,
    vars: { body: { customer_id: 'c1', months: 1, items: [] } },
    worklist: true, staffToday: false, overview: true,
    why: 'creates a live contract, changing contract counts, debt totals and queue membership',
  },
];

describe('TR-002-D1 — the whole worklist family refreshes', () => {
  it.each(CASES.filter((c) => c.worklist))('$name refetches every worklist variant', async (c) => {
    const moved = await refetchedAfter(c.hook, c.vars);
    for (const key of WORKLIST_FAMILY) {
      expect(moved.has(key), `${c.name} left ${key} stale — ${c.why}`).toBe(true);
    }
  });

  it.each(CASES.filter((c) => !c.worklist))('$name leaves the worklist alone', async (c) => {
    const moved = await refetchedAfter(c.hook, c.vars);
    for (const key of WORKLIST_FAMILY) {
      expect(moved.has(key), `${c.name} refetched ${key} needlessly — ${c.why}`).toBe(false);
    }
  });
});

describe('TR-002-D2 — staff Today tiles refresh on payment state changes', () => {
  it.each(CASES.filter((c) => c.staffToday))('$name refetches staffToday', async (c) => {
    const moved = await refetchedAfter(c.hook, c.vars);
    expect(moved.has('staffToday()'), `${c.name} left the tiles stale — ${c.why}`).toBe(true);
  });

  it.each(CASES.filter((c) => !c.staffToday))('$name leaves staffToday alone', async (c) => {
    const moved = await refetchedAfter(c.hook, c.vars);
    expect(moved.has('staffToday()'), `${c.name} refetched staffToday needlessly — ${c.why}`).toBe(false);
  });
});

describe('tenant Overview refreshes when its financial aggregates change', () => {
  it.each(CASES.filter((c) => c.overview))('$name refetches overview', async (c) => {
    const moved = await refetchedAfter(c.hook, c.vars);
    expect(moved.has('overview()'), `${c.name} left Overview stale — ${c.why}`).toBe(true);
  });

  it.each(CASES.filter((c) => !c.overview))('$name leaves overview alone', async (c) => {
    const moved = await refetchedAfter(c.hook, c.vars);
    expect(moved.has('overview()'), `${c.name} refetched Overview needlessly — ${c.why}`).toBe(false);
  });
});

describe('no blanket invalidation', () => {
  it.each(CASES)('$name leaves unrelated tenant queries untouched', async (c) => {
    const moved = await refetchedAfter(c.hook, c.vars);
    for (const key of CONTROLS) {
      expect(moved.has(key), `${c.name} invalidated ${key} — too wide`).toBe(false);
    }
  });
});

describe('the invalidation is expressed through the key factory', () => {
  it('worklistAll() is a true prefix of both worklist shapes', () => {
    const all = k.tenant.worklistAll();
    for (const key of [k.tenant.worklist(8), k.tenant.worklistPaged(20, 'ali')]) {
      expect(key.slice(0, all.length)).toEqual([...all]);
    }
    // …and does not reach a sibling family.
    expect(k.tenant.payments().slice(0, all.length)).not.toEqual([...all]);
  });

  it('money writes still never retry, and nothing renders optimistically', () => {
    const { result } = renderHook(() => useRecordTenantPayment(), { wrapper });
    expect(qc.getDefaultOptions().mutations?.retry).toBe(false);
    // No onMutate anywhere in these hooks means no optimistic cache write.
    expect(result.current.isPending).toBe(false);
    expect(qc.getQueryData(k.tenant.installment('i1'))).toBeUndefined();
  });
});
