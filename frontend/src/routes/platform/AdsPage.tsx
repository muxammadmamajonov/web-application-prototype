import { useEffect, useMemo, useState, type ReactNode } from 'react';
import { useTranslation } from 'react-i18next';
import {
  Ban, ChevronRight, Megaphone,
} from 'lucide-react';
import { MobileHeader } from '@/components/mobile/primitives';
import { PageHeader } from '@/components/ui/PageHeader';
import { FloatingCreateAction } from '@/components/ui/FloatingCreateAction';
import { Button } from '@/components/ui/Button';
import { Badge } from '@/components/ui/Badge';
import { EmptyState } from '@/components/ui/EmptyState';
import { ErrorCard } from '@/components/ui/ErrorCard';
import { DataTable, type Column } from '@/components/ui/DataTable';
import { SkeletonList } from '@/components/ui/Skeleton';
import { SearchInput } from '@/components/ui/SearchInput';
import { DetailModal, DefList, DefRow } from '@/components/ui/DetailModal';
import {
  FilterBar, FilterButton, FilterDrawer, ActiveFilters, RadioGroup, CheckGroup,
  DateRangeGroup, RangeGroup, matchesDatePreset, matchesRange, rangeActive, type DatePreset,
} from '@/components/ui/FilterDrawer';
import { usePlatformAds, type AdRow } from '@/lib/queries';
import { useDeleteAd } from '@/lib/mutations';
import { money, shortDate } from '@/lib/format';
import { confirmDialog, toast } from '@/lib/feedback';
import { CreateAdModal } from './_modals/CreateAdModal';

type StatusFilter = 'all' | 'active' | 'inactive';
type SortKey = 'default' | 'budgetDesc' | 'leadsDesc' | 'az';

/** Status badge — maps ad status string to Badge tone. */
function AdStatusBadge({ status }: { status: string }) {
  const { t } = useTranslation();
  const tone = status === 'active' ? 'success'
    : status === 'paused' ? 'warn'
    : status === 'ended' ? 'neutral'
    : 'neutral'; // draft
  return <Badge tone={tone}>{t(`platformAds.status.${status}`, status)}</Badge>;
}

/** Channel pill — small mono label. */
function ChannelChip({ channel }: { channel: string }) {
  const { t } = useTranslation();
  return (
    <span className="inline-flex items-center rounded-pill border border-line px-2 py-0.5 text-micro font-semibold uppercase tracking-wide text-ink-soft">
      {t(`platformAds.channels.${channel}`, channel)}
    </span>
  );
}

/** Mobile ad card — richer than a RowCard: name, channel badge, status, budget/leads. */
function AdCard({ row, onOpen }: { row: AdRow; onOpen: () => void }) {
  const { t } = useTranslation();
  const currency = t('billing.currency');
  return (
    <div
      role="button"
      tabIndex={0}
      onClick={onOpen}
      onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); onOpen(); } }}
      className="cursor-pointer rounded-card border border-line bg-surface p-4 transition hover:border-ink/15 hover:shadow-card focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ink/20"
    >
      <div className="flex items-start gap-3">
        <span className="grid h-10 w-10 shrink-0 place-items-center rounded-xl bg-accent/10 text-accent">
          <Megaphone size={18} />
        </span>
        <div className="min-w-0 flex-1">
          <div className="truncate font-medium">{row.name}</div>
          <div className="mt-0.5 truncate text-caption text-muted">
            <ChannelChip channel={row.channel} />
          </div>
        </div>
        <ChevronRight size={16} className="mt-1 shrink-0 text-muted" />
      </div>
      <div className="mt-3 flex flex-wrap items-center gap-2">
        <AdStatusBadge status={row.status} />
        {row.started_at && (
          <span className="text-micro text-muted">{shortDate(row.started_at)}</span>
        )}
      </div>
      <div className="mt-3 grid grid-cols-2 gap-2 border-t border-line pt-3">
        <div>
          <div className="text-micro text-muted">{t('platformAds.colBudget', 'Byudjet')}</div>
          <div className="mt-0.5 text-small font-semibold tabular-nums">{money(row.budget)} <span className="font-normal text-muted">{currency}</span></div>
        </div>
        <div>
          <div className="text-micro text-muted">{t('platformAds.colLeads', 'Lidlar')}</div>
          <div className="mt-0.5 text-small font-semibold tabular-nums">{row.leads_count}</div>
        </div>
      </div>
    </div>
  );
}

