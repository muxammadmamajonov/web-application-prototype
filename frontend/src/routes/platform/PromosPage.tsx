import { useEffect, useMemo, useState, type ReactNode } from 'react';
import { useTranslation } from 'react-i18next';
import {
  ChevronRight, Ticket,
} from 'lucide-react';
import { PageHeader } from '@/components/ui/PageHeader';
import { FloatingCreateAction } from '@/components/ui/FloatingCreateAction';
import { Button } from '@/components/ui/Button';
import { Badge } from '@/components/ui/Badge';
import { EmptyState } from '@/components/ui/EmptyState';
import { ErrorCard } from '@/components/ui/ErrorCard';
import { DataTable, type Column } from '@/components/ui/DataTable';
import { SearchInput } from '@/components/ui/SearchInput';
import { SkeletonList } from '@/components/ui/Skeleton';
import { DetailDrawer, DetailSection, DetailRow, DangerZone } from '@/components/ui/DetailDrawer';
import {
  FilterBar, FilterButton, FilterDrawer, ActiveFilters, RadioGroup, DateRangeGroup, RangeGroup,
  matchesDatePreset, matchesRange, rangeActive, type DatePreset,
} from '@/components/ui/FilterDrawer';
import { usePlatformPromos, type PromoRow } from '@/lib/queries';
import { useDeletePromo } from '@/lib/mutations';
import { MobileHeader } from '@/components/mobile/primitives';
import { confirmDialog, toast } from '@/lib/feedback';
import { money, shortDate } from '@/lib/format';
import { CreatePromoModal } from './_modals/CreatePromoModal';

type StatusFilter = 'all' | 'active' | 'inactive';
type KindFilter = 'all' | 'percent' | 'fixed';
type SortKey = 'default' | 'valueDesc' | 'usedDesc';

/** Human-readable discount value — shared by table, mobile card, and detail drawer. */
function valueLabel(p: PromoRow, currency: string) {
  return p.kind === 'percent' ? `${p.value}%` : `${money(p.value)} ${currency}`;
}

/** Discount-kind chip — distinct appearance for percent vs fixed. */
function KindBadge({ kind, t }: { kind: PromoRow['kind']; t: ReturnType<typeof useTranslation>['t'] }) {
  return kind === 'percent'
    ? <Badge tone="accent">{t('platformPromos.kindPercent', 'Foiz (%)')}</Badge>
    : <Badge tone="neutral">{t('platformPromos.kindFixed', "Qat'iy summa")}</Badge>;
}

/** Mobile promo card — identity + badges, tappable full-surface. */
function PromoCard({ row, onOpen, currency, t }: {
  row: PromoRow;
  onOpen: () => void;
  currency: string;
  t: ReturnType<typeof useTranslation>['t'];
}) {
  return (
    <div
      role="button"
      tabIndex={0}
      onClick={onOpen}
      onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); onOpen(); } }}
      className="cursor-pointer rounded-card border border-line bg-surface p-4 transition hover:border-ink/15 hover:shadow-card focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ink/20"
    >
      <div className="flex items-start gap-3">
        <span className="grid h-10 w-10 shrink-0 place-items-center rounded-xl bg-[color:var(--c-accent-soft)] text-accent">
          <Ticket size={18} />
        </span>
        <div className="min-w-0 flex-1">
          <div className="truncate font-mono font-semibold">{row.code}</div>
          <div className="truncate text-caption text-muted">{row.description ?? valueLabel(row, currency)}</div>
        </div>
        <ChevronRight size={16} className="mt-1 shrink-0 text-muted" />
      </div>
      <div className="mt-3 flex flex-wrap items-center gap-2">
        {row.is_active
          ? <Badge tone="success">{t('common.active', 'Faol')}</Badge>
          : <Badge tone="neutral">{t('common.inactive', 'Nofaol')}</Badge>}
        <KindBadge kind={row.kind} t={t} />
        <span className="font-mono text-caption font-medium text-ink-soft tabular-nums">
          {valueLabel(row, currency)}
        </span>
      </div>
      <div className="mt-3 flex items-center justify-between gap-2 border-t border-line pt-3 text-caption text-muted">
        <span>{t('platformPromos.colUsed', 'Ishlatildi')}</span>
        <span className="tabular-nums">
          {row.used_count}{row.max_uses != null ? ` / ${row.max_uses}` : ''}
        </span>
      </div>
    </div>
  );
}

