import { useCallback, useEffect, useMemo, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Users } from 'lucide-react';
import { PageHeader } from '@/components/ui/PageHeader';
import { DesktopActionBar } from '@/components/layout/TopBar';
import { Button } from '@/components/ui/Button';
import { StatusBadge } from '@/components/ui/Badge';
import { RowCard } from '@/components/ui/RowCard';
import { DataTable, type Column } from '@/components/ui/DataTable';
import { SearchInput } from '@/components/ui/SearchInput';
import { SkeletonList } from '@/components/ui/Skeleton';
import { ErrorCard } from '@/components/ui/ErrorCard';
import { EmptyState } from '@/components/ui/EmptyState';
import {
  FilterBar, FilterButton, FilterDrawer, RadioGroup, DateRangeGroup, RangeGroup,
  matchesDatePreset, matchesRange, rangeActive, type DatePreset,
} from '@/components/ui/FilterDrawer';
import { DetailModal, DefList, DefRow } from '@/components/ui/DetailModal';
import { MobileHeader } from '@/components/mobile/primitives';
import { useTenantCustomers, type CustomerRow } from '@/lib/queries';
import { shortDate, money } from '@/lib/format';
import { displayPhone } from '@/lib/phone';

type StatusFilter = '' | 'pending' | 'active' | 'blocked';
type SortKey = 'new' | 'name' | 'income';

/** Staff (sales) — read-only customer lookup. Creating/approving/deleting is
 *  owner/manager-only on the backend, so the detail popup is view-only. */
