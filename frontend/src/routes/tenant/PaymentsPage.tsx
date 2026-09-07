import { useCallback, useEffect, useMemo, useState } from 'react';
import { useSearchParams } from 'react-router';
import { useTranslation } from 'react-i18next';
import { Check, CheckCheck, ChevronRight, Eye, Wallet, X } from 'lucide-react';
import { PageHeader } from '@/components/ui/PageHeader';
import { Button } from '@/components/ui/Button';
import { StatusBadge } from '@/components/ui/Badge';
import { RowCard } from '@/components/ui/RowCard';
import { DataTable, type Column } from '@/components/ui/DataTable';
import { RowActionMenu, type RowAction } from '@/components/ui/RowActionMenu';
import { SkeletonList } from '@/components/ui/Skeleton';
import { ErrorCard } from '@/components/ui/ErrorCard';
import { SearchInput } from '@/components/ui/SearchInput';
import { MobileHeader } from '@/components/mobile/primitives';
import { StatusTabs } from '@/components/ui/StatusTabs';
import {
  FilterBar, FilterButton, FilterDrawer, RadioGroup, CheckGroup, DateRangeGroup, RangeGroup,
  matchesDatePreset, matchesRange, rangeActive, type DatePreset,
} from '@/components/ui/FilterDrawer';
import { DetailModal, DefList, DefRow } from '@/components/ui/DetailModal';
import { ReceiptImageFrame } from '@/components/ui/ReceiptImageFrame';
import { confirmDialog, toast } from '@/lib/feedback';
import { useTenantPaymentsPage, type PaymentRow } from '@/lib/queries';
import { useApprovePayment, useRejectPayment } from '@/lib/mutations';
import { apiErrorMessage } from '@/lib/errors';
import { V2_SURFACES_ENABLED } from '@/routes/_scope';
import { money, paymentMethodLabel, shortDate } from '@/lib/format';
import { rowCurrency, useCurrency } from '@/lib/currency';
import { useSubmitGuard } from '@/hooks/useSubmitGuard';
import { EmptyState } from '@/components/ui/EmptyState';

type StatusFilter = '' | 'pending' | 'approved' | 'rejected' | 'refunded';
type SortKey = 'new' | 'amountDesc' | 'amountAsc';
// The backend's hard cap (`le=200`) per page — large enough that a typical
// tenant's history for a given status fits in one page, same as the old
// single-shot fetch; "Yana yuklash" only appears past this for a genuinely
// long history (MUDD-84).
const PAYMENTS_PAGE_SIZE = 200;
const METHODS = ['cash', 'card', 'click', 'payme', 'transfer'] as const;

/** The method label lives in `@/lib/format` — one implementation shared with the
 *  contract detail and the customer's own payment history, so no surface can
 *  drift back to `.toUpperCase()`-ing the raw enum. */
const methodLabel = paymentMethodLabel;

/** Owner / manager — the payment approval queue + ledger. Staff submit payments
 *  as `pending`; the owner verifies the receipt and approves or rejects. The
 *  ledger is the source of truth, so it's read-only beyond approve/reject. */