export default function PlatformPromosPage() {
  const { t } = useTranslation();
  const currency = t('billing.currency');

  // ── Modal / drawer state ──────────────────────────────────────────────────
  const [createOpen, setCreateOpen] = useState(false);
  const [editing, setEditing] = useState<PromoRow | null>(null);
  const [drawerRow, setDrawerRow] = useState<PromoRow | null>(null);

  // ── Data ──────────────────────────────────────────────────────────────────
  const q = usePlatformPromos();
  const rows = useMemo(() => q.data ?? [], [q.data]);
  const del = useDeletePromo();

  // ── Filter / search state ─────────────────────────────────────────────────
  const [search, setSearch] = useState('');
  const [debouncedSearch, setDebouncedSearch] = useState('');
  useEffect(() => {
    const id = setTimeout(() => setDebouncedSearch(search.trim().toLowerCase()), 250);
    return () => clearTimeout(id);
  }, [search]);

  const [status, setStatus] = useState<StatusFilter>('all');
  const [kind, setKind] = useState<KindFilter>('all');
  const [datePreset, setDatePreset] = useState<DatePreset>('all');
  const [usesRange, setUsesRange] = useState({ min: '', max: '' });
  const [sort, setSort] = useState<SortKey>('default');
  const [filterOpen, setFilterOpen] = useState(false);

  // ── Derived counts ────────────────────────────────────────────────────────
  const counts: Record<StatusFilter, number> = {
    all: rows.length,
    active: rows.filter((p) => p.is_active).length,
    inactive: rows.filter((p) => !p.is_active).length,
  };
  const kindCounts: Record<Exclude<KindFilter, 'all'>, number> = {
    percent: rows.filter((p) => p.kind === 'percent').length,
    fixed: rows.filter((p) => p.kind === 'fixed').length,
  };

  // ── Filtered + sorted rows ────────────────────────────────────────────────
  const filteredRows = useMemo(() => rows
    .filter((p) => status === 'all' || (status === 'active' ? p.is_active : !p.is_active))
    .filter((p) => kind === 'all' || p.kind === kind)
    .filter((p) => matchesDatePreset(p.valid_from ?? '', datePreset))
    .filter((p) => matchesRange(p.used_count, usesRange))
    .filter((p) => !debouncedSearch
      || p.code.toLowerCase().includes(debouncedSearch)
      || (p.description ?? '').toLowerCase().includes(debouncedSearch))
    .slice()
    .sort((a, b) => {
      if (sort === 'valueDesc') return b.value - a.value;
      if (sort === 'usedDesc') return b.used_count - a.used_count;
      return 0;
    }),
  [rows, status, kind, datePreset, usesRange, debouncedSearch, sort]);

  const activeFilters = (status !== 'all' ? 1 : 0) + (kind !== 'all' ? 1 : 0)
    + (datePreset !== 'all' ? 1 : 0) + (rangeActive(usesRange) ? 1 : 0) + (sort !== 'default' ? 1 : 0);
  const resetAll = () => {
    setStatus('all'); setKind('all'); setDatePreset('all');
    setUsesRange({ min: '', max: '' }); setSort('default');
  };
  const clearAll = () => { resetAll(); setSearch(''); };
  const searchOrFilter = !!debouncedSearch || activeFilters > 0;

  // ── Filter chips ──────────────────────────────────────────────────────────
  const dateLabel: Record<DatePreset, string> = {
    all: '', today: t('filters.today', 'Bugun'), '7d': t('filters.last7', '7 kun'),
    '30d': t('filters.last30', '30 kun'), '90d': t('filters.last90', '90 kun'),
  };
  const sortLabel: Record<SortKey, string> = {
    default: '',
    valueDesc: t('platformPromos.sortValue', 'Qiymat (katta)'),
    usedDesc: t('platformPromos.sortUsed', "Ishlatilgan (ko'p)"),
  };
  const chips = [
    status !== 'all' && {
      key: 'status',
      label: status === 'active' ? t('common.active', 'Faol') : t('common.inactive', 'Nofaol'),
      onRemove: () => setStatus('all'),
    },
    kind !== 'all' && {
      key: 'kind',
      label: kind === 'percent' ? t('platformPromos.kindPercent', 'Foiz (%)') : t('platformPromos.kindFixed', "Qat'iy summa"),
      onRemove: () => setKind('all'),
    },
    datePreset !== 'all' && { key: 'date', label: dateLabel[datePreset], onRemove: () => setDatePreset('all') },
    rangeActive(usesRange) && {
      key: 'uses',
      label: t('platformPromos.colUsed', 'Ishlatildi'),
      onRemove: () => setUsesRange({ min: '', max: '' }),
    },
    sort !== 'default' && { key: 'sort', label: sortLabel[sort], onRemove: () => setSort('default') },
  ].filter(Boolean) as { key: string; label: string; onRemove: () => void }[];

  // ── Actions ───────────────────────────────────────────────────────────────
  const onDelete = async (p: PromoRow) => {
    if (!(await confirmDialog({
      message: t('platformPromos.deleteConfirm', { code: p.code }),
      danger: true,
      confirmLabel: t('common.delete'),
    }))) return;
    try {
      await del.mutateAsync(p.id);
      setDrawerRow(null);
    } catch (ex) {
      const e = ex as { response?: { data?: { detail?: string } } };
      toast.error(e.response?.data?.detail ?? t('common.errorLoading'));
    }
  };
  const openEdit = (p: PromoRow) => { setDrawerRow(null); setEditing(p); };

  // ── Desktop table columns ───────────────────────────────────────────────── (H17)
  const cols: Column<PromoRow>[] = useMemo(() => [
    {
      key: 'code', label: t('platformPromos.colCode', 'Kod'), width: '1.5fr',
      render: (p) => <span className="block truncate font-mono font-semibold">{p.code}</span>,
      sortValue: (p) => p.code.toLowerCase(),
    },
    {
      key: 'kind', label: t('platformPromos.colKind', 'Tur'), width: '140px', truncate: false,
      render: (p) => <KindBadge kind={p.kind} t={t} />,
      sortValue: (p) => p.kind,
    },
    {
      key: 'value', label: t('platformPromos.colValue', 'Chegirma'), width: '140px',
      render: (p) => <span className="font-mono text-small text-muted tabular-nums">{valueLabel(p, currency)}</span>,
      sortValue: (p) => p.value,
    },
    {
      key: 'used', label: t('platformPromos.colUsed', 'Ishlatildi'), width: '120px', align: 'right',
      render: (p) => (
        <span className="tabular-nums text-small text-muted">
          {p.used_count}{p.max_uses != null ? `/${p.max_uses}` : ''}
        </span>
      ),
      sortValue: (p) => p.used_count,
    },
    {
      key: 'status', label: t('platformPromos.filterStatus', 'Holat'), width: '110px', truncate: false,
      render: (p) => p.is_active
        ? <Badge tone="success">{t('common.active', 'Faol')}</Badge>
        : <Badge tone="neutral">{t('common.inactive', 'Nofaol')}</Badge>,
      sortValue: (p) => (p.is_active ? 1 : 0),
    },
    {
      key: 'chevron', label: '', width: '32px', align: 'right',
      render: () => <ChevronRight size={16} className="text-muted" />,
    },
  ], [t, currency]);

  // ── KPI summary — compact StatStrip on phones, full KpiCards from md up. The
  //    strip carries `loading` flags so the metrics never blank out mid-fetch.
  //    Both the strip and the desktop cards stay hidden on error — a fetch
  //    failure must never render as a confident "0" (the ErrorCard above the
  //    list already communicates the failure; we don't also fake the summary).
  // (#6) The summary strip and KpiCard grid are gone: on a list page they are
  // a preamble the reader scrolls past to reach the rows they came for, and on
  // a phone they cost most of the first paint. The figures live on Overview.


  // ── Toolbar (search + filter): shown when loading, has data, or filter/search
  //    is active so the bar never disappears while the user is mid-search. ────
  const showToolbar = q.isLoading || rows.length > 0 || searchOrFilter;

  // ── Body ──────────────────────────────────────────────────────────────────
  let body: ReactNode;
  if (q.isLoading) {
    body = <SkeletonList rows={5} />;
  } else if (q.isError) {
    body = <ErrorCard error={q.error} onRetry={() => q.refetch()} />;
  } else if (rows.length === 0) {
    // First-run empty — accent tone, marketing-oriented copy. The CTA is the
    // floating `+` already on screen, not a second button inside the card.
    body = (
      <EmptyState
        tone="accent"
        size="compact"
        icon={<Ticket size={24} />}
        title={t('platformPromos.emptyTitle', "Hali promo yo'q")}
        body={t('platformPromos.emptyBody', "Marketing kampaniyalari uchun chegirma kodlari qo'shing.")}
      />
    );
  } else if (filteredRows.length === 0) {
    // No-results — muted tone, "Tozalash" to reset filters.
    body = (
      <EmptyState
        size="compact"
        icon={<Ticket size={24} />}
        title={t('common.noResults', 'Natija topilmadi')}
        body={t('platformPromos.noResultsBody', 'Filtr yoki qidiruvga mos promokod topilmadi.')}
        action={searchOrFilter
          ? <Button variant="outline" size="md" onClick={clearAll}>{t('common.clear', 'Tozalash')}</Button>
          : undefined}
      />
    );
  } else {
    body = (
      <>
        {/* Desktop (lg+): sortable DataTable — takes full content width. */}
        <div className="hidden lg:block">
          <DataTable
            columns={cols}
            rows={filteredRows}
            rowKey={(p) => p.id}
            onRowClick={(p) => setDrawerRow(p)}
          />
        </div>

        {/* Mobile + tablet: rich promo cards — never the cramped table. */}
        <ul className="space-y-2.5 lg:hidden">
          {filteredRows.map((p) => (
            <li key={p.id}>
              <PromoCard
                row={p}
                onOpen={() => setDrawerRow(p)}
                currency={currency}
                t={t}
              />
            </li>
          ))}
        </ul>
      </>
    );
  }

  return (
    <>
      {/* Desktop PageHeader — hidden on mobile (Telegram-safe). */}
      <div className="hidden md:block">
        <PageHeader
          title={t('nav.promos')}
          subtitle={t('platformPromos.pageSubtitle', '{{count}} ta promokod', { count: rows.length })}
        />
      </div>

      <div className="md:hidden">
        <MobileHeader title={t('nav.promos')} />
        <p className="mb-3 max-w-prose text-small leading-snug text-muted">
          {t('platformPromos.pageSubtitle', '{{count}} ta promokod', { count: rows.length })}
        </p>
      </div>

      <FloatingCreateAction onClick={() => setCreateOpen(true)} label={t('platformPromos.newPromo', 'Yangi promo')} />

      <div className="space-y-4 sm:space-y-5">
        {/* KPI summary — above the toolbar so the page has substance once data
            loads; renders nothing on fetch error (see `summary` above). */}
        {/* FilterBar + ActiveFilters — wrapped in SectionCard for consistent
            visual grouping; toolbar hidden on first-run empty (no data yet). */}
        {showToolbar && (
          <div>
            <FilterBar>
              <SearchInput
                value={search}
                onChange={setSearch}
                className="flex-1"
                placeholder={t('platformPromos.searchPh', "Kod yoki tavsif bo'yicha qidirish…")}
              />
              <FilterButton count={activeFilters} onClick={() => setFilterOpen(true)} />
            </FilterBar>
            <ActiveFilters filters={chips} onClearAll={clearAll} />
          </div>
        )}

        {body}
      </div>

      {/* FilterDrawer — portaled; full filter surface. */}
      <FilterDrawer open={filterOpen} onClose={() => setFilterOpen(false)} onClear={resetAll}>
        <RadioGroup<StatusFilter>
          label={t('platformPromos.filterStatus', 'Holat')}
          value={status}
          onChange={setStatus}
          options={[
            { value: 'all', label: t('common.all', 'Hammasi'), count: counts.all },
            { value: 'active', label: t('common.active', 'Faol'), count: counts.active },
            { value: 'inactive', label: t('common.inactive', 'Nofaol'), count: counts.inactive },
          ]}
        />
        <RadioGroup<KindFilter>
          label={t('platformPromos.colKind', 'Tur')}
          value={kind}
          onChange={setKind}
          options={[
            { value: 'all', label: t('common.all', 'Hammasi') },
            { value: 'percent', label: t('platformPromos.kindPercent', 'Foiz (%)'), count: kindCounts.percent },
            { value: 'fixed', label: t('platformPromos.kindFixed', "Qat'iy summa"), count: kindCounts.fixed },
          ]}
        />
        <DateRangeGroup
          label={t('platformPromos.filterValidFrom', 'Boshlanish vaqti')}
          value={datePreset}
          onChange={setDatePreset}
        />
        <RangeGroup
          label={t('platformPromos.colUsed', 'Ishlatildi')}
          value={usesRange}
          onChange={setUsesRange}
        />
        <RadioGroup<SortKey>
          label={t('common.sort', 'Saralash')}
          value={sort}
          onChange={setSort}
          options={[
            { value: 'default', label: t('platformPromos.sortDefault', 'Standart') },
            { value: 'valueDesc', label: t('platformPromos.sortValue', 'Qiymat (katta)') },
            { value: 'usedDesc', label: t('platformPromos.sortUsed', "Ishlatilgan (ko'p)") },
          ]}
        />
      </FilterDrawer>

      {/* Detail drawer — right-side panel (desktop) / full-height sheet (mobile).
          Hosts Edit + Delete so the list never needs a 3-dot per row. */}
      <DetailDrawer
        open={!!drawerRow}
        onClose={() => setDrawerRow(null)}
        eyebrow={t('nav.promos')}
        title={drawerRow ? <span className="font-mono">{drawerRow.code}</span> : ''}
        badge={drawerRow
          ? (drawerRow.is_active
            ? <Badge tone="success">{t('common.active', 'Faol')}</Badge>
            : <Badge tone="neutral">{t('common.inactive', 'Nofaol')}</Badge>)
          : undefined}
        footer={drawerRow ? (
          <>
            <Button variant="outline" size="md" onClick={() => drawerRow && openEdit(drawerRow)}>
              {t('common.edit', 'Tahrirlash')}
            </Button>
          </>
        ) : undefined}
      >
        {drawerRow && (
          <>
            <DetailSection title={t('platformPromos.detailInfo', "Promo ma'lumotlari")}>
              <DetailRow label={t('platformPromos.colKind', 'Tur')}>
                <KindBadge kind={drawerRow.kind} t={t} />
              </DetailRow>
              <DetailRow label={t('platformPromos.colValue', 'Chegirma')}>
                <span className="font-mono tabular-nums">{valueLabel(drawerRow, currency)}</span>
              </DetailRow>
              <DetailRow label={t('platformPromos.colUsed', 'Ishlatildi')}>
                {drawerRow.used_count}{drawerRow.max_uses != null ? ` / ${drawerRow.max_uses}` : ''}
              </DetailRow>
              {drawerRow.description && (
                <DetailRow label={t('platformPromos.colDesc', 'Tavsif')}>
                  {drawerRow.description}
                </DetailRow>
              )}
              {drawerRow.valid_from && (
                <DetailRow label={t('platformPromos.validFrom', 'Boshlanish')}>
                  {shortDate(drawerRow.valid_from)}
                </DetailRow>
              )}
              {drawerRow.valid_until && (
                <DetailRow label={t('platformPromos.validUntil', 'Tugash')}>
                  {shortDate(drawerRow.valid_until)}
                </DetailRow>
              )}
            </DetailSection>

            <DangerZone
              title={t('platformPromos.dangerTitle', 'Xavfli zona')}
              description={t('platformPromos.dangerDesc', "Bu amalni bekor qilib bo'lmaydi.")}
            >
              <Button
                variant="danger"
                size="md"
                loading={del.isPending}
                onClick={() => onDelete(drawerRow)}
              >
                {t('common.delete', "O'chirish")}
              </Button>
            </DangerZone>
          </>
        )}
      </DetailDrawer>

      {/* Create / Edit modal. */}
      <CreatePromoModal
        open={createOpen || !!editing}
        editing={editing}
        onClose={() => { setCreateOpen(false); setEditing(null); }}
      />
    </>
  );
}
