import { useEffect, useMemo, useState, type ReactNode } from 'react';
import { useTranslation } from 'react-i18next';
import { Building2, ChevronRight } from 'lucide-react';
import { PageHeader } from '@/components/ui/PageHeader';
import { FloatingCreateAction } from '@/components/ui/FloatingCreateAction';
import { Button } from '@/components/ui/Button';
import { StatusBadge } from '@/components/ui/Badge';
import { Avatar } from '@/components/ui/Avatar';
import { EmptyState } from '@/components/ui/EmptyState';
import { ErrorCard } from '@/components/ui/ErrorCard';
import { DataTable, type Column } from '@/components/ui/DataTable';
import { SearchInput } from '@/components/ui/SearchInput';
import { SkeletonList } from '@/components/ui/Skeleton';
import {
  FilterBar, FilterButton, FilterDrawer, ActiveFilters, RadioGroup, DateRangeGroup,
  matchesDatePreset, type DatePreset,
} from '@/components/ui/FilterDrawer';
import { usePlatformOverview, usePlatformTenants, type TenantRow } from '@/lib/queries';
import { shortDate } from '@/lib/format';
import { MobileHeader } from '@/components/mobile/primitives';
import { CreateTenantModal } from './_modals/CreateTenantModal';
import { TenantDetailDrawer } from './_modals/TenantDetailDrawer';
import { displayPhone } from '@/lib/phone';

const STATUS_TABS = ['all', 'active', 'demo', 'suspended', 'blocked'] as const;
type StatusTab = (typeof STATUS_TABS)[number];
const PLAN_OPTS = ['all', 'trial', 'lite', 'pro', 'custom'] as const;
type PlanOpt = (typeof PLAN_OPTS)[number];
const LOCALE_OPTS = ['all', 'uz', 'ru'] as const;
type LocaleOpt = (typeof LOCALE_OPTS)[number];
type TenantSort = 'new' | 'name';

/** Small uppercase tariff pill — one shape for the table + mobile cards. */
function PlanBadge({ code }: { code: string }) {
  return (
    <span className="inline-flex items-center rounded-pill border border-line px-2 py-0.5 text-micro font-semibold uppercase tracking-wide text-ink-soft">
      {code}
    </span>
  );
}

/** The "Biznes" identity cell — avatar + name + slug·locale, shared by table rows. */
function BusinessCell({ row }: { row: TenantRow }) {
  return (
    <span className="flex min-w-0 items-center gap-3">
      <Avatar name={row.name} size="sm" />
      <span className="min-w-0">
        <span className="block truncate font-medium">{row.name}</span>
        <span className="block truncate text-micro text-muted">
          <span className="font-mono">{row.slug}</span> · {row.locale.toUpperCase()}
        </span>
      </span>
    </span>
  );
}

/** Mobile tenant card — richer than a plain RowCard: identity, badges, phone,
 *  created date, and a clear tap-to-open affordance. */
function TenantCard({ row, onOpen }: { row: TenantRow; onOpen: () => void }) {
  const { t } = useTranslation();
  return (
    <div
      role="button"
      tabIndex={0}
      onClick={onOpen}
      onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); onOpen(); } }}
      className="cursor-pointer rounded-card border border-line bg-surface p-4 transition hover:border-ink/15 hover:shadow-card focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ink/20"
    >
      <div className="flex items-start gap-3">
        <Avatar name={row.name} size="md" />
        <div className="min-w-0 flex-1">
          <div className="truncate font-medium">{row.name}</div>
          <div className="truncate font-mono text-caption text-muted">{row.slug}</div>
        </div>
        <ChevronRight size={16} className="mt-1 shrink-0 text-muted" />
      </div>
      <div className="mt-3 flex flex-wrap items-center gap-2">
        <StatusBadge status={row.status} />
        <PlanBadge code={row.plan_code} />
        <span className="text-micro font-medium uppercase text-muted">{row.locale}</span>
      </div>
      <div className="mt-3 flex items-center justify-between gap-2 border-t border-line pt-3 text-caption text-muted">
        <span className="truncate tabular-nums">{displayPhone(row.contact_phone) || t('platformTenants.noPhone', 'Telefon yo‘q')}</span>
        <span className="shrink-0 tabular-nums">{shortDate(row.created_at)}</span>
      </div>
    </div>
  );
}

