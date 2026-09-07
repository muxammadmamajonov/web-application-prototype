import { useEffect, useMemo, useRef, useState, lazy, Suspense } from 'react';
import { useSearchParams } from 'react-router';
import { useTranslation } from 'react-i18next';
import { Ban, Banknote, Bell, CalendarClock, CheckCircle2, CreditCard, FileText, Loader2, Pencil, SearchX } from 'lucide-react';
import { PageHeader } from '@/components/ui/PageHeader';
import { FloatingCreateAction } from '@/components/ui/FloatingCreateAction';
import { Button } from '@/components/ui/Button';
import { StatusBadge } from '@/components/ui/Badge';
import { RowCard } from '@/components/ui/RowCard';
import { DataTable, type Column } from '@/components/ui/DataTable';
import { SkeletonList } from '@/components/ui/Skeleton';
import { ErrorCard } from '@/components/ui/ErrorCard';
import { useTenantInstallments, useTenantInstallmentDetail, useTenantWorklistPages, type InstallmentRow, type InstallmentDetail as InstallmentDetailType,
  useCatalogCategories,
} from '@/lib/queries';
import {
  useRemindInstallment, useRecordTenantPayment,
  useCancelInstallment, useRescheduleInstallment,
} from '@/lib/mutations';
import { Modal, Field, inputCls } from '@/components/ui/Modal';
import { Select } from '@/components/ui/Select';
import { SearchInput } from '@/components/ui/SearchInput';
import {
  FilterBar, FilterButton, FilterDrawer, RadioGroup, DateRangeGroup, RangeGroup,
  matchesDatePreset, matchesRange, rangeActive, type DatePreset,
} from '@/components/ui/FilterDrawer';
import { MoneyInput, Textarea } from '@/components/ui/FormFields';
import { DatePicker } from '@/components/ui/DatePicker';
import { ImageUploadField } from '@/components/ui/ImageUploadField';
import { useSession } from '@/store/session';
import { canManageTenant } from '@/store/persona';
import { V2_SURFACES_ENABLED } from '@/routes/_scope';
import { money, paymentMethodLabel, shortDate } from '@/lib/format';
import { rowCurrency, useCurrency } from '@/lib/currency';
import { isRowOverdue, overdueDays } from '@/lib/overdue';
import { remainingOf } from '@/lib/calc';
import { SegmentBar, MobileHeader } from '@/components/mobile/primitives';
import { confirmDialog, toast } from '@/lib/feedback';
import { apiErrorMessage } from '@/lib/errors';
import { openContractPdf } from '@/lib/contractPdf';
import { EmptyState } from '@/components/ui/EmptyState';
import { displayPhone } from '@/lib/phone';
import { useSubmitGuard } from '@/hooks/useSubmitGuard';

const InstallmentModal = lazy(() => import('./_modals/InstallmentModal').then(m => ({ default: m.InstallmentModal })));

// (H13) Stable per-submit idempotency key, same helper as InstallmentModal's
// `newIdemKey`: crypto.randomUUID in the browser (secure context), a
// timestamp+random fallback for the jsdom test env.
const newRecordIdemKey = (): string =>
  globalThis.crypto?.randomUUID?.() ?? `idem-${Date.now()}-${Math.random().toString(36).slice(2)}`;

/**
 * `due` is the collections view: everything that needs collecting today —
 * overdue AND due-today together. Not `overdue` alone, which would drop every
 * contract falling due today from the view its own queue previews.
 */
type StatusFilter = 'all' | 'due' | 'active' | 'overdue' | 'completed';
type SortKey = 'new' | 'due' | 'remaining' | 'overdue';

/** The method label lives in `@/lib/format` — one implementation shared with the
 *  payment ledger and the customer's own payment history, so no surface can
 *  drift back to `.toUpperCase()`-ing the raw enum. */
const methodLabel = paymentMethodLabel;

