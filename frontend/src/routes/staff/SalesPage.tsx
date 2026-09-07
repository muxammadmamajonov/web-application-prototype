import { useEffect, useMemo, useState } from 'react';
import { useSearchParams } from 'react-router';
import { useTranslation } from 'react-i18next';
import { CreditCard } from 'lucide-react';
import { PageHeader } from '@/components/ui/PageHeader';
import { FloatingCreateAction } from '@/components/ui/FloatingCreateAction';
import { Button } from '@/components/ui/Button';
import { StatusBadge } from '@/components/ui/Badge';
import { RowCard } from '@/components/ui/RowCard';
import { DataTable, type Column } from '@/components/ui/DataTable';
import { SkeletonList } from '@/components/ui/Skeleton';
import { ErrorCard } from '@/components/ui/ErrorCard';
import { EmptyState } from '@/components/ui/EmptyState';
import { SearchInput } from '@/components/ui/SearchInput';
import {
  FilterBar, FilterButton, FilterDrawer, RadioGroup, DateRangeGroup, RangeGroup,
  matchesDatePreset, matchesRange, rangeActive, type DatePreset,
} from '@/components/ui/FilterDrawer';
import { MobileHeader } from '@/components/mobile/primitives';
import { useTenantInstallments, type InstallmentRow } from '@/lib/queries';
import { money, shortDate } from '@/lib/format';
import { useCurrency } from '@/lib/currency';
import { remainingOf } from '@/lib/calc';
import { InstallmentModal } from '../tenant/_modals/InstallmentModal';
import { InstallmentDetailModal } from '../tenant/InstallmentsPage';

type StatusFilter = 'all' | 'active' | 'overdue' | 'completed';
type SortKey = 'new' | 'due' | 'remaining';

/** Staff (sales) — view installments + create new sales. Row → detail popup
 *  (record-payment is enabled for sales; lifecycle/delete stays owner-only). */
