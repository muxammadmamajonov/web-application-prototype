// @vitest-environment jsdom
/**
 * P2-02 — the collections view must be driven by the worklist query.
 *
 * Two defects this pins:
 *
 *   * the due view rendered the ordinary installments query's loading, error,
 *     empty state, retry and count, so a *failed* worklist request looked like
 *     an empty book and Retry refetched the wrong endpoint;
 *   * overdue presentation was re-derived in the browser from `status`, which
 *     the nightly `mark_overdue` cron lags — a contract past due but not yet
 *     flipped rendered as "active" with no overdue treatment.
 */
import { describe, expect, it, beforeEach, vi } from 'vitest';
import { fireEvent, render, screen, cleanup, waitFor } from '@testing-library/react';
import i18n from '@/i18n';
import { isRowOverdue, overdueDays } from '@/lib/overdue';

// ── the two queries the screen can be driven by ─────────────────────────────
const listQ = { data: { items: [], total: 0 }, isLoading: false, isError: false, error: null, refetch: vi.fn() };
let workQ: Record<string, unknown>;
/** What each hook was last called with — the wiring under test in C3-03. */
const listCalls: Array<{ params: unknown; enabled: unknown }> = [];
const workCalls: Array<{ pageSize: unknown; q: unknown; enabled: unknown }> = [];
let STATUS = 'due';

vi.mock('@/store/session', () => ({
  useSession: (sel: (s: unknown) => unknown) => sel({ activeRole: () => 'owner' }),
}));
// The real hook returns a STABLE params object until the URL changes; handing
// back a fresh one each render would re-fire the deep-link sync effect forever
// and pin the filter to the URL value.
const paramsCache = new Map<string, URLSearchParams>();
vi.mock('react-router', async (orig) => ({
  ...(await orig<Record<string, unknown>>()),
  useSearchParams: () => {
    if (!paramsCache.has(STATUS)) paramsCache.set(STATUS, new URLSearchParams(`status=${STATUS}`));
    return [paramsCache.get(STATUS)!, vi.fn()];
  },
}));
vi.mock('@/lib/queries', async (orig) => ({
  ...(await orig<Record<string, unknown>>()),
  useTenantInstallments: (params: unknown, enabled: unknown) => {
    listCalls.push({ params, enabled });
    return listQ;
  },
  useTenantWorklistPages: (pageSize: unknown, q: unknown, enabled: unknown) => {
    workCalls.push({ pageSize, q, enabled });
    return workQ;
  },
  useTenantInstallmentDetail: () => ({ data: undefined, isLoading: false }),
  // (MUDD-83) The page reads the catalog to name its category chips. These
  // tests render without a QueryClientProvider — they rely on every data hook
  // being stubbed — so an unmocked real `useQuery` throws before anything
  // about the collections view is exercised.
  useCatalogCategories: () => ({ data: [], isPending: false, isLoading: false, isError: false }),
}));
vi.mock('@/lib/mutations', async (orig) => ({
  ...(await orig<Record<string, unknown>>()),
  useRemindInstallment: () => ({ mutateAsync: vi.fn(), isPending: false }),
  useRecordTenantPayment: () => ({ mutateAsync: vi.fn(), isPending: false }),
  useCancelInstallment: () => ({ mutateAsync: vi.fn(), isPending: false }),
  useRescheduleInstallment: () => ({ mutateAsync: vi.fn(), isPending: false }),
}));

import TenantInstallmentsPage from './InstallmentsPage';

/** A contract the cron has not yet flipped: past due, still `active`. */
const cronLagRow = {
  id: 'c1', reference: 'R1', contract_number: 'MDD-1', customer_id: 'x',
  customer_name: 'Late Payer', customer_phone: null,
  status: 'active', is_overdue: true,
  total_amount: 6_000_000, down_payment: 0, principal: 6_000_000, markup_amount: 0,
  calc_formula: 'none', markup_pct: null, interest_rate: null,
  months: 6, monthly_amount: 1_000_000, paid_count: 0, paid_amount: 0,
  start_date: '2026-01-01', next_due_date: '2026-03-01',
  created_at: '2026-01-01T00:00:00Z',
};