export default function TenantInstallmentsPage() {
  const { t, i18n } = useTranslation();
  const currency = useCurrency();
  const [search, setSearch] = useState('');
  const [debouncedSearch, setDebouncedSearch] = useState('');
  useEffect(() => {
    const id = setTimeout(() => setDebouncedSearch(search.trim()), 250);
    return () => clearTimeout(id);
  }, [search]);
  const [open, setOpen] = useState(false);
  const [searchParams, setSearchParams] = useSearchParams();
  const initialFilter = (['all', 'due', 'active', 'overdue', 'completed'] as const)
    .find((s) => s === searchParams.get('status')) ?? 'all';
  const [filter, setFilter] = useState<StatusFilter>(initialFilter);
  // (MUDD-83) Category lives SERVER-side, unlike the status/date/amount
  // refinements below. Those narrow the page that was already fetched; this one
  // cannot — a contract for a car may be on page 4, and filtering the current
  // page would report "no results" for data that exists.
  const [category, setCategory] = useState<string>('');
  const [datePreset, setDatePreset] = useState<DatePreset>('all');
  const [amountRange, setAmountRange] = useState({ min: '', max: '' });
  const [sort, setSort] = useState<SortKey>('new');
  const [filterOpen, setFilterOpen] = useState(false);
  const remind = useRemindInstallment();
  const [remindingId, setRemindingId] = useState<string | null>(null);
  // (#414) The open contract lives in the URL (?installment=<id>), matching the
  // SettingsShell ?tab= pattern: the detail survives refresh, is shareable, and
  // setSearchParams pushes a history entry so Back closes the modal instead of
  // leaving the list. Same setter name keeps the call sites unchanged.
  const detailId = searchParams.get('installment');
  // Set only by the post-creation hand-off, so a control labelled "record
  // a payment" lands the user on the payment form itself rather than on a detail
  // screen where the form is still one click away. Kept out of the URL: it is a
  // one-shot intent, not a shareable location.
  const [payForId, setPayForId] = useState<string | null>(null);
  const setDetailId = (id: string | null) => {
    const next = new URLSearchParams(searchParams);
    if (id) next.set('installment', id);
    else next.delete('installment');
    setSearchParams(next);
  };
  const [editing, setEditing] = useState<InstallmentDetailType | null>(null);

  // Deep-link from the Overview "needs attention" / KPI tap-throughs.
  useEffect(() => {
    const s = searchParams.get('status');
    if (s && (['due', 'active', 'overdue', 'completed'] as string[]).includes(s)) {
      setFilter(s as StatusFilter);
    }
  }, [searchParams]);

  // The collections view is served by the worklist endpoint, which classifies by
  // the TENANT's today and by due date rather than by the status column the
  // nightly job lags behind — and pages, so every row is reachable. Every other
  // filter keeps the ordinary list.

  // Both queries take the SAME debounced search term, and only the one backing
  // the current view runs: search has to reach whichever endpoint is actually
  // producing the rows, or the box filters a dataset nobody is looking at.
  const isDueView = filter === 'due';
  const query = useTenantInstallments(
    debouncedSearch || category
      ? { ...(debouncedSearch ? { q: debouncedSearch } : {}), ...(category ? { category } : {}) }
      : undefined,
    !isDueView,
  );
  const worklistQ = useTenantWorklistPages(20, debouncedSearch, isDueView);
  // No `business_type` argument, deliberately — see InstallmentModal. Used only
  // to turn a stored slug into a name; the chips themselves come from the
  // server's tenant-wide counts, so a category with no sales never appears.
  const categoriesQ = useCatalogCategories();
  const rows = useMemo<InstallmentRow[]>(
    () => (isDueView
      ? worklistQ.data?.pages.flatMap((p) => p.items) ?? []
      : query.data?.items ?? []),
    [isDueView, worklistQ.data, query.data],
  );
  const dueTotal = worklistQ.data?.pages[0]?.total ?? 0;
  // Tenant-local reference date for overdue-day arithmetic.
  const dueAsOf = worklistQ.data?.pages[0]?.as_of_date ?? null;

  // One source of truth for the view's state. The collections view is served by
  // a different endpoint, so its loading, error, retry, count and pagination
  // must all come from THAT query — otherwise a failed worklist request renders
  // the ordinary list's (successful) empty state and Retry refetches the wrong
  // thing.
  const activeQuery = isDueView ? worklistQ : query;
  const listIsLoading = activeQuery.isLoading;
  const listIsError = activeQuery.isError;
  const listError = activeQuery.error;
  const listRefetch = () => { void activeQuery.refetch(); };
  const listTotal = isDueView ? dueTotal : (query.data?.total ?? 0);

  const onRemind = async (i: InstallmentRow) => {
    setRemindingId(i.id);
    try {
      const r = await remind.mutateAsync(i.id);
      if (r.sent) toast.success(t('installments.remindSent', 'Eslatma Telegram orqali yuborildi'));
      else if (r.phone) {
        toast.info(t('installments.remindNoTg', 'Telegram ulanmagan — qo‘ng‘iroq qiling'));
        window.location.href = `tel:${r.phone}`;
      }
    } catch (ex) {
      toast.error(apiErrorMessage(ex));
    } finally {
      setRemindingId(null);
    }
  };

  // Built from the server's tenant-wide tallies, not from `rows`: a chip has to
  // say how many contracts it WOULD reveal, and counting the current page would
  // make every other chip read zero the moment one filter is on.
  const categoryCounts = query.data?.category_counts;
  const categoryOptions = useMemo(() => {
    const counts = categoryCounts ?? {};
    const label = (slug: string) => {
      const row = categoriesQ.data?.find((c) => c.slug === slug);
      if (!row) return slug;   // a slug whose catalog row was retired still has history
      return i18n.language.startsWith('ru') ? (row.name_ru || row.name_uz) : row.name_uz;
    };
    return [
      { value: '', label: t('common.all', 'Hammasi') },
      ...Object.entries(counts)
        .sort((a, b) => b[1] - a[1])
        .map(([slug, count]) => ({ value: slug, label: label(slug), count })),
    ];
  }, [categoryCounts, categoriesQ.data, i18n.language, t]);

  const counts: Record<StatusFilter, number> = {
    all: rows.length,
    // The collections count is the server's tenant-wide total, not a page tally.
    due: dueTotal,
    active: rows.filter((r) => r.status === 'active').length,
    overdue: rows.filter(isRowOverdue).length,
    completed: rows.filter((r) => r.status === 'completed').length,
  };
  const filteredRows = useMemo(() => (filter === 'all' || isDueView ? rows : rows.filter((r) => r.status === filter))
    .filter((r) => matchesDatePreset(r.start_date, datePreset))
    .filter((r) => matchesRange(remainingOf(r), amountRange))
    .slice()
    .sort((a, b) => {
      if (sort === 'remaining') return remainingOf(b) - remainingOf(a);
      if (sort === 'due') return (a.next_due_date ?? '9999').localeCompare(b.next_due_date ?? '9999');
      if (sort === 'overdue') {
        const ao = isRowOverdue(a) ? 1 : 0;
        const bo = isRowOverdue(b) ? 1 : 0;
        if (ao !== bo) return bo - ao;
        return remainingOf(b) - remainingOf(a);
      }
      return 0; // 'new' — keep server order (created desc)
    }), [rows, filter, isDueView, datePreset, amountRange, sort]);

  const activeFilters = (filter !== 'all' ? 1 : 0) + (datePreset !== 'all' ? 1 : 0)
    + (rangeActive(amountRange) ? 1 : 0) + (sort !== 'new' ? 1 : 0) + (category ? 1 : 0);

  /**
   * What the user can clear WITHOUT leaving the view they are in.
   *
   * In the collections view `status=due` is the route's own identity, not a
   * refinement the user chose — so an empty result there must not offer to
   * "clear filters" by resetting it, which silently drops the user out of
   * collections into the full contract list.
   */
  const statusIsRefinement = filter !== 'all' && !isDueView;
  const refinementCount = (statusIsRefinement ? 1 : 0) + (datePreset !== 'all' ? 1 : 0)
    + (rangeActive(amountRange) ? 1 : 0) + (sort !== 'new' ? 1 : 0) + (category ? 1 : 0);
  const clearRefinements = () => {
    if (statusIsRefinement) setFilter('all');
    setDatePreset('all'); setAmountRange({ min: '', max: '' }); setSort('new'); setCategory('');
  };
  /** A status label that always resolves — `status.due` does not exist. */
  const statusLabel = (f: StatusFilter) =>
    f === 'due' ? t('installments.filterDue', 'Yig‘ish kerak')
      : f === 'all' ? t('common.all', 'Hammasi')
        : t(`status.${f}`);

  // Desktop (md+) dense table columns. Mobile keeps the RowCard list below.
  // (H17) memoized against unrelated re-renders (search/filter state changes
  // elsewhere in this component).
  const cols: Column<InstallmentRow>[] = useMemo(() => [
    {
      key: 'customer', label: t('installments.colCustomer', { defaultValue: 'Mijoz' }), width: '1.5fr',
      render: (r) => <span className="block truncate font-medium">{r.customer_name ?? (r.contract_number ?? r.reference ?? r.id.slice(0, 8))}</span>,
      sortValue: (r) => (r.customer_name ?? '').toLowerCase(),
    },
    {
      key: 'contract', label: t('installments.colContract', { defaultValue: 'Shartnoma' }), width: '1fr',
      render: (r) => <span className="block truncate font-mono text-caption text-muted">{r.contract_number ?? r.reference ?? r.id.slice(0, 8)}</span>,
    },
    {
      key: 'status', label: t('installments.colStatus', 'Holat'), width: '116px',
      render: (r) => <StatusBadge status={isRowOverdue(r) ? 'overdue' : r.status} />,
      sortValue: (r) => (isRowOverdue(r) ? 'overdue' : r.status),
    },
    {
      key: 'progress', label: t('installments.colProgress', { defaultValue: "To'lov" }), width: '84px',
      render: (r) => <span className="tabular-nums text-small">{r.paid_count}/{r.months}</span>,
      sortValue: (r) => r.paid_count / Math.max(1, r.months),
    },
    {
      key: 'remaining', label: t('installments.remainingShort', 'qoldi'), width: '130px', align: 'right',
      render: (r) => <span className="tabular-nums">{money(remainingOf(r))}</span>, sortValue: (r) => remainingOf(r),
    },
    {
      key: 'due', label: t('installments.colDue', { defaultValue: 'Muddat' }), width: '124px', align: 'right',
      sortValue: (r) => r.next_due_date ?? '9999',
      render: (r) => {
        const od = overdueDays(r, dueAsOf);
        return (
          <span className="block">
            <span className="block tabular-nums text-caption text-muted">{shortDate(r.next_due_date)}</span>
            {od > 0 && (
              <span className="block text-micro font-medium text-[color:var(--c-danger-strong)]">
                {t('installments.overdueDays', { n: od, defaultValue: `${od} kun kechikkan` })}
              </span>
            )}
          </span>
        );
      },
    },
  ], [t, dueAsOf]);

  return (
    <>
      <div className="hidden md:block">
        <div className="flex items-center justify-between">
          <PageHeader
            title={t('nav.installments')}
            subtitle={t('installments.subtitle', { count: listTotal })}
          />
          {activeQuery.isFetching && !activeQuery.isLoading && (
            <span className="text-micro text-muted opacity-60 flex items-center gap-1">
              <Loader2 size={14} className="animate-spin" />
              {t('list.refetching')}
            </span>
          )}
        </div>
      </div>

      <div className="md:hidden">
        <MobileHeader title={t('nav.installments')} />
        <p className="mb-3 max-w-prose text-small leading-snug text-muted">
          {t('installments.mobileSub', { active: counts.active, overdue: counts.overdue, defaultValue: '{{active}} faol · {{overdue}} o\'tgan' })}
        </p>
      </div>

      <FloatingCreateAction onClick={() => setOpen(true)} label={t('common.newInstallment')} />

      <FilterBar>
        <SearchInput value={search} onChange={setSearch} className="flex-1"
          placeholder={t('installments.searchPh', { defaultValue: 'Shartnoma yoki mijoz bo‘yicha qidirish…' })} />
        <FilterButton count={activeFilters} onClick={() => setFilterOpen(true)} />
      </FilterBar>

      <FilterDrawer open={filterOpen} onClose={() => setFilterOpen(false)}
        onClear={() => { setFilter('all'); setDatePreset('all'); setAmountRange({ min: '', max: '' }); setSort('new'); setCategory(''); }}>
        <RadioGroup<StatusFilter>
          label={t('installments.colStatus', 'Holat')}
          value={filter}
          onChange={setFilter}
          options={[
            { value: 'all', label: t('common.all', 'Hammasi'), count: counts.all },
            { value: 'due', label: t('installments.filterDue', 'Yig‘ish kerak'), count: counts.due },
            { value: 'active', label: t('status.active'), count: counts.active },
            { value: 'overdue', label: t('status.overdue'), count: counts.overdue },
            { value: 'completed', label: t('status.completed'), count: counts.completed },
          ]}
        />
        {categoryOptions.length > 1 && (
          <RadioGroup<string>
            label={t('installments.filterCategory', 'Toifa')}
            value={category}
            onChange={setCategory}
            options={categoryOptions}
          />
        )}
        <DateRangeGroup value={datePreset} onChange={setDatePreset} />
        <RangeGroup
          label={t('installments.remainingShort', 'Qoldiq')}
          unit={currency}
          value={amountRange}
          onChange={setAmountRange}
        />
        <RadioGroup<SortKey>
          label={t('common.sort', 'Saralash')}
          value={sort}
          onChange={setSort}
          options={[
            { value: 'new', label: t('installments.sortNew', 'Eng yangi') },
            { value: 'due', label: t('installments.sortDue', 'To‘lov muddati') },
            { value: 'overdue', label: t('installments.sortOverdue', 'Kechikkanlar') },
            { value: 'remaining', label: t('installments.sortRemaining', 'Qoldiq (katta)') },
          ]}
        />
      </FilterDrawer>

      {listIsLoading ? (
        <SkeletonList rows={6} />
      ) : listIsError ? (
        <ErrorCard error={listError} onRetry={listRefetch} />
      ) : filteredRows.length === 0 ? (
        debouncedSearch ? (
          /* A search that matched nothing. The only sensible recovery is to
             clear the SEARCH — clearing the view's status would answer a
             question the user did not ask and move them out of collections. */
          <EmptyState
            size="compact"
            icon={<SearchX size={20} />}
            title={t('common.noResults', 'Topilmadi')}
            body={isDueView
              ? t('installments.emptyDueSearchHint', 'Qidiruv faqat yigʻish roʻyxati boʻyicha ishlaydi.')
              : t('installments.emptySearchHint', 'Boshqa ism, telefon yoki shartnoma raqamini kiriting.')}
            action={
              <Button variant="outline" size="md" onClick={() => setSearch('')}>
                {t('common.clearSearch', 'Qidiruvni tozalash')}
              </Button>
            }
          />
        ) : refinementCount > 0 ? (
          /* (#456) A refinement excluded everything. Name it and offer a
             one-tap clear, so this reads as "the filter excluded everything"
             rather than "the KPI lied". */
          <EmptyState
            size="compact"
            icon={<CreditCard size={20} />}
            title={statusIsRefinement
              ? t('installments.emptyFiltered', { status: statusLabel(filter), defaultValue: '"{{status}}" bo‘yicha shartnoma topilmadi' })
              : t('common.noResults', 'Topilmadi')}
            body={t('installments.emptyFilteredHint', 'Filtrlarni tozalab, barcha shartnomalarni ko‘ring')}
            action={
              <Button variant="outline" size="md" onClick={clearRefinements}>
                {t('common.clear', 'Tozalash')}
              </Button>
            }
          />
        ) : isDueView ? (
          /* Nothing to collect is good news, not "you have no contracts". */
          <EmptyState
            size="compact"
            icon={<CheckCircle2 size={20} />}
            title={t('installments.emptyDueTitle', 'Bugun yigʻish uchun hech narsa yoʻq')}
            body={t('installments.emptyDueBody', 'Muddati oʻtgan yoki bugun toʻlanishi kerak boʻlgan shartnoma yoʻq.')}
          />
        ) : (
          <EmptyState
            size="compact"
            icon={<CreditCard size={20} className="text-muted" />}
            title={t('installments.emptyTitle')}
            body={t('installments.emptyBody')}
          />
        )
      ) : (
        <>
          {/* Desktop (md+): dense, sortable, paginated table — full-width scannable.
              No isError/error/onRetry on the table: the page short-circuits on
              `listIsError` above and renders its own ErrorCard, so DataTable's
              error branch is unreachable from here. Passing them wired up a
              fallback that could never fire. */}
          <div className="hidden md:block">
            <DataTable<InstallmentRow>
              columns={cols}
              rows={filteredRows}
              rowKey={(r) => r.id}
              onRowClick={(r) => setDetailId(r.id)}
              pageSize={25}
            />
          </div>
          {/* Mobile (<md): rich cards (unchanged). */}
          <ul className="space-y-2 md:hidden">
            {filteredRows.map((i) => {
              const remaining = Math.max(0, i.principal - i.paid_amount);
              return (
                <li key={i.id}>
                  {/* Row opens detail on click; the kebab stopPropagations. The
                      wrapper is a div[role=button] (not a <button>) so the menu's
                      own buttons nest legally — RowCard renders a plain div when
                      it has no onClick/href. */}
                  <div
                    role="button"
                    tabIndex={0}
                    onClick={() => setDetailId(i.id)}
                    onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); setDetailId(i.id); } }}
                    className="cursor-pointer rounded-card focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ink/20"
                  >
                    <RowCard
                      initial={i.customer_name ?? i.contract_number ?? '#'}
                      title={i.customer_name ?? (i.contract_number ?? i.reference ?? i.id.slice(0, 8))}
                      subtitle={`${i.contract_number ?? i.reference ?? i.id.slice(0, 8)} · ${i.paid_count}/${i.months} · ${t('installments.remainingShort', 'qoldi')} ${money(remaining)}`}
                      leftBadge={<StatusBadge status={isRowOverdue(i) ? 'overdue' : i.status} />}
                      // The row IS the way to the contract; a menu whose only
                      // entry was "open this row" added a step and a second
                      // thing for a screen reader to walk past.
                      trailing={
                        <span className="hidden text-caption tabular-nums text-muted sm:block">{shortDate(i.next_due_date)}</span>
                      }
                    />
                  </div>
                </li>
              );
            })}
          </ul>
        </>
      )}

      {(open || !!editing) && (
        <Suspense fallback={<div className="text-center p-8">...</div>}>
          <InstallmentModal open={true} editing={editing}
                            onClose={() => { setOpen(false); setEditing(null); }}
                            onRecordPayment={(id) => { setDetailId(id); setPayForId(id); }} />
        </Suspense>
      )}
      {/* The collections view pages, so no due contract is unreachable. */}
      {isDueView && worklistQ.hasNextPage && (
        <div className="mt-3 text-center">
          <Button variant="outline" size="md"
                  loading={worklistQ.isFetchingNextPage}
                  onClick={() => void worklistQ.fetchNextPage()}>
            {worklistQ.isFetchingNextPage
              ? t('common.loading')
              : t('common.loadMore', {
                  shown: rows.length, total: dueTotal,
                  defaultValue: 'Yana yuklash ({{shown}}/{{total}})',
                })}
          </Button>
        </div>
      )}

      <InstallmentDetailModal id={detailId} openPayment={!!payForId && payForId === detailId}
                              onClose={() => { setDetailId(null); setPayForId(null); }}
                              onEdit={(d) => { setDetailId(null); setEditing(d); }}
                              onRemind={onRemind} remindingId={remindingId} />
    </>
  );
}