export default function TenantPaymentsPage() {
  const { t } = useTranslation();
  const currency = useCurrency();
  const [searchParams, setSearchParams] = useSearchParams();

  // Read initial filter state from URL params
  const [statusFilter, setStatusFilterState] = useState<StatusFilter>(
    (searchParams.get('status') as StatusFilter | null) ?? ''
  );
  // (MUDD-84) Filtered ON THE SERVER and paged — so a status with more than
  // `PAYMENTS_PAGE_SIZE` rows of history stays fully reachable instead of
  // being sliced out of a single unfiltered fetch before the filter ever ran.
  const query = useTenantPaymentsPage(PAYMENTS_PAGE_SIZE, statusFilter);
  const allRows = useMemo(() => query.data?.pages.flatMap((p) => p.items) ?? [], [query.data]);
  const total = query.data?.pages[0]?.total ?? 0;
  const counts = query.data?.pages[0]?.counts;
  const [datePreset, setDatePresetState] = useState<DatePreset>(
    (searchParams.get('datePreset') as DatePreset | null) ?? 'all'
  );
  const [methods, setMethods] = useState<string[]>([]);
  const [amountRange, setAmountRange] = useState({ min: '', max: '' });
  const [sort, setSort] = useState<SortKey>('new');
  const [filterOpen, setFilterOpen] = useState(false);
  const [detail, setDetail] = useState<PaymentRow | null>(null);
  // (#432) Debounced client-side search over the loaded rows, consistent with
  // Customers/Installments/Products.
  const [q, setQState] = useState(searchParams.get('q') ?? '');
  const [debouncedQ, setDebouncedQ] = useState('');
  
  // Sync URL whenever filters/search change
  const setStatusFilter = (s: StatusFilter) => {
    setStatusFilterState(s);
    const next = new URLSearchParams(searchParams);
    if (s) next.set('status', s);
    else next.delete('status');
    setSearchParams(next);
  };
  
  const setDatePreset = (d: DatePreset) => {
    setDatePresetState(d);
    const next = new URLSearchParams(searchParams);
    if (d !== 'all') next.set('datePreset', d);
    else next.delete('datePreset');
    setSearchParams(next);
  };
  
  const setQ = (s: string) => {
    setQState(s);
    const next = new URLSearchParams(searchParams);
    if (s.trim()) next.set('q', s.trim());
    else next.delete('q');
    setSearchParams(next);
  };
  
  useEffect(() => { 
    const id = setTimeout(() => setDebouncedQ(q.trim().toLowerCase()), 250); 
    return () => clearTimeout(id); 
  }, [q]);

  const approve = useApprovePayment();
  const reject = useRejectPayment();
  // Synchronous one-shot guard so a double-tap on Approve/Reject can't fire two
  // mutations before react-query's isPending flips (it only flips post-async).
  const approveGuard = useSubmitGuard();
  const rejectGuard = useSubmitGuard();

  // The status filter already ran on the server (`allRows` is that status's
  // page), so this only applies the remaining client-side facets.
  const rows = useMemo(
    () => allRows
      .filter((p) => matchesDatePreset(p.created_at, datePreset))
      .filter((p) => (methods.length ? methods.includes(p.method) : true))
      .filter((p) => matchesRange(p.amount, amountRange))
      .filter((p) => (debouncedQ
        ? `${p.customer_name ?? ''} ${p.method} ${p.amount}`.toLowerCase().includes(debouncedQ)
        : true))
      .slice()
      .sort((a, b) => {
        if (sort === 'amountDesc') return b.amount - a.amount;
        if (sort === 'amountAsc') return a.amount - b.amount;
        return 0; // 'new' — keep server order (created desc)
      }),
    [allRows, datePreset, methods, amountRange, sort, debouncedQ],
  );
  // Real server-wide totals per status, independent of which tab is loaded —
  // not derived from the rows this page happened to fetch.
  const pendingCount = counts?.pending ?? 0;
  const methodCounts = useMemo(() => {
    const m: Record<string, number> = {};
    for (const p of allRows) m[p.method] = (m[p.method] ?? 0) + 1;
    return m;
  }, [allRows]);
  // Status lives in the visible StatusTabs row, not the drawer, so it is not
  // counted here — the drawer's badge reflects only the drawer's own filters.
  const activeFilters = (datePreset !== 'all' ? 1 : 0)
    + (methods.length ? 1 : 0) + (rangeActive(amountRange) ? 1 : 0) + (sort !== 'new' ? 1 : 0);
  const resetAll = () => { setStatusFilter(''); setDatePreset('all'); setMethods([]); setAmountRange({ min: '', max: '' }); setSort('new'); };
  // `total` is the CURRENT tab's filtered total; "Hammasi" needs the true
  // tenant-wide figure, which `counts` (always unfiltered) sums to.
  const allTotal = counts ? Object.values(counts).reduce((a, b) => a + b, 0) : undefined;
  const statusTabs: { value: StatusFilter; label: string; count?: number }[] = [
    { value: '', label: t('tenantPayments.filterAll', 'Hammasi'), count: allTotal },
    { value: 'pending', label: t('tenantPayments.filterPending', 'Tasdiqlash kutilmoqda'), count: counts?.pending },
    { value: 'approved', label: t('tenantPayments.filterApproved', 'Tasdiqlangan'), count: counts?.approved },
    { value: 'rejected', label: t('tenantPayments.filterRejected', 'Rad etilgan'), count: counts?.rejected },
    { value: 'refunded', label: t('tenantPayments.filterRefunded', 'Qaytarilgan'), count: counts?.refunded },
  ];

  // Approving applies money to a contract and changes what the customer owes, so
  // confirmation follows financial consequence: restate amount, customer and
  // contract before the write. The success message names the resulting contract
  // balance from the server's post-apply response — never optimistically.
  const onApprove = useCallback(async (p: PaymentRow) => {
    if (!(await confirmDialog({
      title: t('tenantPayments.approveConfirmTitle', { defaultValue: "To'lovni tasdiqlang" }),
      message: t('tenantPayments.approveConfirmBody', {
        amount: `${money(p.amount)} ${rowCurrency(p, currency)}`,
        name: p.customer_name ?? t('common.unknown', { defaultValue: 'Mijoz' }),
        contract: p.contract_number ?? '—',
        defaultValue: '{{amount}} · {{name}} · {{contract}} — shartnomaga hisoblanadi.',
      }),
      confirmLabel: t('tenantPayments.approve', { defaultValue: 'Tasdiqlash' }),
    }))) return;
    await approveGuard.run(async () => {
      try {
        const res = await approve.mutateAsync(p.id);
        setDetail(null);
        const remaining = res?.installment?.remaining;
        toast.success(remaining == null
          ? t('tenantPayments.approved', { defaultValue: "To'lov tasdiqlandi" })
          : t('tenantPayments.approvedWithBalance', {
              amount: `${money(p.amount)} ${rowCurrency(p, currency)}`,
              remaining: `${money(remaining)} ${rowCurrency(p, currency)}`,
              defaultValue: "{{amount}} tasdiqlandi. Qolgan qarz: {{remaining}}.",
            }));
      }
      catch (ex) { toast.error(apiErrorMessage(ex)); }
    });
  }, [t, currency, approveGuard, approve]);
  const onReject = useCallback(async (p: PaymentRow) => {
    if (!(await confirmDialog({
      message: t('tenantPayments.rejectConfirmBody', {
        amount: `${money(p.amount)} ${rowCurrency(p, currency)}`,
        name: p.customer_name ?? t('common.unknown', { defaultValue: 'Mijoz' }),
        contract: p.contract_number ?? '—',
        defaultValue: '{{amount}} · {{name}} · {{contract}} — rad etiladi. Mijozga xabar boradi.',
      }),
      danger: true, confirmLabel: t('tenantPayments.reject', { defaultValue: 'Rad etish' }),
    }))) return;
    await rejectGuard.run(async () => {
      try { await reject.mutateAsync(p.id); setDetail(null); }
      catch (ex) { toast.error(apiErrorMessage(ex)); }
    });
  }, [t, currency, rejectGuard, reject]);

  const { pending: bulkBusy, run: runBulk } = useSubmitGuard();
  const onApproveAll = async () => {
    const pend = allRows.filter((p) => p.status === 'pending');
    if (pend.length === 0) return;
    if (!(await confirmDialog({
      message: t('tenantPayments.approveAllConfirm', { count: pend.length, defaultValue: `{{count}} ta to'lovni tasdiqlaysizmi?` }),
      confirmLabel: t('tenantPayments.approve', { defaultValue: 'Tasdiqlash' }),
    }))) return;
    await runBulk(async () => {
      let ok = 0;
      for (const p of pend) {
        try { await approve.mutateAsync(p.id); ok += 1; } catch { /* keep going */ }
      }
      toast.success(t('tenantPayments.approvedN', { count: ok, defaultValue: `{{count}} ta tasdiqlandi` }));
    });
  };

  const subtitle = t('tenantPayments.subtitle', { count: total, defaultValue: "{{count}} ta to'lov" });


  // Desktop (md+) approval-queue table. Mobile keeps the RowCard list below.
  // (H17) memoized so an unrelated re-render (e.g. the debounced search box)
  // doesn't rebuild this array and defeat DataTable's own React.memo.
  // `rowActions` moved inline — its only caller — so its real dependencies
  // (the pending/loading flags) drive the memo directly.
  const cols: Column<PaymentRow>[] = useMemo(() => {
    const rowActions = (p: PaymentRow): RowAction[] => [
      { key: 'view', label: t('common.viewDetails', 'Batafsil'), icon: Eye, onClick: () => setDetail(p) },
      ...(p.status === 'pending' ? [
        { key: 'approve', label: t('tenantPayments.approve', { defaultValue: 'Tasdiqlash' }), icon: Check, onClick: () => onApprove(p), disabled: approve.isPending || approveGuard.pending },
        { key: 'reject', label: t('tenantPayments.reject', { defaultValue: 'Rad etish' }), icon: X, onClick: () => onReject(p), destructive: true, disabled: reject.isPending || rejectGuard.pending },
      ] : []),
    ];
    return [
    {
      key: 'customer', label: t('tenantPayments.colCustomer', 'Mijoz'), width: '1.5fr',
      sortValue: (p) => (p.customer_name ?? '').toLowerCase(),
      render: (p) => <span className="block truncate font-medium">{p.customer_name ?? '—'}</span>,
    },
    {
      key: 'method', label: t('tenantPayments.colMethod', 'Usul'), width: '110px',
      sortValue: (p) => p.method,
      render: (p) => <span className="text-small text-muted">{methodLabel(t, p.method)}</span>,
    },
    {
      key: 'date', label: t('tenantPayments.colDate', 'Sana'), width: '120px',
      sortValue: (p) => p.created_at ?? '',
      render: (p) => <span className="tabular-nums text-caption text-muted">{shortDate(p.created_at)}</span>,
    },
    {
      key: 'amount', label: t('tenantPayments.colAmount', 'Summa'), width: '140px', align: 'right',
      sortValue: (p) => p.amount,
      render: (p) => <span className="font-semibold tabular-nums">{money(p.amount)}</span>,
    },
    {
      key: 'status', label: t('tenantPayments.colStatus', 'Holat'), width: '116px',
      sortValue: (p) => p.status,
      render: (p) => <StatusBadge status={p.status} />,
    },
    {
      key: 'actions', label: '', width: '48px', align: 'right',
      // (§3) Pending rows get 2 real actions beyond "open detail" (approve/
      // reject) — those need the menu. Every other status only opens the
      // detail, which the row click already does, so show a plain chevron
      // instead of a kebab whose single entry duplicates the row click.
      render: (p) => (p.status === 'pending'
        ? <RowActionMenu actions={rowActions(p)} ariaLabel={t('common.actions', 'Amallar')} />
        : <ChevronRight size={16} className="text-muted" />),
    },
    ];
  }, [t, onApprove, onReject, approve.isPending, approveGuard.pending, reject.isPending, rejectGuard.pending]);

  return (
    <>
      <div className="hidden md:block">
        <PageHeader title={t('nav.payments')} subtitle={subtitle} />
      </div>
      <div className="md:hidden">
        <MobileHeader title={t('nav.payments')} />
        <p className="mb-3 max-w-prose text-small leading-snug text-muted">{subtitle}</p>
      </div>

      {/* First-class, always-visible status filter — the single source of truth
          for status; the drawer below no longer duplicates it (MUDD-84). */}
      <StatusTabs<StatusFilter> value={statusFilter} onChange={setStatusFilter} options={statusTabs} />

      <FilterBar>
        <SearchInput value={q} onChange={setQ} className="flex-1"
          placeholder={t('tenantPayments.searchPh', { defaultValue: 'Mijoz, usul yoki summa…' })} />
        <FilterButton count={activeFilters} onClick={() => setFilterOpen(true)} />
        {/* Bulk approve-all is deferred: it applies money across many contracts
            in one click and its loop swallowed per-item failures, so a partly
            failed run reported success. Payments are approved one at a time,
            each with its own confirmation and server-confirmed result. */}
        {V2_SURFACES_ENABLED && pendingCount > 0 && (
          <Button variant="primary" size="md" onClick={onApproveAll} loading={bulkBusy}
            className="bg-success text-white hover:brightness-110">
            {!bulkBusy && <CheckCheck size={16} />}
            {t('tenantPayments.approveAll', { count: pendingCount, defaultValue: `Hammasini tasdiqlash ({{count}})` })}
          </Button>
        )}
      </FilterBar>

      <FilterDrawer open={filterOpen} onClose={() => setFilterOpen(false)} onClear={resetAll}>
        <CheckGroup
          label={t('tenantPayments.colMethod', 'Usul')}
          value={methods}
          onChange={setMethods}
          options={METHODS.map((m) => ({
            value: m,
            label: `${methodLabel(t, m)}${methodCounts[m] ? ` · ${methodCounts[m]}` : ''}`,
          }))}
        />
        <DateRangeGroup value={datePreset} onChange={setDatePreset} />
        <RangeGroup
          label={t('tenantPayments.colAmount', 'Summa')}
          unit={currency}
          value={amountRange}
          onChange={setAmountRange}
          step={1000}
        />
        <RadioGroup<SortKey>
          label={t('common.sort', 'Saralash')}
          value={sort}
          onChange={setSort}
          options={[
            { value: 'new', label: t('tenantPayments.sortNew', 'Eng yangi') },
            { value: 'amountDesc', label: t('tenantPayments.sortAmountDesc', 'Summa (katta)') },
            { value: 'amountAsc', label: t('tenantPayments.sortAmountAsc', 'Summa (kichik)') },
          ]}
        />
      </FilterDrawer>

      {query.isLoading ? (
        <SkeletonList rows={6} />
      ) : query.isError ? (
        <ErrorCard error={query.error} onRetry={() => query.refetch()} />
      ) : rows.length === 0 && (activeFilters > 0 || debouncedQ || statusFilter) ? (
        // A selected status tab narrows the result set exactly like a drawer
        // filter does — zero rows here means "adjust what's selected", not
        // "this tenant has never taken a payment" (MUDD-84).
        <EmptyState
          size="compact"
          icon={<Wallet size={24} />}
          title={t('common.noResults', 'Topilmadi')}
          body={t('tenantPayments.noResultsBody', { defaultValue: "Qidiruv yoki filterlarni o'zgartirib ko'ring." })}
          action={<Button variant="outline" size="md" onClick={() => { resetAll(); setQ(''); }}>{t('common.clear', 'Tozalash')}</Button>}
        />
      ) : rows.length === 0 ? (
        <EmptyState
          size="compact"
          icon={<Wallet size={24} />}
          title={t('tenantPayments.emptyTitle', { defaultValue: "Hali to'lov yo'q" })}
          body={t('tenantPayments.emptyBody', { defaultValue: "Xodimlar qabul qilgan to'lovlar shu yerda tasdiqlash uchun ko'rinadi." })}
        />
      ) : (
        <>
          {/* Desktop (md+): dense approval queue — scannable, with inline
              approve/reject on pending rows; row click opens the receipt. */}
          <div className="hidden md:block">
            <DataTable<PaymentRow>
              columns={cols}
              rows={rows}
              isError={query.isError}
              error={query.error}
              onRetry={() => query.refetch()}
              rowKey={(p) => p.id}
              onRowClick={(p) => setDetail(p)}
              pageSize={25}
            />
          </div>
          {/* Mobile (<md): rich cards (unchanged). */}
          <ul className="space-y-2 md:hidden">
            {rows.map((p) => (
              <li key={p.id}>
                <RowCard
                  initial={p.customer_name ?? '#'}
                  title={p.customer_name ?? '—'}
                  subtitle={`${methodLabel(t, p.method)} · ${shortDate(p.created_at)}`}
                  leftBadge={<StatusBadge status={p.status} />}
                  trailing={<span className="text-body font-semibold tabular-nums">{money(p.amount)}</span>}
                  onClick={() => setDetail(p)}
                />
              </li>
            ))}
          </ul>
        </>
      )}

      {/* A status view can hold more than one page of history; without this
          the tenant's own oldest rejected/approved payments would be
          unreachable again, just one layer up (MUDD-84). */}
      {query.hasNextPage && (
        <div className="mt-3 text-center">
          <Button variant="outline" size="md"
                  loading={query.isFetchingNextPage}
                  onClick={() => void query.fetchNextPage()}>
            {query.isFetchingNextPage
              ? t('common.loading')
              : t('common.loadMore', {
                  shown: allRows.length, total,
                  defaultValue: 'Yana yuklash ({{shown}}/{{total}})',
                })}
          </Button>
        </div>
      )}

      <DetailModal
        open={!!detail}
        onClose={() => setDetail(null)}
        title={detail?.customer_name ?? t('nav.payments')}
        subtitle={detail ? <StatusBadge status={detail.status} /> : undefined}
        actions={detail?.status === 'pending' ? (
          <>
            <Button variant="outline" size="md" onClick={() => onReject(detail)} loading={reject.isPending || rejectGuard.pending}
              className="border-danger/40 text-danger hover:bg-[color:var(--c-danger-soft)]">
              <X size={16} /> {t('tenantPayments.reject', { defaultValue: 'Rad etish' })}
            </Button>
            <Button variant="primary" size="md" onClick={() => onApprove(detail)} loading={approve.isPending || approveGuard.pending}>
              <Check size={16} /> {t('tenantPayments.approve', { defaultValue: 'Tasdiqlash' })}
            </Button>
          </>
        ) : undefined}
      >
        {detail && (
          <div className="space-y-4">
            <DefList>
              <DefRow label={t('tenantPayments.colCustomer', 'Mijoz')}>{detail.customer_name}</DefRow>
              <DefRow label={t('tenantPayments.colAmount', 'Summa')}>{money(detail.amount)} {rowCurrency(detail, currency)}</DefRow>
              <DefRow label={t('tenantPayments.colMethod', 'Usul')}>{methodLabel(t, detail.method)}</DefRow>
              <DefRow label={t('tenantPayments.colDate', 'Sana')}>{shortDate(detail.created_at)}</DefRow>
              <DefRow label={t('tenantPayments.paidAt', "To'langan sana")}>{detail.paid_at ? shortDate(detail.paid_at) : null}</DefRow>
            </DefList>
            {detail.receipt_image_url && (
              <div>
                <div className="mb-2 text-micro uppercase tracking-wider text-muted">{t('tenantPayments.receipt', { defaultValue: 'Chek' })}</div>
                {/* (LX-07) Same reserved box as the review screen — an
                    uncapped `<img>` here re-flowed the drawer under the
                    reviewer mid-read. */}
                <ReceiptImageFrame
                  src={detail.receipt_image_url}
                  alt={t('tenantPayments.receipt', { defaultValue: 'Chek' })}
                />
                <a href={detail.receipt_image_url} target="_blank" rel="noopener" className="mt-2 inline-block text-caption font-medium text-accent underline">
                  {t('common.open', { defaultValue: 'Ochish' })}
                </a>
              </div>
            )}
          </div>
        )}
      </DetailModal>
    </>
  );
}