export default function PlatformTenantsPage() {
  const { t } = useTranslation();
  const [tab, setTab] = useState<StatusTab>('all');
  const [plan, setPlan] = useState<PlanOpt>('all');
  const [loc, setLoc] = useState<LocaleOpt>('all');
  const [datePreset, setDatePreset] = useState<DatePreset>('all');
  const [sort, setSort] = useState<TenantSort>('new');
  const [filterOpen, setFilterOpen] = useState(false);
  const [createOpen, setCreateOpen] = useState(false);
  const [drawerRow, setDrawerRow] = useState<TenantRow | null>(null);
  const [q, setQ] = useState('');
  const [debouncedQ, setDebouncedQ] = useState('');
  useEffect(() => { const id = setTimeout(() => setDebouncedQ(q.trim().toLowerCase()), 250); return () => clearTimeout(id); }, [q]);

  const query = usePlatformTenants({ status: tab === 'all' ? undefined : tab, limit: 200 });
  const allRows = useMemo(() => query.data?.items ?? [], [query.data]);
  const total = query.data?.total ?? 0;

  // Plan + locale + date + search refined client-side; sort applied last.
  const rows = useMemo(() => allRows.filter((r) => {
    if (plan !== 'all' && r.plan_code !== plan) return false;
    if (loc !== 'all' && r.locale.toLowerCase() !== loc) return false;
    if (!matchesDatePreset(r.created_at, datePreset)) return false;
    if (debouncedQ && !(`${r.name} ${r.slug} ${r.contact_phone ?? ''}`.toLowerCase().includes(debouncedQ))) return false;
    return true;
  }).slice().sort((a, b) => (sort === 'name' ? a.name.localeCompare(b.name) : 0)),
  [allRows, plan, loc, datePreset, debouncedQ, sort]);

  const activeFilters = (tab !== 'all' ? 1 : 0) + (plan !== 'all' ? 1 : 0)
    + (loc !== 'all' ? 1 : 0) + (datePreset !== 'all' ? 1 : 0) + (sort !== 'new' ? 1 : 0);
  const clearAll = () => { setTab('all'); setPlan('all'); setLoc('all'); setDatePreset('all'); setSort('new'); };
  const searchOrFilter = !!debouncedQ || activeFilters > 0;

  const dateLabel: Record<DatePreset, string> = {
    all: '', today: t('filters.today', 'Bugun'), '7d': t('filters.last7', '7 kun'),
    '30d': t('filters.last30', '30 kun'), '90d': t('filters.last90', '90 kun'),
  };
  const chips = [
    tab !== 'all' && { key: 'status', label: t(`status.${tab}`), onRemove: () => setTab('all') },
    plan !== 'all' && { key: 'plan', label: plan.toUpperCase(), onRemove: () => setPlan('all') },
    loc !== 'all' && { key: 'loc', label: loc.toUpperCase(), onRemove: () => setLoc('all') },
    datePreset !== 'all' && { key: 'date', label: dateLabel[datePreset], onRemove: () => setDatePreset('all') },
    sort !== 'new' && { key: 'sort', label: t('platformTenants.sortName', 'Nomi (A–Z)'), onRemove: () => setSort('new') },
  ].filter(Boolean) as { key: string; label: string; onRemove: () => void }[];

  // Per-status counts from the server aggregate (never derived from the ≤200 page).
  const ov = usePlatformOverview();
  const counts = useMemo<Record<StatusTab, number>>(() => {
    const b = ov.data?.tenants_by_status;
    if (b) return { all: ov.data!.tenants_total, active: b.active, demo: b.demo, suspended: b.suspended, blocked: b.blocked };
    const acc: Record<StatusTab, number> = { all: total, active: 0, demo: 0, suspended: 0, blocked: 0 };
    for (const r of allRows) acc[r.status] = (acc[r.status] ?? 0) + 1;
    return acc;
  }, [ov.data, allRows, total]);

  // Trimmed to 5 columns so the table stays comfortable at lg (≈664px content
  // once the 280px sidebar is subtracted). Phone lives in the detail drawer +
  // mobile cards; locale is folded into the Biznes cell.
  const cols: Column<TenantRow>[] = useMemo(() => [
    {
      key: 'name', label: t('platformTenants.colBusiness', 'Biznes'), width: '1.6fr', truncate: false,
      render: (r) => <BusinessCell row={r} />, sortValue: (r) => r.name.toLowerCase(),
    },
    {
      key: 'plan', label: t('platformTenants.colPlan', 'Tarif'), width: '92px', truncate: false,
      render: (r) => <PlanBadge code={r.plan_code} />, sortValue: (r) => r.plan_code,
    },
    {
      key: 'status', label: t('platformTenants.colStatus', 'Holat'), width: '124px', truncate: false,
      render: (r) => <StatusBadge status={r.status} />, sortValue: (r) => r.status,
    },
    {
      key: 'created', label: t('platformTenants.colCreated', 'Sana'), width: '108px', align: 'right',
      render: (r) => <span className="tabular-nums text-small text-muted">{shortDate(r.created_at)}</span>,
      sortValue: (r) => r.created_at,
    },
    {
      key: 'chevron', label: '', width: '28px', align: 'right',
      render: () => <ChevronRight size={16} className="text-muted" />,
    },
  ], [t]);

  // (#6) The summary strip and KpiCard grid are gone: on a list page they are
  // a preamble the reader scrolls past to reach the thing they came for, and
  // on a phone they cost most of the first screen before a single row appears.
  // The figures still exist on the Overview dashboard, which is the page whose
  // job they are. `counts` is retained — the status tabs below are built from it.

  const showToolbar = query.isLoading || total > 0 || searchOrFilter;

  let body: ReactNode;
  if (query.isLoading) {
    body = <SkeletonList rows={6} />;
  } else if (query.isError) {
    body = <ErrorCard error={query.error} onRetry={() => query.refetch()} />;
  } else if (rows.length === 0 && searchOrFilter) {
    body = (
      <EmptyState
        size="compact"
        icon={<Building2 size={24} />}
        title={t('common.noResults', 'Natija topilmadi')}
        body={allRows.length < total
          ? t('platformTenants.searchTruncatedHint', { shown: allRows.length, total })
          : t('platformTenants.noResultsBody', "Qidiruv yoki filterlarni o‘zgartirib ko‘ring.")}
        action={<Button variant="outline" size="md" onClick={clearAll}>{t('common.clear', 'Tozalash')}</Button>}
      />
    );
  } else if (rows.length === 0) {
    // First-run empty — compact: 3 light numbered chips telling the operator
    // what creating a tenant involves. No CTA of its own: the floating `+` is
    // already on screen, so a button here would be the same action twice.
    body = (
      <EmptyState
        tone="accent"
        size="compact"
        icon={<Building2 size={24} />}
        title={t('platformTenants.emptyTitle')}
        body={t('platformTenants.emptyBody')}
        action={
          <>
            <ol className="mt-1 grid w-full max-w-narrow grid-cols-3 gap-2">
              {[
                t('platformTenants.stepShort1', 'Ma’lumotlar'),
                t('platformTenants.stepShort2', 'Tarif/status'),
                t('platformTenants.stepShort3', 'Havola'),
              ].map((step, i) => (
                <li key={i} className="flex flex-col items-center gap-1.5 rounded-control border border-line bg-cream/60 px-2 py-2.5 text-center">
                  <span className="grid h-5 w-5 shrink-0 place-items-center rounded-pill bg-[color:var(--c-accent-soft)] text-micro font-semibold text-accent">{i + 1}</span>
                  <span className="text-caption leading-tight text-ink-soft">{step}</span>
                </li>
              ))}
            </ol>
          </>
        }
      />
    );
  } else {
    body = (
      <>
        {allRows.length < total && (
          <div className="card mb-2 px-4 py-3 text-center text-body text-muted">
            {t('platformTenants.truncatedNote', { shown: allRows.length, total })}
          </div>
        )}
        {/* Desktop (lg+): dense sortable + paginated table. Below lg the sidebar
            leaves too little width for a table, so we show cards through tablet. */}
        <div className="hidden lg:block">
          <DataTable columns={cols} rows={rows} rowKey={(r) => r.id} onRowClick={(r) => setDrawerRow(r)} />
        </div>
        {/* Mobile + tablet: rich tenant cards (never the squeezed desktop table). */}
        <ul className="space-y-2.5 lg:hidden">
          {rows.map((r) => (
            <li key={r.id}><TenantCard row={r} onOpen={() => setDrawerRow(r)} /></li>
          ))}
        </ul>
      </>
    );
  }

  return (
    <>
      <div className="hidden md:block">
        <PageHeader title={t('nav.tenants')} subtitle={t('platformTenants.pageSubtitle')} />
      </div>
      <div className="md:hidden">
        <MobileHeader title={t('nav.tenants')} />
        <p className="mb-3 max-w-prose text-small leading-snug text-muted">{t('platformTenants.pageSubtitle')}</p>
      </div>

      <FloatingCreateAction onClick={() => setCreateOpen(true)} label={t('platformTenants.newTenant')} />

      <div className="space-y-4 sm:space-y-5">

        {showToolbar && (
          <div>
            <FilterBar>
              <SearchInput value={q} onChange={setQ} className="flex-1"
                placeholder={t('platformTenants.searchPhLong', 'Biznes nomi, slug yoki telefon bo‘yicha qidiring…')} />
              <FilterButton count={activeFilters} onClick={() => setFilterOpen(true)} />
            </FilterBar>
            <ActiveFilters filters={chips} onClearAll={clearAll} />
          </div>
        )}

        {body}
      </div>

      <FilterDrawer open={filterOpen} onClose={() => setFilterOpen(false)} onClear={clearAll}>
        <RadioGroup<StatusTab>
          label={t('platformTenants.colStatus', 'Holat')}
          value={tab} onChange={setTab}
          options={STATUS_TABS.map((s) => ({ value: s, label: s === 'all' ? t('common.all') : t(`status.${s}`), count: counts[s] }))}
        />
        <RadioGroup<PlanOpt>
          label={t('platformTenants.colPlan', 'Tarif')}
          value={plan} onChange={setPlan}
          options={PLAN_OPTS.map((p) => ({ value: p, label: p === 'all' ? t('common.all') : p.toUpperCase() }))}
        />
        <RadioGroup<LocaleOpt>
          label={t('platformTenants.colLocale', 'Til')}
          value={loc} onChange={setLoc}
          options={LOCALE_OPTS.map((l) => ({ value: l, label: l === 'all' ? t('common.all') : l.toUpperCase() }))}
        />
        <DateRangeGroup value={datePreset} onChange={setDatePreset} />
        <RadioGroup<TenantSort>
          label={t('common.sort', 'Saralash')}
          value={sort} onChange={setSort}
          options={[
            { value: 'new',  label: t('platformTenants.sortNew', 'Eng yangi') },
            { value: 'name', label: t('platformTenants.sortName', 'Nomi (A–Z)') },
          ]}
        />
      </FilterDrawer>

      <CreateTenantModal open={createOpen} onClose={() => setCreateOpen(false)} onCreated={() => { /* list auto-refreshes via query invalidation */ }} />
      <TenantDetailDrawer tenant={drawerRow} onClose={() => setDrawerRow(null)} />
    </>
  );
}