/** Read-only drill-in: schedule progress, line items, and payment history for
 *  one contract. Opened by tapping an installment card/row. */
export function InstallmentDetailModal({ id, onClose, onEdit, onRemind, remindingId, openPayment }: {
  id: string | null; onClose: () => void; onEdit?: (d: InstallmentDetailType) => void;
  onRemind?: (i: InstallmentRow) => void; remindingId?: string | null;
  /**
   * Open with the record-payment form already expanded, so a caller labelled
   * "record a payment" lands the user ON that form rather than a detail screen
   * where the form is still one click away.
   */
  openPayment?: boolean;
}) {
  const { t } = useTranslation();
  const currency = useCurrency();
  const q = useTenantInstallmentDetail(id);
  const d = q.data;
  const remaining = d ? Math.max(0, d.principal - d.paid_amount) : 0;
  const canRemind = !!onRemind && d && (d.status === 'overdue' || d.status === 'active');

  return (
    <Modal open={!!id} onClose={onClose}
           title={d ? (d.contract_number ?? d.reference ?? d.id.slice(0, 8)) : t('common.loading')}
           subtitle={d?.customer_name ?? undefined} size="lg"
           footer={d ? (
             <>
               {canRemind && (
                 <Button variant="outline" size="md" onClick={() => onRemind?.(d as unknown as InstallmentRow)} disabled={remindingId === d.id}>
                   {remindingId === d.id ? <Loader2 size={14} className="animate-spin" /> : <Bell size={14} />}
                   {t('installments.remind', 'Eslatish')}
                 </Button>
               )}
               <Button variant="primary" size="md" onClick={() => openContractPdf(d.id)}>
                 <FileText size={14} /> {t('installments.contractPdf', 'Shartnoma PDF')}
               </Button>
             </>
           ) : undefined}>
      {q.isLoading && <SkeletonList rows={4} />}
      {d && (
        <div className="space-y-5">
          <div className="flex items-center justify-between gap-3">
            <StatusBadge status={d.status} />
            <span className="font-mono text-caption text-muted">{shortDate(d.start_date)} → {shortDate(d.next_due_date)}</span>
          </div>

          <div className="grid grid-cols-3 gap-2 text-center">
            <Stat label={t('installments.colPrincipal', 'Asosiy')} value={`${money(d.principal)}`} />
            <Stat label={t('installments.colPaid', "To'langan")} value={`${money(d.paid_amount)}`} />
            <Stat label={t('installments.remainingShort', 'qoldi')} value={`${money(remaining)}`} />
          </div>

          <div>
            <SegmentBar paid={d.paid_count} total={d.months} tone={d.status === 'overdue' ? 'danger' : 'ink'} />
            <div className="mt-1.5 text-caption text-muted">
              {d.paid_count}/{d.months} · {money(d.monthly_amount)} {rowCurrency(d, currency)}/{t('installments.monthShort', 'oy')}
            </div>
          </div>

          {/* Items */}
          {d.items.length > 0 && (
            <section>
              <div className="mb-2 text-micro uppercase tracking-wider text-muted">{t('installments.colSchedule', 'Mahsulotlar')}</div>
              <ul className="divide-y divide-line rounded-control border border-line">
                {d.items.map((it) => (
                  <li key={it.id} className="flex items-center justify-between gap-3 px-3 py-2 text-body">
                    <span className="min-w-0">
                      <span className="block truncate font-medium">{it.product_name_snapshot}{it.variant_label ? ` · ${it.variant_label}` : ''}</span>
                      {it.serial_number && <span className="block font-mono text-micro text-muted">{it.serial_number}</span>}
                    </span>
                    <span className="shrink-0 tabular-nums">{it.qty}×{money(it.unit_price)}</span>
                  </li>
                ))}
              </ul>
            </section>
          )}

          {/* Payment history */}
          <section>
            <div className="mb-2 text-micro uppercase tracking-wider text-muted">{t('tenantPayments.historyTitle', "To'lovlar tarixi")}</div>
            {d.payments.length === 0 ? (
              <div className="rounded-control border border-dashed border-line px-3 py-4 text-center text-caption text-muted">
                {t('tenantPayments.noPayments', "Hali to'lov yo'q")}
              </div>
            ) : (
              <ul className="divide-y divide-line rounded-control border border-line">
                {d.payments.map((p) => (
                  <li key={p.id} className="flex items-center justify-between gap-3 px-3 py-2 text-body">
                    <span className="text-muted">{shortDate(p.paid_at)} · {methodLabel(t, p.method)}</span>
                    <span className="flex items-center gap-2">
                      <span className="tabular-nums">{money(p.amount)}</span>
                      <StatusBadge status={p.status} />
                    </span>
                  </li>
                ))}
              </ul>
            )}
          </section>

          {/* Guarantors */}
          {d.guarantors.length > 0 && (
            <section>
              <div className="mb-2 text-micro uppercase tracking-wider text-muted">{t('installments.guarantors', 'Kafillar')}</div>
              <ul className="space-y-1.5">
                {d.guarantors.map((g) => (
                  <li key={g.id} className="flex items-center justify-between rounded-control border border-line px-3 py-2 text-body">
                    <span className="truncate">{g.full_name}{g.relationship ? ` · ${g.relationship}` : ''}</span>
                    <a href={`tel:${g.phone}`} className="shrink-0 font-mono text-caption text-accent">{displayPhone(g.phone)}</a>
                  </li>
                ))}
              </ul>
            </section>
          )}

          {/* Record payment (owner/manager → approved, sales → pending) */}
          {d.status !== 'completed' && d.status !== 'cancelled' && (
            <RecordPaymentSection installmentId={d.id} monthly={d.monthly_amount}
                                  currency={rowCurrency(d, currency)}
                                  customerName={d.customer_name} contractNumber={d.contract_number}
                                  initiallyOpen={openPayment} />
          )}

          {/* Lifecycle (owner/manager): edit before any payment, reschedule, cancel */}
          <LifecycleSection d={d} onEdit={onEdit} onDone={onClose} />

        </div>
      )}
    </Modal>
  );
}

