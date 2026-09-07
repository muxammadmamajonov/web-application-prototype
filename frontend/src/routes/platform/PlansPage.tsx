import { useMemo, useState, type ReactNode } from 'react';
import { useTranslation } from 'react-i18next';
import {
  ChevronRight, Layers,
} from 'lucide-react';
import { useParams } from 'react-router';
import { PageHeader } from '@/components/ui/PageHeader';
import { FloatingCreateAction } from '@/components/ui/FloatingCreateAction';
import { Button } from '@/components/ui/Button';
import { Badge } from '@/components/ui/Badge';
import { SectionCard } from '@/components/ui/SectionCard';
import { EmptyState } from '@/components/ui/EmptyState';
import { DataTable, type Column } from '@/components/ui/DataTable';
import { SkeletonList } from '@/components/ui/Skeleton';
import { ErrorCard } from '@/components/ui/ErrorCard';
import { SearchInput } from '@/components/ui/SearchInput';
import {
  FilterBar, FilterButton, FilterDrawer, ActiveFilters, RadioGroup, RangeGroup,
  matchesRange, rangeActive,
} from '@/components/ui/FilterDrawer';
import { usePlatformPlans, type PlanRow } from '@/lib/queries';
import { money } from '@/lib/format';
import { MobileHeader } from '@/components/mobile/primitives';
import { toast } from '@/lib/feedback';
import { EditPlanModal } from './_modals/EditPlanModal';
import { CreatePlanModal } from './_modals/CreatePlanModal';
import { PlanDetailDrawer } from './_modals/PlanDetailModal';
import { DemoPlanModal } from './_modals/DemoPlanModal';
import { V2_SURFACES_ENABLED } from '@/routes/_scope';

// Mirror of the backend MAX_PLANS cap (app/api/platform/provisioning.py).
const MAX_PLANS = 5;

type StatusFilter = 'all' | 'active' | 'inactive';
type SortKey = 'default' | 'priceAsc' | 'priceDesc' | 'az';

/** Small uppercase plan code pill — consistent shape on table + mobile cards. */
function PlanCodeBadge({ code }: { code: string }) {
  return (
    // `w-fit self-start`: `inline-flex` alone still stretches when the cell
    // around it is a flex container, which is how this pill grew to the full
    // column width and read as an empty input box rather than a code chip.
    // Pinned on the badge itself so it cannot be re-broken by a layout change
    // in any of the three places that render it.
    <span className="inline-flex w-fit shrink-0 self-start items-center rounded-pill border border-line px-2 py-0.5 text-micro font-semibold uppercase tracking-wide text-ink-soft">
      {code}
    </span>
  );
}

/** Mobile plan card — richer than a plain RowCard: name, code, price, status. */
function PlanCard({ plan, lng, onOpen }: { plan: PlanRow; lng: 'uz' | 'ru'; onOpen: () => void }) {
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
        <span className="grid h-10 w-10 shrink-0 place-items-center rounded-control bg-[color:var(--c-accent-soft)] text-accent">
          <Layers size={18} />
        </span>
        <div className="min-w-0 flex-1">
          <div className="truncate font-medium">{lng === 'ru' ? plan.name_ru : plan.name_uz}</div>
          <div className="mt-0.5 flex flex-wrap items-center gap-1.5">
            <PlanCodeBadge code={plan.code} />
            {plan.is_active
              ? <Badge tone="success">{t('common.active', 'Faol')}</Badge>
              : <Badge tone="neutral">{t('common.inactive', 'Nofaol')}</Badge>}
          </div>
        </div>
        <ChevronRight size={16} className="mt-1 shrink-0 text-muted" />
      </div>
      <div className="mt-3 flex items-center justify-between gap-2 border-t border-line pt-3">
        <span className="text-caption text-muted">
          {plan.max_active_installments != null ? plan.max_active_installments : '∞'} {t('platformPlans.activeCap', 'faol limit')} · {plan.max_staff ?? 1} {t('tenantStaff.colUser', 'xodim').toLowerCase()}
        </span>
        <span className="shrink-0 font-mono text-body font-semibold tabular-nums">
          {plan.is_contact_sales ? t('platformPlans.contactPrice', 'Kelishiladi') : money(plan.price_monthly)}
        </span>
      </div>
    </div>
  );
}

/**
 * Plan management — owner-defined plans (R36, free-form codes, capped at 5).
 * Summary KpiCards → Demo plan settings card → filter toolbar → card grid (mobile)
 * / sortable DataTable (desktop). Clicking a plan opens a DetailDrawer with
 * Edit + Delete in the DangerZone — no redundant 3-dot menu.
 */