export default function StaffCustomersPage() {
  const { t } = useTranslation();
  const [statusFilter, setStatusFilter] = useState<StatusFilter>('');
  const [datePreset, setDatePreset] = useState<DatePreset>('all');
  const [incomeRange, setIncomeRange] = useState({ min: '', max: '' });
  const [sort, setSort] = useState<SortKey>('new');
  const [filterOpen, setFilterOpen] = useState(false);
  const [detail, setDetail] = useState<CustomerRow | null>(null);
  const [q, setQ] = useState('');
  const [debouncedQ, setDebouncedQ] = useState('');
  useEffect(() => { const id = setTimeout(() => setDebouncedQ(q.trim()), 250); return () => clearTimeout(id); }, [q]);

  // Status is applied client-side (not via status_filter) so the drawer can show
  // a live per-status count over the same loaded set.
  const query = useTenantCustomers(debouncedQ ? { q: debouncedQ } : undefined);
  const serverRows = useMemo(() => query.data?.items ?? [], [query.data]);
  const counts: Record<Exclude<StatusFilter, ''> | 'all', number> = {
    all: serverRows.length,
    pending: serverRows.filter((c) => c.status === 'pending').length,
    active: serverRows.filter((c) => c.status === 'active').length,
    blocked: serverRows.filter((c) => c.status === 'blocked').length,
  };
  const rows = useMemo(() => serverRows
    .filter((c) => (statusFilter ? c.status === statusFilter : true))
    .filter((c) => matchesDatePreset(c.created_at, datePreset))
    .filter((c) => matchesRange(c.monthly_income, incomeRange))
    .slice()
    .sort((a, b) => {
      if (sort === 'name') return a.full_name.localeCompare(b.full_name);
      if (sort === 'income') return (b.monthly_income ?? 0) - (a.monthly_income ?? 0);
      return 0; // 'new' — keep server order (created desc)
    }), [serverRows, statusFilter, datePreset, incomeRange, sort]);
  const activeFilters = (statusFilter ? 1 : 0) + (datePreset !== 'all' ? 1 : 0)
    + (rangeActive(incomeRange) ? 1 : 0) + (sort !== 'new' ? 1 : 0);
  const resetFilters = () => { setStatusFilter(''); setDatePreset('all'); setIncomeRange({ min: '', max: '' }); setSort('new'); };
  // (H17) stable across renders (pure function of its argument) so the `cols`
  // useMemo below doesn't rebuild every render.
  const passport = useCallback((c: CustomerRow) => [c.passport_series, c.passport_number].filter(Boolean).join(' ') || c.passport_id || null, []);

  // Desktop (md+) table columns; the mobile RowCard list (below) is kept verbatim. (H17)
  const cols: Column<CustomerRow>[] = useMemo(() => [
    {
      key: 'name', label: t('customers.fullName', 'F.I.Sh.'), width: '1.6fr',
      render: (c) => <span className="block truncate font-medium">{c.full_name}</span>,
      sortValue: (c) => c.full_name.toLowerCase(),
    },
    {
      key: 'phone', label: t('customers.phone', 'Telefon'), width: '1.3fr',
      render: (c) => <span className="block truncate text-small text-muted">{[displayPhone(c.phone), passport(c), c.jshshir].filter(Boolean).join(' · ') || '—'}</span>,
    },
    {
      key: 'income', label: t('customers.monthlyIncome', 'Oylik daromad'), width: '140px', align: 'right',
      render: (c) => <span className="tabular-nums text-small">{c.monthly_income ? money(c.monthly_income) : '—'}</span>,
      sortValue: (c) => c.monthly_income ?? 0,
    },
    {
      key: 'status', label: t('customers.colStatus', 'Holat'), width: '120px',
      render: (c) => <StatusBadge status={c.status} />, sortValue: (c) => c.status,
    },
    {
      key: 'created', label: t('customers.colJoined', 'Qo‘shildi'), width: '110px', align: 'right',
      render: (c) => <span className="tabular-nums text-small text-muted">{shortDate(c.created_at)}</span>,
      sortValue: (c) => c.created_at,
    },
  ], [t, passport]);

  return (
    <>
      <div className="hidden md:block">
        <PageHeader
          title={t('nav.customers')}
          subtitle={`${t('customers.subtitle', { count: query.data?.total ?? 0 })} · ${t('staffCustomers.createHint', 'Yangi mijoz sotuv paytida qo‘shiladi — “Yangi sotuv” tugmasidan.')}`}
          actions={<DesktopActionBar />}
        />
      </div>
      <div className="md:hidden">
        <MobileHeader title={t('nav.customers')} />
        <p className="mb-1 text-small leading-snug text-muted">{t('customers.subtitle', { count: query.data?.total ?? 0 })}</p>
        <p className="mb-3 text-caption leading-snug text-muted">{t('staffCustomers.createHint', 'Yangi mijoz sotuv paytida qo‘shiladi — “Yangi sotuv” tugmasidan.')}</p>
      </div>

      <FilterBar>
        <SearchInput value={q} onChange={setQ} className="flex-1"
          placeholder={t('customers.searchPh', { defaultValue: 'Ism, telefon, passport yoki JSHSHIR…' })} />
        <FilterButton count={activeFilters} onClick={() => setFilterOpen(true)} />
      </FilterBar>

      <FilterDrawer open={filterOpen} onClose={() => setFilterOpen(false)} onClear={resetFilters}>
        <RadioGroup<StatusFilter>
          label={t('customers.colStatus', 'Holat')}
          value={statusFilter}
          onChange={setStatusFilter}
          options={[
            { value: '', label: t('common.all', 'Hammasi'), count: counts.all },
            { value: 'pending', label: t('status.pending', 'Kutilmoqda'), count: counts.pending },
            { value: 'active', label: t('status.active', 'Faol'), count: counts.active },
            { value: 'blocked', label: t('status.blocked', 'Bloklangan'), count: counts.blocked },
          ]}
        />
        <DateRangeGroup value={datePreset} onChange={setDatePreset} />
        <RangeGroup
          label={t('customers.monthlyIncome', 'Oylik daromad')}
          unit={t('common.sum', "so'm")}
          value={incomeRange}
          onChange={setIncomeRange}
        />
        <RadioGroup<SortKey>
          label={t('common.sort', 'Saralash')}
          value={sort}
          onChange={setSort}
          options={[
            { value: 'new', label: t('customers.sortNew', 'Eng yangi') },
            { value: 'name', label: t('customers.sortName', 'Ism (A→Z)') },
            { value: 'income', label: t('customers.sortIncome', 'Daromad (katta)') },
          ]}
        />
      </FilterDrawer>

      {query.isLoading ? (
        <SkeletonList rows={6} />
      ) : query.isError ? (
        <ErrorCard error={query.error} onRetry={() => query.refetch()} />
      ) : rows.length === 0 ? (
        activeFilters > 0 ? (
          <EmptyState
            size="compact"
            icon={<Users size={20} />}
            title={t('common.noResults', 'Topilmadi')}
            body={t('customers.emptyFilteredHint', 'Filtrlarni tozalab, barcha mijozlarni ko‘ring')}
            action={<Button variant="outline" size="md" onClick={resetFilters}>{t('common.clear', 'Tozalash')}</Button>}
          />
        ) : (
          <EmptyState
            size="compact"
            icon={<Users size={20} />}
            title={debouncedQ ? t('common.noResults', 'Topilmadi') : t('customers.emptyTitle')}
          />
        )
      ) : (
        <>
          {/* Desktop (md+): dense sortable table that uses the wide screen. */}
          <div className="hidden md:block">
            <DataTable columns={cols} rows={rows} rowKey={(c) => c.id} onRowClick={(c) => setDetail(c)} />
          </div>

          {/* Mobile: richer avatar RowCards (verbatim). */}
          <ul className="space-y-2 md:hidden">
            {rows.map((c) => (
              <li key={c.id}>
                <RowCard
                  initial={c.full_name}
                  title={c.full_name}
                  subtitle={[displayPhone(c.phone), passport(c), c.jshshir].filter(Boolean).join(' · ') || '—'}
                  leftBadge={<StatusBadge status={c.status} />}
                  trailing={<span className="hidden text-caption tabular-nums text-muted sm:block">{shortDate(c.created_at)}</span>}
                  onClick={() => setDetail(c)}
                />
              </li>
            ))}
          </ul>
        </>
      )}

      <DetailModal
        open={!!detail}
        onClose={() => setDetail(null)}
        title={detail?.full_name ?? ''}
        subtitle={detail ? <StatusBadge status={detail.status} /> : undefined}
      >
        {detail && (
          <DefList>
            {/* No "F.I.Sh." row — its value was the dialog title verbatim, so
                the row carried no information the header had not just given. */}
            <DefRow label={t('customers.phone', 'Telefon')}><a href={`tel:${detail.phone}`} className="text-accent hover:underline">{displayPhone(detail.phone)}</a></DefRow>
            <DefRow label={t('customers.secondaryPhone', "Qo'shimcha telefon")}>{displayPhone(detail.secondary_phone)}</DefRow>
            <DefRow label={t('customers.passport', 'Passport')}>{passport(detail)}</DefRow>
            <DefRow label="JSHSHIR">{detail.jshshir}</DefRow>
            <DefRow label={t('customers.address', 'Manzil')}>{detail.address}</DefRow>
            <DefRow label={t('customers.employmentPlace', 'Ish joyi')}>{detail.employment_place}</DefRow>
            <DefRow label={t('customers.monthlyIncome', 'Oylik daromad')}>{detail.monthly_income ? money(detail.monthly_income) : null}</DefRow>
            <DefRow label={t('customers.colJoined', 'Qo‘shildi')}>{shortDate(detail.created_at)}</DefRow>
          </DefList>
        )}
      </DetailModal>
    </>
  );
}