function LifecycleSection({ d, onEdit, onDone }: {
  d: InstallmentDetailType; onEdit?: (d: InstallmentDetailType) => void; onDone: () => void;
}) {
  const { t } = useTranslation();
  const activeRole = useSession((s) => s.activeRole());
  const canManage = canManageTenant(activeRole);
  const cancel = useCancelInstallment();
  const reschedule = useRescheduleInstallment();
  const [mode, setMode] = useState<'none' | 'reschedule' | 'cancel'>('none');
  const [addMonths, setAddMonths] = useState(0);
  const [newDue, setNewDue] = useState('');
  const [reason, setReason] = useState('');

  if (!canManage) return null;
  const open = d.status === 'active' || d.status === 'overdue';
  const editable = open && (d.paid_count ?? 0) === 0 && (d.paid_amount ?? 0) === 0;

  // Rescheduling rewrites every remaining due date, so restate current terms
  // against proposed terms before writing. Server-authoritative: nothing is
  // shown as done until the mutation resolves.
  const doReschedule = async () => {
    const nextMonths = d.months + addMonths;
    if (!(await confirmDialog({
      title: t('installments.rescheduleConfirmTitle', 'Jadvalni o‘zgartirishni tasdiqlang'),
      message: t('installments.rescheduleConfirmBody', {
        contract: d.contract_number ?? '—',
        name: d.customer_name ?? '',
        fromMonths: d.months,
        toMonths: nextMonths,
        fromDue: d.next_due_date ? shortDate(d.next_due_date) : '—',
        toDue: newDue ? shortDate(newDue) : (d.next_due_date ? shortDate(d.next_due_date) : '—'),
        defaultValue: '{{contract}} · {{name}}\nMuddat: {{fromMonths}} oy → {{toMonths}} oy\nKeyingi to‘lov: {{fromDue}} → {{toDue}}\nQolgan barcha to‘lov sanalari qayta hisoblanadi.',
      }),
      confirmLabel: t('installments.reschedule', 'Qayta rejalashtirish'),
    }))) return;
    try {
      await reschedule.mutateAsync({ id: d.id, add_months: addMonths || undefined, next_due_date: newDue || undefined });
      toast.success(t('installments.rescheduled', 'Jadval yangilandi'));
      setMode('none'); onDone();
    } catch (ex) {
      toast.error(apiErrorMessage(ex));   // localized; never the raw `detail`
    }
  };
  const doCancel = async () => {
    if (!(await confirmDialog({ message: t('installments.cancelConfirm', 'Bu shartnomani bekor qilasizmi?'), danger: true, confirmLabel: t('installments.cancel', 'Bekor qilish') }))) return;
    try {
      await cancel.mutateAsync({ id: d.id, reason: reason.trim() });
      toast.success(t('installments.cancelled', 'Bekor qilindi'));
      setMode('none'); onDone();
    } catch (ex) { toast.error(apiErrorMessage(ex)); }
  };

  if (!open) return null;

  return (
    <section className="rounded-card border border-line p-3">
      {mode === 'none' && (
        <div className="flex flex-wrap gap-2">
          {/* A contract exposes five actions: record a payment, remind, PDF,
              reschedule, cancel. Replacing terms wholesale is deferred; the
              retained way to change a live schedule is Reschedule, which
              restates old vs new terms before committing. */}
          {V2_SURFACES_ENABLED && editable && onEdit && (
            <Button variant="outline" size="sm" onClick={() => onEdit(d)}>
              <Pencil size={14} /> {t('common.edit')}
            </Button>
          )}
          <Button variant="outline" size="sm" onClick={() => { setAddMonths(0); setNewDue(''); setMode('reschedule'); }}>
            <CalendarClock size={14} /> {t('installments.reschedule', 'Qayta rejalashtirish')}
          </Button>
          <Button variant="outline" size="sm" className="border-danger/40 text-danger hover:bg-[color:var(--c-danger-soft)]"
                  onClick={() => { setReason(''); setMode('cancel'); }}>
            <Ban size={14} /> {t('installments.cancel', 'Bekor qilish')}
          </Button>
        </div>
      )}

      {mode === 'reschedule' && (
        <div className="grid gap-3">
          <Field label={t('installments.addMonths', 'Muddatga oy qo‘shish')}>
            <input type="number" min={0} max={60} className={inputCls} value={addMonths}
                   onChange={(e) => setAddMonths(Math.max(0, Math.min(60, Number(e.target.value || 0))))} />
          </Field>
          <Field label={t('installments.newNextDue', 'Yangi to‘lov sanasi (ixtiyoriy)')}>
            <DatePicker value={newDue} onChange={setNewDue} />
          </Field>
          <div className="flex gap-2">
            <Button variant="primary" size="md" onClick={doReschedule} disabled={!addMonths && !newDue} loading={reschedule.isPending}>
              <CalendarClock size={14} />
              {t('common.save')}
            </Button>
            <Button variant="outline" size="md" onClick={() => setMode('none')}>{t('common.cancel')}</Button>
          </div>
        </div>
      )}

      {mode === 'cancel' && (
        <div className="grid gap-3">
          <Field label={t('installments.cancelReason', 'Bekor qilish sababi')}>
            <Textarea rows={2} maxLength={500} value={reason}
                      onChange={setReason}
                      placeholder={t('installments.cancelReasonPh', 'Masalan: mijoz qaytarib berdi') ?? ''} />
          </Field>
          <div className="flex gap-2">
            <Button variant="danger" size="md" onClick={doCancel} loading={cancel.isPending}>
              <Ban size={14} />
              {t('installments.cancel', 'Bekor qilish')}
            </Button>
            <Button variant="outline" size="md" onClick={() => setMode('none')}>{t('common.cancel')}</Button>
          </div>
        </div>
      )}
    </section>
  );
}