export default function PlatformAdsPage() {
  const { t } = useTranslation();
  const [open, setOpen] = useState(false);
  const [editing, setEditing] = useState<AdRow | null>(null);
  const [detail, setDetail] = useState<AdRow | null>(null);
  const [search, setSearch] = useState('');
  const [debouncedSearch, setDebouncedSearch] = useState('');
  useEffect(() => {
    const id = setTimeout(() => setDebouncedSearch(search.trim().toLowerCase()), 250);
    return () => clearTimeout(id);
  }, [search]);

  const [status, setStatus] = useState<StatusFilter>('all');
  const [channels, setChannels] = useState<string[]>([]);
  const [datePreset, setDatePreset] = useState<DatePreset>('all');
  const [budgetRange, setBudgetRange] = useState({ min: '', max: '' });
  const [sort, setSort] = useState<SortKey>('default');
  const [filterOpen, setFilterOpen] = useState(false);

  const q = usePlatformAds();
  const rows = useMemo(() => q.data ?? [], [q.data]);
  const del = useDeleteAd();

  // ── Aggregates for KPI cards ──────────────────────────────────────────────
  const kpiActive = rows.filter((a) => a.status === 'active').length;

  // Status counts for filter drawer
  const counts: Record<StatusFilter, number> = {
    all: rows.length,
    active: kpiActive,
    inactive: rows.filter((a) => a.status !== 'active').length,
  };

  // Channel options derived from loaded rows
  const channelOptions = useMemo(() => {
    const seen = new Map<string, number>();
    for (const a of rows) seen.set(a.channel, (seen.get(a.channel) ?? 0) + 1);
    return [...seen.keys()].sort().map((c) => ({ value: c, label: t(`platformAds.channels.${c}`, c) }));
  }, [rows, t]);

  const filteredRows = useMemo(() => {
    return rows
      .filter((a) => status === 'all' || (status === 'active' ? a.status === 'active' : a.status !== 'active'))
      .filter((a) => channels.length === 0 || channels.includes(a.channel))
      .filter((a) => matchesDatePreset(a.started_at, datePreset))
      .filter((a) => matchesRange(a.budget, budgetRange))
      .filter((a) => !debouncedSearch
        || a.name.toLowerCase().includes(debouncedSearch)
        || a.channel.toLowerCase().includes(debouncedSearch))
      .slice()
      .sort((a, b) => {
        if (sort === 'budgetDesc') return b.budget - a.budget;
        if (sort === 'leadsDesc') return b.leads_count - a.leads_count;
        if (sort === 'az') return a.name.localeCompare(b.name);
        return 0;
      });
  }, [rows, status, channels, datePreset, budgetRange, debouncedSearch, sort]);

  const activeFilters = (status !== 'all' ? 1 : 0) + channels.length
    + (datePreset !== 'all' ? 1 : 0) + (rangeActive(budgetRange) ? 1 : 0) + (sort !== 'default' ? 1 : 0);
  const searchOrFilter = !!debouncedSearch || activeFilters > 0;
  const resetAll = () => {
    setStatus('all'); setChannels([]); setDatePreset('all');
    setBudgetRange({ min: '', max: '' }); setSort('default');
  };
  const clearEverything = () => { resetAll(); setSearch(''); };

  // Active filter chips
  const dateLabel: Record<DatePreset, string> = {
    all: '', today: t('filters.today', 'Bugun'), '7d': t('filters.last7', '7 kun'),
    '30d': t('filters.last30', '30 kun'), '90d': t('filters.last90', '90 kun'),
  };
  const chips = [
    status !== 'all' && { key: 'status', label: status === 'active' ? t('platformAds.status.active') : t('common.inactive'), onRemove: () => setStatus('all') },
    ...channels.map((c) => ({ key: `ch-${c}`, label: t(`platformAds.channels.${c}`, c), onRemove: () => setChannels((prev) => prev.filter((x) => x !== c)) })),
    datePreset !== 'all' && { key: 'date', label: dateLabel[datePreset], onRemove: () => setDatePreset('all') },
    rangeActive(budgetRange) && { key: 'budget', label: t('platformAds.colBudget', 'Byudjet'), onRemove: () => setBudgetRange({ min: '', max: '' }) },
    sort !== 'default' && {
      key: 'sort',
      label: sort === 'budgetDesc' ? t('platformAds.sortBudget', 'Byudjet (katta)')
           : sort === 'leadsDesc'  ? t('platformAds.sortLeads', "Lidlar (ko'p)")
           : t('platformAds.sortAz', 'Nomi (A–Z)'),
      onRemove: () => setSort('default'),
    },
  ].filter(Boolean) as { key: string; label: string; onRemove: () => void }[];

  const onDelete = async (a: AdRow) => {
    if (!(await confirmDialog({ message: t('platformAds.deleteConfirm', { name: a.name }), danger: true, confirmLabel: t('common.delete') }))) return;
    try { await del.mutateAsync(a.id); setDetail(null); }
    catch (ex) {
      const e = ex as { response?: { data?: { detail?: string } } };
      toast.error(e.response?.data?.detail ?? t('common.errorLoading'));
    }
  };
  const openEdit = (a: AdRow) => { setDetail(null); setEditing(a); };

  // Desktop table columns — lg breakpoint (sidebar takes 280px so table starts at ~744px content)
  const currency = t('billing.currency');
  // (H17) memoized so an unrelated re-render doesn't rebuild this array.
  const cols: Column<AdRow>[] = useMemo(() => [
    {
      key: 'name', label: t('platformAds.colName', 'Kampaniya'), width: '1.6fr',
      render: (a) => (
        <span className="flex min-w-0 items-center gap-3">
          <span className="grid h-8 w-8 shrink-0 place-items-center rounded-xl bg-accent/10 text-accent">
            <Megaphone size={14} />
          </span>
          <span className="block truncate font-medium">{a.name}</span>
        </span>
      ),
      sortValue: (a) => a.name.toLowerCase(),
    },
    {
      key: 'channel', label: t('platformAds.colCampaign', 'Kanal'), width: '100px', truncate: false,
      render: (a) => <ChannelChip channel={a.channel} />,
      sortValue: (a) => a.channel.toLowerCase(),
    },
    {
      key: 'status', label: t('platformAds.filterStatus', 'Holat'), width: '110px', truncate: false,
      render: (a) => <AdStatusBadge status={a.status} />,
      sortValue: (a) => a.status,
    },
    {
      key: 'leads', label: t('platformAds.colLeads', 'Lidlar'), width: '80px', align: 'right',
      render: (a) => <span className="tabular-nums text-small">{a.leads_count}</span>,
      sortValue: (a) => a.leads_count,
    },
    {
      key: 'budget', label: t('platformAds.colBudget', 'Byudjet'), width: '140px', align: 'right',
      render: (a) => (
        <span className="tabular-nums text-small">
          {money(a.budget)} <span className="text-muted">{currency}</span>
        </span>
      ),
      sortValue: (a) => a.budget,
    },
    {
      key: 'started', label: t('platformAds.colStarted', 'Boshlandi'), width: '108px', align: 'right',
      render: (a) => <span className="tabular-nums text-small text-muted">{a.started_at ? shortDate(a.started_at) : '—'}</span>,
      sortValue: (a) => a.started_at ?? '',
    },
    {
      key: 'chevron', label: '', width: '28px', align: 'right',
      render: () => <ChevronRight size={16} className="text-muted" />,
    },
  ], [t, currency]);

  // (#6) The summary strip and KpiCard grid are gone: on a list page they are
  // a preamble the reader scrolls past to reach the rows they came for, and on
  // a phone they cost most of the first paint. The figures live on Overview.


  const showToolbar = q.isLoading || rows.length > 0 || searchOrFilter;

  let body: ReactNode;
  if (q.isLoading) {
    body = <SkeletonList rows={5} />;
  } else if (q.isError) {
    body = <ErrorCard error={q.error} onRetry={() => q.refetch()} />;
  } else if (rows.length === 0) {
    // First-run: no campaigns at all
    body = (
      <EmptyState
        tone="accent"
        size="compact"
        icon={<Megaphone size={24} />}
        title={t('platformAds.emptyTitle')}
        body={t('platformAds.emptyBody')}
      />
    );
  } else if (filteredRows.length === 0 && searchOrFilter) {
    // No-results after filter/search
    body = (
      <EmptyState
        size="compact"
        icon={<Ban size={24} />}
        title={t('common.noResults', 'Natija topilmadi')}
        body={t('platformAds.noResultsBody', "Filtr yoki qidiruvga mos kampaniya topilmadi.")}
        action={
          <Button variant="outline" size="md" onClick={clearEverything}>
            {t('common.clear', 'Tozalash')}
          </Button>
        }
      />
    );
  } else {
    body = (
      <>
        {/* Desktop (lg+): dense sortable table; below lg sidebar leaves too little width. */}
        <div className="hidden lg:block">
          <DataTable
            columns={cols}
            rows={filteredRows}
            rowKey={(a) => a.id}
            onRowClick={(a) => setDetail(a)}
          />
        </div>
        {/* Mobile + tablet: rich ad cards. */}
        <ul className="space-y-2.5 lg:hidden">
          {filteredRows.map((a) => (
            <li key={a.id}>
              <AdCard row={a} onOpen={() => setDetail(a)} />
            </li>
          ))}
        </ul>
      </>
    );
  }

  return (
    <>
      {/* Header — desktop PageHeader / mobile MobileHeader (Telegram-safe). */}
      <div className="hidden md:block">
        <PageHeader
          title={t('nav.ads')}
          subtitle={t('platformAds.pageSubtitle', 'Barcha reklama kampaniyalari va ularning natijalari')}
        />
      </div>
      <div className="md:hidden">
        <MobileHeader title={t('nav.ads')} />
        <p className="mb-3 max-w-prose text-small leading-snug text-muted">
          {t('platformAds.pageSubtitle', 'Barcha reklama kampaniyalari va ularning natijalari')}
        </p>
      </div>

      <FloatingCreateAction onClick={() => setOpen(true)} label={t('platformAds.newCampaign')} />

      <div className="space-y-4 sm:space-y-5">
        {/* Toolbar — search + filter. Hidden on pure first-run empty. */}
        {showToolbar && (
          <div>
            <FilterBar>
              <SearchInput
                value={search}
                onChange={setSearch}
                className="flex-1"
                placeholder={t('platformAds.searchPh', "Kampaniya yoki kanal bo'yicha qidirish…")}
              />
              <FilterButton count={activeFilters} onClick={() => setFilterOpen(true)} />
            </FilterBar>
            <ActiveFilters filters={chips} onClearAll={clearEverything} />
          </div>
        )}

        {body}
      </div>

      {/* Filter drawer */}
      <FilterDrawer open={filterOpen} onClose={() => setFilterOpen(false)} onClear={resetAll}>
        <RadioGroup<StatusFilter>
          label={t('platformAds.filterStatus', 'Holat')}
          value={status}
          onChange={setStatus}
          options={[
            { value: 'all',      label: t('common.all', 'Hammasi'),              count: counts.all },
            { value: 'active',   label: t('platformAds.status.active', 'Faol'),  count: counts.active },
            { value: 'inactive', label: t('common.inactive', 'Nofaol'),          count: counts.inactive },
          ]}
        />
        {channelOptions.length > 0 && (
          <CheckGroup
            label={t('platformAds.colCampaign', 'Kanal')}
            value={channels}
            onChange={setChannels}
            options={channelOptions}
          />
        )}
        <DateRangeGroup
          label={t('platformAds.colStarted', 'Boshlangan vaqti')}
          value={datePreset}
          onChange={setDatePreset}
        />
        <RangeGroup
          label={t('platformAds.colBudget', 'Byudjet')}
          unit={t('billing.currency', "so'm")}
          value={budgetRange}
          onChange={setBudgetRange}
        />
        <RadioGroup<SortKey>
          label={t('common.sort', 'Saralash')}
          value={sort}
          onChange={setSort}
          options={[
            { value: 'default',    label: t('platformAds.sortDefault', 'Standart') },
            { value: 'budgetDesc', label: t('platformAds.sortBudget', 'Byudjet (katta)') },
            { value: 'leadsDesc',  label: t('platformAds.sortLeads', "Lidlar (ko'p)") },
            { value: 'az',         label: t('platformAds.sortAz', 'Nomi (A–Z)') },
          ]}
        />
      </FilterDrawer>

      {/* Detail modal — hosts Edit + Delete; no 3-dot on rows. */}
      <DetailModal
        open={!!detail}
        onClose={() => setDetail(null)}
        title={detail?.name ?? ''}
        subtitle={detail ? <AdStatusBadge status={detail.status} /> : undefined}
        onEdit={detail ? () => openEdit(detail) : undefined}
        onDelete={detail ? () => onDelete(detail) : undefined}
        deleting={del.isPending}
      >
        {detail && (
          <DefList>
            <DefRow label={t('platformAds.colCampaign', 'Kanal')}>
              <ChannelChip channel={detail.channel} />
            </DefRow>
            <DefRow label={t('platformAds.colBudget', 'Byudjet')}>
              {money(detail.budget)} / {money(detail.spent)} {currency}
            </DefRow>
            <DefRow label={t('platformAds.colLeads', 'Lidlar')}>{detail.leads_count}</DefRow>
            <DefRow label={t('platformAds.colConv', 'Konversiya')}>{detail.converted_tenants}</DefRow>
            <DefRow label={t('platformAds.colStarted', 'Boshlandi')}>
              {detail.started_at ? shortDate(detail.started_at) : '—'}
            </DefRow>
            <DefRow label={t('platformAds.colEnded', 'Tugadi')}>
              {detail.ended_at ? shortDate(detail.ended_at) : '—'}
            </DefRow>
            <DefRow label={t('platformAds.colNotes', 'Izoh')}>{detail.notes}</DefRow>
          </DefList>
        )}
      </DetailModal>

      {/* Create / Edit modal */}
      <CreateAdModal
        open={open || !!editing}
        editing={editing}
        onClose={() => { setOpen(false); setEditing(null); }}
      />
    </>
  );
}