export default function PlatformPlansPage() {
  const { t, i18n } = useTranslation();
  const { locale } = useParams();
  const lng = (locale ?? i18n.language) as 'uz' | 'ru';
  const [detail, setDetail] = useState<PlanRow | null>(null);
  const [editing, setEditing] = useState<PlanRow | null>(null);
  const [creating, setCreating] = useState(false);
  const [demoOpen, setDemoOpen] = useState(false);
  const query = usePlatformPlans();
  const plans = useMemo(() => query.data ?? [], [query.data]);

  // Client-side toolbar/drawer (gold-standard pattern). No new server queries —
  // the plan list is tiny (capped at 5), so search + filters run over loaded rows.
  const [search, setSearch] = useState('');
  const [status, setStatus] = useState<StatusFilter>('all');
  const [priceRange, setPriceRange] = useState({ min: '', max: '' });
  const [sort, setSort] = useState<SortKey>('default');
  const [filterOpen, setFilterOpen] = useState(false);

  const counts: Record<StatusFilter, number> = {
    all: plans.length,
    active: plans.filter((p) => p.is_active).length,
    inactive: plans.filter((p) => !p.is_active).length,
  };

  const filteredPlans = useMemo(() => {
    const q = search.trim().toLowerCase();
    return plans
      .filter((p) => status === 'all' || (status === 'active' ? p.is_active : !p.is_active))
      .filter((p) => !q
        || (lng === 'ru' ? p.name_ru : p.name_uz).toLowerCase().includes(q)
        || p.code.toLowerCase().includes(q))
      .filter((p) => matchesRange(p.price_monthly, priceRange))
      .slice()
      .sort((a, b) => {
        if (sort === 'priceAsc') return a.price_monthly - b.price_monthly;
        if (sort === 'priceDesc') return b.price_monthly - a.price_monthly;
        if (sort === 'az') return (lng === 'ru' ? a.name_ru : a.name_uz).localeCompare(lng === 'ru' ? b.name_ru : b.name_uz);
        return 0; // 'default' — keep server order
      });
  }, [plans, status, search, priceRange, sort, lng]);

  const activeFilters = (status !== 'all' ? 1 : 0) + (rangeActive(priceRange) ? 1 : 0) + (sort !== 'default' ? 1 : 0);
  const resetAll = () => { setStatus('all'); setPriceRange({ min: '', max: '' }); setSort('default'); };
  const clearChips = [
    status !== 'all' && { key: 'status', label: t(`common.${status}`, status), onRemove: () => setStatus('all') },
    rangeActive(priceRange) && { key: 'price', label: t('platformPlans.filterPrice', 'Oylik narx'), onRemove: () => setPriceRange({ min: '', max: '' }) },
    sort !== 'default' && { key: 'sort', label: t(`platformPlans.sort${sort.charAt(0).toUpperCase() + sort.slice(1)}`, sort), onRemove: () => setSort('default') },
  ].filter(Boolean) as { key: string; label: string; onRemove: () => void }[];

  const atCap = plans.length >= MAX_PLANS;
  const onAdd = () => {
    if (atCap) { toast.info(t('platformPlans.limitReached', "Eng ko'pi 5 ta tarif yaratish mumkin.")); return; }
    setCreating(true);
  };

  // Trimmed to 5 columns; phone lives in mobile cards. (H17) memoized so an
  // unrelated re-render doesn't rebuild this array.
  const cols: Column<PlanRow>[] = useMemo(() => [
    {
      key: 'name', label: t('platformPlans.colName', 'Nomi'), width: '1.6fr',
      render: (p) => (
        <span className="flex items-center gap-3">
          <span className="grid h-8 w-8 shrink-0 place-items-center rounded-control bg-[color:var(--c-accent-soft)] text-accent">
            <Layers size={16} />
          </span>
          <span className="block truncate font-medium">{lng === 'ru' ? p.name_ru : p.name_uz}</span>
        </span>
      ),
      sortValue: (p) => (lng === 'ru' ? p.name_ru : p.name_uz).toLowerCase(),
    },
    {
      key: 'code', label: t('platformPlans.colCode', 'Kod'), width: '1.2fr',
      render: (p) => (
        <span className="flex flex-col gap-0.5">
          <PlanCodeBadge code={p.code} />
          <span className="text-micro text-muted">
            {p.max_active_installments != null ? p.max_active_installments : '∞'} {t('platformPlans.activeCap', 'faol')} · {p.max_staff ?? 1} {t('tenantStaff.colUser', 'xodim').toLowerCase()}
          </span>
        </span>
      ),
    },
    {
      key: 'price', label: t('platformPlans.filterPrice', 'Oylik narx'), width: '140px', align: 'right',
      render: (p) => (
        <span className="tabular-nums font-medium">
          {p.is_contact_sales ? t('platformPlans.contactPrice', 'Kelishiladi') : money(p.price_monthly)}
        </span>
      ),
      sortValue: (p) => p.price_monthly,
    },
    {
      key: 'status', label: t('platformPlans.filterStatus', 'Holat'), width: '110px',
      render: (p) => (p.is_active
        ? <Badge tone="success">{t('common.active', 'Faol')}</Badge>
        : <Badge tone="neutral">{t('common.inactive', 'Nofaol')}</Badge>),
      sortValue: (p) => (p.is_active ? 1 : 0),
    },
    {
      key: 'chevron', label: '', width: '28px', align: 'right',
      render: () => <ChevronRight size={16} className="text-muted" />,
    },
  ], [t, lng]);

  // (#6) The summary strip and KpiCard grid are gone: on a list page they are
  // a preamble the reader scrolls past to reach the rows they came for, and on
  // a phone they cost most of the first paint. The figures live on Overview.


  // ── Body ──────────────────────────────────────────────────────────────────
  let body: ReactNode;
  if (query.isLoading) {
    body = <SkeletonList rows={4} />;
  } else if (query.isError) {
    body = <ErrorCard error={query.error} onRetry={() => query.refetch()} />;
  } else if (plans.length === 0) {
    body = (
      <EmptyState
        tone="accent"
        size="compact"
        icon={<Layers size={24} />}
        title={t('platformPlans.emptyTitle', "Hali tariflar yo'q")}
        body={t('platformPlans.emptyBody', 'Birinchi tarifingizni yarating — kod, nom va oylik narx bilan.')}
      />
    );
  } else if (filteredPlans.length === 0) {
    body = (
      <EmptyState
        size="compact"
        icon={<Layers size={24} />}
        title={t('common.noResults', 'Topilmadi')}
        body={t('platformPlans.noResultsBody', 'Filtr yoki qidiruvga mos tarif topilmadi.')}
        action={(activeFilters > 0 || search.trim())
          ? <Button variant="outline" size="md" onClick={() => { resetAll(); setSearch(''); }}>{t('common.clear', 'Tozalash')}</Button>
          : undefined}
      />
    );
  } else {
    body = (
      <>
        {/* Desktop (lg+): dense sortable + clickable table. */}
        <div className="hidden lg:block">
          <DataTable columns={cols} rows={filteredPlans} rowKey={(p) => p.id} onRowClick={(p) => setDetail(p)} />
        </div>
        {/* Mobile + tablet: rich plan cards. */}
        <ul className="space-y-2.5 lg:hidden">
          {filteredPlans.map((p) => (
            <li key={p.id}>
              <PlanCard plan={p} lng={lng} onOpen={() => setDetail(p)} />
            </li>
          ))}
        </ul>
      </>
    );
  }

  return (
    <>
      {/* Desktop header */}
      <div className="hidden md:block">
        <PageHeader
          title={t('nav.plans', 'Tariflar')}
          subtitle={t('platformPlans.headerSub', { used: plans.length, max: MAX_PLANS, defaultValue: `${plans.length}/${MAX_PLANS} tarif ishlatilgan` })}
          actions={
            atCap ? (
              <span className="inline-flex items-center rounded-pill border border-line bg-cream px-3 py-1.5 text-caption font-medium text-muted">
                {t('platformPlans.capBadge', { used: plans.length, max: MAX_PLANS, defaultValue: `Limit ${plans.length}/${MAX_PLANS}` })}
              </span>
            ) : undefined
          }
        />
      </div>

      <div className="md:hidden">
        <MobileHeader title={t('nav.plans', 'Tariflar')} />
        <p className="mb-3 max-w-prose text-small leading-snug text-muted">
          {t('platformPlans.headerSub', { used: plans.length, max: MAX_PLANS, defaultValue: `${plans.length}/${MAX_PLANS} tarif ishlatilgan` })}
        </p>
      </div>

      {/* At the plan cap the button stays VISIBLE and disabled rather than
          disappearing: hiding it would take the tooltip explaining the limit
          with it, leaving the user hunting for a control that moved. */}
      <FloatingCreateAction
        onClick={onAdd}
        label={t('platformPlans.add', 'Yangi tarif')}
        disabled={atCap}
        title={atCap ? t('platformPlans.limitReached', "Eng ko'pi 5 ta tarif yaratish mumkin.") : undefined}
      />

      <div className="space-y-4 sm:space-y-5">
        {/* KPI row — shown once data has loaded successfully. A failed fetch
            with no cached plans must never render "0 jami / 0 faol / 5 bo'sh
            slot" — that reads as a clean, empty-but-fine tariff book instead of
            a load failure, and "5 bo'sh slot" in particular would actively
            mislead an operator into thinking they have free plan slots. The
            ErrorCard rendered below (via `body`) already covers the failure. */}
        {/* Demo plan — a deliberate settings card (not one of the 5 owner-defined
            plans). Reads as an intentional, labelled setting: icon + title +
            helper + a right chevron that says "opens a panel".
            Configures the deferred self-serve demo funnel, so it is not
            presented beside live plan management. */}
        {V2_SURFACES_ENABLED && (
        <SectionCard
          icon={<Layers size={16} />}
          title={t('demoPlan.title', 'Demo (sinov) tarifi')}
          meta={t('demoPlan.cardHint', 'Sinov muddati, cheklovlar va tugash xatti-harakatini boshqaring')}
          action={
            <button
              type="button"
              onClick={() => setDemoOpen(true)}
              className="inline-flex items-center gap-1.5 rounded-icon px-2 py-1 text-caption font-medium text-accent hover:underline"
            >
              {t('common.settings', 'Sozlamalar')} <ChevronRight size={14} />
            </button>
          }
        >
          <button
            type="button"
            onClick={() => setDemoOpen(true)}
            className="flex w-full items-center justify-between gap-3 rounded-control border border-line bg-cream px-4 py-3 text-left transition hover:border-accent/40 hover:bg-[color:var(--c-accent-soft)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ink/20"
          >
            <span className="flex items-center gap-3">
              <span className="grid h-9 w-9 shrink-0 place-items-center rounded-control bg-[color:var(--c-accent-soft)] text-accent">
                <Layers size={18} />
              </span>
              <span className="min-w-0">
                <span className="block text-small font-semibold">{t('demoPlan.title', 'Demo (sinov) tarifi')}</span>
                <span className="block text-caption text-muted">{t('demoPlan.cardHint', 'Sinov muddati, cheklovlar va tugash xatti-harakatini boshqaring')}</span>
              </span>
            </span>
            <span className="flex items-center gap-2">
              <Badge tone="accent">{t('demoPlan.badge', 'Demo / sinov')}</Badge>
              <ChevronRight size={16} className="shrink-0 text-muted" />
            </span>
          </button>
        </SectionCard>
        )}

        {/* Filter toolbar — only shown when there is something to filter. */}
        {!query.isError && (query.isLoading || plans.length > 0) && (
          <div>
            <FilterBar>
              <SearchInput value={search} onChange={setSearch} className="flex-1"
                placeholder={t('platformPlans.searchPh', "Tarif nomi yoki kodi bo'yicha qidirish…")} />
              <FilterButton count={activeFilters} onClick={() => setFilterOpen(true)} />
            </FilterBar>
            <ActiveFilters filters={clearChips} onClearAll={resetAll} />
          </div>
        )}

        {/* Loading / error / empty / list — single source of truth in `body`. */}
        {body}
      </div>

      <FilterDrawer open={filterOpen} onClose={() => setFilterOpen(false)} onClear={resetAll}>
        <RadioGroup<StatusFilter>
          label={t('platformPlans.filterStatus', 'Holat')}
          value={status}
          onChange={setStatus}
          options={[
            { value: 'all', label: t('common.all', 'Hammasi'), count: counts.all },
            { value: 'active', label: t('common.active', 'Faol'), count: counts.active },
            { value: 'inactive', label: t('common.inactive', 'Nofaol'), count: counts.inactive },
          ]}
        />
        <RangeGroup
          label={t('platformPlans.filterPrice', 'Oylik narx')}
          unit={t('billing.currency', "so'm")}
          value={priceRange}
          onChange={setPriceRange}
        />
        <RadioGroup<SortKey>
          label={t('common.sort', 'Saralash')}
          value={sort}
          onChange={setSort}
          options={[
            { value: 'default', label: t('platformPlans.sortDefault', 'Standart') },
            { value: 'priceAsc', label: t('platformPlans.sortPriceAsc', 'Narx (arzon)') },
            { value: 'priceDesc', label: t('platformPlans.sortPriceDesc', 'Narx (qimmat)') },
            { value: 'az', label: t('platformPlans.sortAz', 'Nomi (A–Z)') },
          ]}
        />
      </FilterDrawer>

      <PlanDetailDrawer
        plan={detail}
        onClose={() => setDetail(null)}
        onEdit={(p) => { setDetail(null); setEditing(p); }}
      />
      <EditPlanModal open={!!editing} onClose={() => setEditing(null)} plan={editing} />
      <CreatePlanModal open={creating} onClose={() => setCreating(false)} />
      <DemoPlanModal open={demoOpen} onClose={() => setDemoOpen(false)} />
    </>
  );
}