const worklistPage = (over: Record<string, unknown> = {}) => ({
  data: {
    pages: [{
      items: [cronLagRow], total: 1, overdue_count: 1, due_today_count: 0,
      as_of: '2026-03-05T09:00:00Z', as_of_date: '2026-03-05', timezone: 'Asia/Tashkent',
    }],
  },
  isLoading: false, isError: false, error: null,
  refetch: vi.fn(), fetchNextPage: vi.fn(), hasNextPage: false, isFetchingNextPage: false,
  ...over,
});

beforeEach(async () => {
  cleanup();
  vi.clearAllMocks();
  listCalls.length = 0;
  workCalls.length = 0;
  STATUS = 'due';
  workQ = worklistPage();
  await i18n.changeLanguage('uz');
  if (!Element.prototype.scrollIntoView) Element.prototype.scrollIntoView = () => {};
});

describe('the due view is driven by the worklist query', () => {
  it('shows the worklist error, not the other query\'s empty state', async () => {
    // The ordinary list is healthy and empty; the worklist failed. Rendering the
    // list's state here is exactly the bug: a failure that reads as "no work".
    workQ = worklistPage({ isError: true, error: { response: { status: 500 } }, data: undefined });
    render(<TenantInstallmentsPage />);

    await waitFor(() => {
      expect(screen.getByRole('alert')).toBeTruthy();
    });
    expect(screen.getByRole('alert').textContent).toContain(i18n.t('errors.server'));
  });

  it('Retry refetches the worklist, never the installments list', async () => {
    const refetch = vi.fn();
    workQ = worklistPage({ isError: true, error: { response: { status: 500 } }, data: undefined, refetch });
    render(<TenantInstallmentsPage />);

    fireEvent.click(await screen.findByRole('button', { name: i18n.t('common.retry') }));
    expect(refetch).toHaveBeenCalledTimes(1);
    expect(listQ.refetch, 'the wrong endpoint must not be refetched').not.toHaveBeenCalled();
  });

  it('shows the worklist loading state, and no rows or error', () => {
    // The ordinary list is settled and empty; only the worklist is in flight.
    workQ = worklistPage({ isLoading: true, data: undefined });
    render(<TenantInstallmentsPage />);

    const busy = screen.getAllByRole('status').filter((n) => n.getAttribute('aria-busy') === 'true');
    expect(busy.length, 'the skeleton is rendered while the worklist loads').toBeGreaterThan(0);
    expect(screen.queryByRole('alert'), 'loading is not an error').toBeNull();
    expect(screen.queryAllByText(/Late Payer/)).toHaveLength(0);
  });

  it('counts the worklist total, not the list total', async () => {
    workQ = worklistPage();
    // The ordinary query reports 0; the worklist reports 1.
    render(<TenantInstallmentsPage />);
    await waitFor(() => {
      expect(screen.getAllByText(/Late Payer/).length).toBeGreaterThan(0);
    });
  });
});

describe('cron-lag rows are presented as overdue', () => {
  it('renders the overdue badge even though status is still active', async () => {
    render(<TenantInstallmentsPage />);
    await waitFor(() => {
      expect(screen.getAllByText(i18n.t('status.overdue')).length).toBeGreaterThan(0);
    });
    // The stale column value must not be what the user sees.
    expect(screen.queryAllByText(i18n.t('status.active'))).toHaveLength(0);
  });

  it('shows the overdue-days text for such a row', async () => {
    render(<TenantInstallmentsPage />);
    // 2026-03-01 due, tenant "today" 2026-03-05 ⇒ 4 days.
    await waitFor(() => {
      expect(screen.getAllByText(/4/).length).toBeGreaterThan(0);
    });
  });
});

describe('the shared classifier', () => {
  it('prefers the server verdict over the status column', () => {
    expect(isRowOverdue({ status: 'active', is_overdue: true })).toBe(true);
    expect(isRowOverdue({ status: 'overdue', is_overdue: false })).toBe(false);
  });

  it('falls back to status only when the server did not classify', () => {
    expect(isRowOverdue({ status: 'overdue' })).toBe(true);
    expect(isRowOverdue({ status: 'active' })).toBe(false);
  });

  it('counts days against the tenant-local reference date, not the browser clock', () => {
    const row = { status: 'active', is_overdue: true, next_due_date: '2026-03-01' };
    expect(overdueDays(row, '2026-03-05')).toBe(4);
    // A different tenant "today" gives a different answer for the same row —
    // which is the point: the browser's clock is not the authority.
    expect(overdueDays(row, '2026-03-10')).toBe(9);
  });

  it('reports zero days for a row that is not overdue', () => {
    expect(overdueDays({ status: 'active', is_overdue: false, next_due_date: '2026-03-01' }, '2026-03-05')).toBe(0);
  });
});