export default function StaffSalesPage() {
  const { t } = useTranslation();
  const currency = useCurrency();
  const query = useTenantInstallments();
  const rows = useMemo(() => query.data?.items ?? [], [query.data]);
  const [open, setOpen] = useState(false);
  const [filter, setFilter] = useState<StatusFilter>('all');
  const [datePreset, setDatePreset] = useState<DatePreset>('all');
  const [amountRange, setAmountRange] = useState({ min: '', max: '' });
  const [sort, setSort] = useState<SortKey>('new');
  const [filterOpen, setFilterOpen] = useState(false);
  // (MUDD-149) The open contract lives in the URL (?installment=<id>), same
  // convention as the tenant page (InstallmentsPage.tsx) — it's what a
  // `tinst_<id>` deep link now lands staff on (see PersonaGuard, router.tsx),
  // so the id has to survive here rather than being dropped on the floor.
  const [searchParams, setSearchParams] = useSearchParams();
  const detailId = searchParams.get('installment');
  const setDetailId = (id: string | null) => {
    const next = new URLSearchParams(searchParams);
    if (id) next.set('installment', id);
    else next.delete('installment');
    setSearchParams(next);
  };
  // Set only by the post-creation hand-off, so a control labelled
  // "record a payment" lands the user on the payment form itself.
  const [payForId, setPayForId] = useState<string | null>(null);
  const [q, setQ] = useState('');
  const [debouncedQ, setDebouncedQ] = useState('');
  useEffect(() => { const id = setTimeout(() => setDebouncedQ(q.trim()), 250); return () => clearTimeout(id); }, [q]);

  const counts: Record<StatusFilter, number> = {
    all: rows.length,
    active: rows.filter((r) => r.status === 'active').length,
    overdue: rows.filter((r) => r.status === 'overdue').length,
    completed: rows.filter((r) => r.status === 'completed').length,
  };
  const ql = debouncedQ.toLowerCase();
  const shown = useMemo(() => rows
    .filter((r) => filter === 'all' || r.status === filter)
    .filter((r) => matchesDatePreset(r.start_date, datePreset))
    .filter((r) => matchesRange(remainingOf(r), amountRange))
    .filter((r) => !ql
      || (r.customer_name ?? '').toLowerCase().includes(ql)
      || (r.contract_number ?? '').toLowerCase().includes(ql)
      || (r.reference ?? '').toLowerCase().includes(ql))
    .slice()
    .sort((a, b) => {
      if (sort === 'remaining') return remainingOf(b) - remainingOf(a);
      if (sort === 'due') return (a.next_due_date ?? '9999').localeCompare(b.next_due_date ?? '9999');
      return 0; // 'new' — keep server order (created desc)
    }), [rows, filter, datePreset, amountRange, ql, sort]);
  const activeFilters = (filter !== 'all' ? 1 : 0) + (datePreset !== 'all' ? 1 : 0)
    + (rangeActive(amountRange) ? 1 : 0) + (sort !== 'new' ? 1 : 0);
  const resetFilters = () => { setFilter('all'); setDatePreset('all'); setAmountRange({ min: '', max: '' }); setSort('new'); };
  // Desktop (md+) table columns; the mobile RowCard list (below) is kept verbatim. (H17)
  const cols: Column<InstallmentRow>[] = useMemo(() => {
    const contractRef = (i: InstallmentRow) => i.contract_number ?? i.reference ?? i.id.slice(0, 8);
    return [
    {
      key: 'customer', label: t('installments.colCustomer', 'Mijoz'), width: '1.6fr',
      render: (i) => <span className="block truncate font-medium">{i.customer_name ?? contractRef(i)}</span>,
      sortValue: (i) => (i.customer_name ?? contractRef(i)).toLowerCase(),
    },
    {
      key: 'contract', label: t('installments.colContract', 'Shartnoma'), width: '1.2fr',
      render: (i) => <span className="block truncate text-small text-muted">{contractRef(i)} · {i.paid_count}/{i.months}</span>,
    },
    {
      key: 'remaining', label: t('installments.remainingShort', 'Qoldiq'), width: '140px', align: 'right',
      render: (i) => <span className="font-semibold tabular-nums">{money(remainingOf(i))}</span>,
      sortValue: (i) => remainingOf(i),
    },
    {
      key: 'status', label: t('installments.colStatus', 'Holat'), width: '120px',
      render: (i) => <StatusBadge status={i.status} />, sortValue: (i) => i.status,
    },
    {
      key: 'due', label: t('installments.sortDue', 'To‘lov muddati'), width: '120px', align: 'right',
      render: (i) => <span className="tabular-nums text-small text-muted">{shortDate(i.next_due_date)}</span>,
      sortValue: (i) => i.next_due_date ?? '9999',
    },
    ];
  }, [t]);

  return (
    <>
      <div className="hidden md:block">
        <PageHeader
          title={t('nav.installments')}
          subtitle={t('installments.subtitle', { count: query.data?.total ?? 0 })}
        />
      </div>
      <div className="md:hidden">
        <MobileHeader title={t('nav.installments')} />
        <p className="mb-3 text-small leading-snug text-muted">{t('installments.subtitle', { count: query.data?.total ?? 0 })}</p>
      </div>

      <FloatingCreateAction onClick={() => setOpen(true)} label={t('common.newSale')} />

      <FilterBar>
        <SearchInput value={q} onChange={setQ} className="flex-1" placeholder={t('installments.searchPh', { defaultValue: 'Mijoz yoki shartnoma…' })} />
        <FilterButton count={activeFilters} onClick={() => setFilterOpen(true)} />
      </FilterBar>

      <FilterDrawer open={filterOpen} onClose={() => setFilterOpen(false)} onClear={resetFilters}>
        <RadioGroup<StatusFilter>
          label={t('installments.colStatus', 'Holat')}
          value={filter}
          onChange={setFilter}
          options={[
            { value: 'all', label: t('common.all', 'Hammasi'), count: counts.all },
            { value: 'active', label: t('status.active'), count: counts.active },
            { value: 'overdue', label: t('status.overdue'), count: counts.overdue },
            { value: 'completed', label: t('status.completed'), count: counts.completed },
          ]}
        />
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
            { value: 'remaining', label: t('installments.sortRemaining', 'Qoldiq (katta)') },
          ]}
        />
      </FilterDrawer>

      {query.isLoading ? (
        <SkeletonList rows={6} />
      ) : query.isError ? (
        <ErrorCard error={query.error} onRetry={() => query.refetch()} />
      ) : shown.length === 0 ? (
        activeFilters > 0 ? (
          <EmptyState
            size="compact"
            icon={<CreditCard size={20} />}
            title={t('common.noResults', 'Topilmadi')}
            body={t('installments.emptyFilteredHint', 'Filtrlarni tozalab, barcha shartnomalarni ko‘ring')}
            action={<Button variant="outline" size="md" onClick={resetFilters}>{t('common.clear', 'Tozalash')}</Button>}
          />
        ) : (
          <EmptyState
            size="compact"
            icon={<CreditCard size={20} />}
            title={debouncedQ ? t('common.noResults', 'Topilmadi') : t('installments.emptyTitle')}
          />
        )
      ) : (
        <>
          {/* Desktop (md+): dense sortable table that uses the wide screen. */}
          <div className="hidden md:block">
            <DataTable columns={cols} rows={shown} rowKey={(i) => i.id} onRowClick={(i) => setDetailId(i.id)} />
          </div>

          {/* Mobile: richer avatar RowCards (verbatim). */}
          <ul className="space-y-2 md:hidden">
            {shown.map((i) => {
              const remaining = Math.max(0, i.principal - i.paid_amount);
              return (
                <li key={i.id}>
                  <RowCard
                    initial={i.customer_name ?? i.contract_number ?? '#'}
                    title={i.customer_name ?? (i.contract_number ?? i.reference ?? i.id.slice(0, 8))}
                    subtitle={`${i.contract_number ?? i.reference ?? i.id.slice(0, 8)} · ${i.paid_count}/${i.months} · ${t('installments.remainingShort', 'qoldi')} ${money(remaining)}`}
                    leftBadge={<StatusBadge status={i.status} />}
                    trailing={<span className="hidden text-caption tabular-nums text-muted sm:block">{shortDate(i.next_due_date)}</span>}
                    onClick={() => setDetailId(i.id)}
                  />
                </li>
              );
            })}
          </ul>
        </>
      )}

      <InstallmentModal open={open} onClose={() => setOpen(false)}
                        onRecordPayment={(id) => { setDetailId(id); setPayForId(id); }} />
      <InstallmentDetailModal id={detailId} openPayment={!!payForId && payForId === detailId}
                              onClose={() => { setDetailId(null); setPayForId(null); }} />
    </>
  );
}