function RecordPaymentSection({ installmentId, monthly, currency, customerName, contractNumber, initiallyOpen }: {
  installmentId: string; monthly: number;
  /** The CONTRACT's currency, passed down rather than re-read from the
   *  shop: a payment is denominated in the contract it lands on, and the server
   *  enforces exactly that. */
  currency: string;
  customerName?: string | null; contractNumber?: string | null;
  initiallyOpen?: boolean;
}) {
  const { t } = useTranslation();
  const activeRole = useSession((s) => s.activeRole());
  const canApprove = canManageTenant(activeRole);
  const record = useRecordTenantPayment();
  const [open, setOpen] = useState(!!initiallyOpen);
  const [amount, setAmount] = useState(monthly);
  const [method, setMethod] = useState('cash');
  const [receipt, setReceipt] = useState('');
  // (H13) Synchronous one-shot guard. This section previously had NONE — only
  // `record.isPending`, which is react-query mutation state and only flips
  // `true` once `mutateAsync` has already started. The confirmDialog await
  // (and the render gap after a fast tap) was completely unguarded. `run`
  // flips its ref before the first `await`, so two activations dispatched in
  // the same frame cannot both pass — mirrors PaymentReviewPage's
  // `useSubmitGuard` for the same reason: this records a real payment.
  const { pending: submitting, run: runSubmit } = useSubmitGuard();
  // (H13) Per-submit idempotency key — the backstop `useCreateInstallment`
  // already has (lib/mutations/tenant.ts): a duplicated `record-payment`
  // request becomes a server-side no-op instead of a second credit.
  // Regenerated whenever the form closes, so the next payment gets its own key.
  const idemKeyRef = useRef<string>(newRecordIdemKey());
  useEffect(() => {
    if (!open) idemKeyRef.current = newRecordIdemKey();
  }, [open]);

  // An owner/manager recording a payment marks it approved in the same call —
  // money reaches the contract immediately — so restate what is about to be
  // recorded before committing. A sales user's entry only queues for approval
  // and says so on success.
  const submit = () => runSubmit(async () => {
    if (amount <= 0) return;
    if (!(await confirmDialog({
      title: t('installments.recordConfirmTitle', "To'lovni tasdiqlang"),
      message: t('installments.recordConfirmBody', {
        amount: `${money(amount)} ${currency}`,
        name: customerName ?? '',
        contract: contractNumber ?? '—',
        defaultValue: '{{amount}} · {{name}} · {{contract}}',
      }),
      confirmLabel: canApprove
        ? t('installments.recordApprove', 'Qayd etish va tasdiqlash')
        : t('staffPayments.record', "To'lov qabul qilish"),
    }))) return;
    try {
      await record.mutateAsync({
        installment_id: installmentId,
        amount,
        method,
        receipt_image_url: receipt.trim() || null,
        mark_approved: canApprove,
        idempotencyKey: idemKeyRef.current,
      });
      toast.success(canApprove
        ? t('installments.paymentRecorded', "To'lov qayd etildi")
        : t('staffPayments.recordedPendingApproval', {
            amount: `${money(amount)} ${currency}`,
            defaultValue: "{{amount}} qabul qilindi. Do'kon egasi yoki menejer tasdiqlagach, qarz qoldig'i yangilanadi.",
          }));
      setOpen(false); setReceipt('');
    } catch (ex) {
      toast.error(apiErrorMessage(ex));
    }
  });

  if (!open) {
    return (
      <Button variant="primary" size="md" className="w-full" onClick={() => { setAmount(monthly); setOpen(true); }}>
        <Banknote size={16} /> {t('installments.recordPayment', "To'lov qayd etish")}
      </Button>
    );
  }
  return (
    <section className="rounded-card border border-line bg-cream p-4">
      <div className="mb-3 text-micro uppercase tracking-wider text-muted">{t('installments.recordPayment', "To'lov qayd etish")}</div>
      <div className="grid gap-3">
        <Field label={t('staffPayments.amount', 'Summa')} hint={currency}>
          <MoneyInput value={amount} onChange={setAmount} />
        </Field>
        <Field label={t('staffPayments.method', "To'lov usuli")}>
          <Select value={method} onChange={setMethod}
                  options={['cash', 'card', 'transfer', 'click', 'payme'].map((m) => ({ value: m, label: methodLabel(t, m) }))} />
        </Field>
        <Field label={t('staffPayments.receipt', 'Chek surati (ixtiyoriy)')}>
          <ImageUploadField value={receipt} onChange={setReceipt} />
        </Field>
        {!canApprove && (
          <p className="text-caption text-muted">{t('installments.recordPendingNote', "Bu to'lov egasi tasdig'iga yuboriladi.")}</p>
        )}
        <div className="flex gap-2">
          <Button variant="primary" size="md" onClick={() => void submit()} disabled={submitting || amount <= 0}>
            {submitting ? <Loader2 size={14} className="animate-spin" /> : <Banknote size={14} />}
            {canApprove ? t('installments.recordApprove', 'Qayd etish va tasdiqlash') : t('staffPayments.record', "To'lov qabul qilish")}
          </Button>
          <Button variant="outline" size="md" onClick={() => setOpen(false)}>{t('common.cancel')}</Button>
        </div>
      </div>
    </section>
  );
}

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-control bg-cream px-2 py-3">
      <div className="text-micro uppercase tracking-wider text-muted">{label}</div>
      <div className="mt-1 font-mono text-small font-semibold tabular-nums">{value}</div>
    </div>
  );
}

/** Fetches the contract PDF through the authed `api` client so the HttpOnly
 *  `muddato.session` cookie rides along (withCredentials). A plain <a href> would
 *  still work same-origin, but going through `api` keeps it consistent and lets
 *  axios attach the CSRF + tenant headers. Wraps the result in a blob URL and
 *  opens it in a new tab. The URL stays valid until the tab is closed; we revoke
 *  it from the parent only on full unmount (browser will GC otherwise).
 */