// ── C3-03: search must reach the query that produces the rows ───────────────

/** The search box, found by its shared placeholder. */
const searchBox = () => screen.getByPlaceholderText(
  i18n.t('installments.searchPh', { defaultValue: 'Shartnoma yoki mijoz bo‘yicha qidirish…' }),
);
const lastWork = () => workCalls[workCalls.length - 1];
const lastList = () => listCalls[listCalls.length - 1];

describe('search in the collections view', () => {
  it('sends the term to the worklist query, which is what renders the rows', async () => {
    render(<TenantInstallmentsPage />);
    fireEvent.change(searchBox(), { target: { value: 'Karimov' } });

    // Debounced, so it arrives shortly — but it must arrive on the WORKLIST.
    await waitFor(() => expect(lastWork().q).toBe('Karimov'));
  });

  it('does not fire the ordinary installments request while showing collections', async () => {
    render(<TenantInstallmentsPage />);
    fireEvent.change(searchBox(), { target: { value: 'Karimov' } });
    await waitFor(() => expect(lastWork().q).toBe('Karimov'));
    // Searching a list the screen is not rendering is the whole defect.
    expect(lastList().enabled, 'the installments query must stay disabled in due mode').toBe(false);
    expect(lastWork().enabled).toBe(true);
  });

  it('clearing the box restores the unfiltered worklist', async () => {
    render(<TenantInstallmentsPage />);
    fireEvent.change(searchBox(), { target: { value: 'Karimov' } });
    await waitFor(() => expect(lastWork().q).toBe('Karimov'));

    fireEvent.change(searchBox(), { target: { value: '' } });
    await waitFor(() => expect(lastWork().q).toBe(''));
  });

  it('trims a whitespace-only term rather than searching for spaces', async () => {
    render(<TenantInstallmentsPage />);
    fireEvent.change(searchBox(), { target: { value: '   ' } });
    await waitFor(() => expect(lastWork().q).toBe(''));
  });

  it('keeps paging on the worklist while a term is active', async () => {
    const fetchNextPage = vi.fn();
    workQ = worklistPage({ hasNextPage: true, fetchNextPage });
    render(<TenantInstallmentsPage />);
    fireEvent.change(searchBox(), { target: { value: 'Karimov' } });
    await waitFor(() => expect(lastWork().q).toBe('Karimov'));

    fireEvent.click(screen.getByRole('button', { name: new RegExp(i18n.t('common.loadMore', { shown: 1, total: 1 }).split('(')[0].trim()) }));
    expect(fetchNextPage).toHaveBeenCalled();
  });
});

describe('outside the collections view the wiring is reversed', () => {
  beforeEach(() => { STATUS = 'all'; });

  it('the ordinary list runs and the worklist does not', async () => {
    render(<TenantInstallmentsPage />);
    await waitFor(() => expect(lastList().enabled).toBe(true));
    expect(lastWork().enabled, 'the worklist must not be fetched off the due view').toBe(false);
  });

  it('search goes to the ordinary list there', async () => {
    render(<TenantInstallmentsPage />);
    fireEvent.change(searchBox(), { target: { value: 'Karimov' } });
    await waitFor(() =>
      expect((lastList().params as { q?: string } | undefined)?.q).toBe('Karimov'));
  });
});

// ── F4-02: the three distinct empty states ──────────────────────────────────

/** An empty page from whichever query is driving the view. */
const emptyWorklist = () => worklistPage({
  data: { pages: [{ items: [], total: 0, overdue_count: 0, due_today_count: 0,
    as_of: '2026-03-05T09:00:00Z', as_of_date: '2026-03-05', timezone: 'Asia/Tashkent' }] },
});

async function searchFor(term: string) {
  fireEvent.change(searchBox(), { target: { value: term } });
  await waitFor(() => expect(lastWork().q).toBe(term));
}

describe('an empty collections queue is good news, not "no contracts"', () => {
  beforeEach(() => { workQ = emptyWorklist(); });

  it('says there is nothing to collect', async () => {
    render(<TenantInstallmentsPage />);
    await waitFor(() =>
      expect(screen.getByText(i18n.t('installments.emptyDueTitle'))).toBeTruthy());
    expect(screen.getByText(i18n.t('installments.emptyDueBody'))).toBeTruthy();
  });

  it('does not claim the shop has no contracts', async () => {
    render(<TenantInstallmentsPage />);
    await waitFor(() => expect(screen.getByText(i18n.t('installments.emptyDueTitle'))).toBeTruthy());
    expect(screen.queryByText(i18n.t('installments.emptyTitle'))).toBeNull();
  });

  it('offers no clear action — there is nothing to clear', async () => {
    render(<TenantInstallmentsPage />);
    await waitFor(() => expect(screen.getByText(i18n.t('installments.emptyDueTitle'))).toBeTruthy());
    expect(screen.queryByRole('button', { name: i18n.t('common.clear') })).toBeNull();
    expect(screen.queryByRole('button', { name: i18n.t('common.clearSearch') })).toBeNull();
  });
});

describe('a search with no matches keeps the user in collections', () => {
  beforeEach(() => { workQ = emptyWorklist(); });

  it('shows the localized no-results state', async () => {
    render(<TenantInstallmentsPage />);
    await searchFor('Nobody');
    await waitFor(() => expect(screen.getByText(i18n.t('common.noResults'))).toBeTruthy());
  });

  it('never renders the raw status.due key', async () => {
    render(<TenantInstallmentsPage />);
    await searchFor('Nobody');
    await waitFor(() => expect(screen.getByText(i18n.t('common.noResults'))).toBeTruthy());
    // The old code built t(`status.${filter}`) → an unmapped key rendered verbatim.
    expect(document.body.textContent).not.toContain('status.due');
  });

  it('its recovery action clears the SEARCH, not the view', async () => {
    render(<TenantInstallmentsPage />);
    await searchFor('Nobody');
    const clear = await screen.findByRole('button', { name: i18n.t('common.clearSearch') });
    fireEvent.click(clear);

    // The term is dropped…
    await waitFor(() => expect(lastWork().q).toBe(''));
    // …and the worklist is still the query driving the view, i.e. still collections.
    expect(lastWork().enabled).toBe(true);
    expect(lastList().enabled).toBe(false);
  });

  it('does not offer the filter-clearing action that would exit collections', async () => {
    render(<TenantInstallmentsPage />);
    await searchFor('Nobody');
    await waitFor(() => expect(screen.getByText(i18n.t('common.noResults'))).toBeTruthy());
    expect(screen.queryByRole('button', { name: i18n.t('common.clear') }),
      'clearing filters here would drop the user out of status=due').toBeNull();
  });

  it('clearing restores the unfiltered worklist rows', async () => {
    render(<TenantInstallmentsPage />);
    await searchFor('Nobody');
    fireEvent.click(await screen.findByRole('button', { name: i18n.t('common.clearSearch') }));
    await waitFor(() => expect(lastWork().q).toBe(''));

    // With rows back, the list renders again rather than an empty state.
    workQ = worklistPage();
    cleanup();
    render(<TenantInstallmentsPage />);
    await waitFor(() => expect(screen.getAllByText(/Late Payer/).length).toBeGreaterThan(0));
  });
});

describe('a non-search refinement clears only refinements', () => {
  it('outside collections, an excluding status filter names itself and clears', async () => {
    STATUS = 'completed';
    workQ = emptyWorklist();
    render(<TenantInstallmentsPage />);

    const clear = await screen.findByRole('button', { name: i18n.t('common.clear') });
    expect(screen.getByText(i18n.t('installments.emptyFiltered', { status: i18n.t('status.completed') }))).toBeTruthy();
    fireEvent.click(clear);
    // Back to the ordinary unfiltered list.
    await waitFor(() => expect(screen.getByText(i18n.t('installments.emptyTitle'))).toBeTruthy());
  });

  it('uses the collections label, never status.due, if a due label is ever needed', () => {
    // `status.due` is deliberately absent from both bundles; the collections
    // label is `installments.filterDue`.
    for (const lng of ['uz', 'ru'] as const) {
      const t = i18n.getFixedT(lng);
      expect(t('status.due'), `status.${lng} must stay unmapped`).toBe('status.due');
      expect(t('installments.filterDue')).not.toBe('installments.filterDue');
    }
  });
});
