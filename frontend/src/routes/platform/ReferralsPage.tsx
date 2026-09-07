import { useMemo, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { ChevronRight, Copy, Gift, Share2, CheckCircle2 } from 'lucide-react';
import { PageHeader } from '@/components/ui/PageHeader';
import { Button } from '@/components/ui/Button';
import { DesktopActionBar } from '@/components/layout/TopBar';
import { Badge } from '@/components/ui/Badge';
import { SectionCard } from '@/components/ui/SectionCard';
import { EmptyState } from '@/components/ui/EmptyState';
import { DataTable, type Column } from '@/components/ui/DataTable';
import { SkeletonList } from '@/components/ui/Skeleton';
import { ErrorCard } from '@/components/ui/ErrorCard';
import { DetailModal, DefList, DefRow } from '@/components/ui/DetailModal';
import { SearchInput } from '@/components/ui/SearchInput';
import {
  FilterBar, FilterButton, FilterDrawer, ActiveFilters, RadioGroup, RangeGroup, DateRangeGroup,
  matchesDatePreset, matchesRange, rangeActive, type DatePreset,
} from '@/components/ui/FilterDrawer';
import { usePlatformReferrals, usePlatformReferralLink, type ReferralRow } from '@/lib/queries';
import { useDeleteReferral } from '@/lib/mutations';
import { money, shortDate } from '@/lib/format';
import { shareViaTelegram } from '@/lib/telegram';
import { MobileHeader } from '@/components/mobile/primitives';
import { confirmDialog } from '@/lib/feedback';

type StatusFilter = 'all' | 'pending' | 'converted';
type SortKey = 'new' | 'old' | 'credit';

/** Mobile referral card — identity + status + bonus, clear tap-to-open affordance. */
function ReferralCard({ row, onOpen }: { row: ReferralRow; onOpen: () => void }) {
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
        <span className="grid h-10 w-10 shrink-0 place-items-center rounded-xl bg-[color:var(--c-accent-soft)] text-accent font-semibold text-small select-none">
          {(row.referrer_name ?? '#').slice(0, 1).toUpperCase()}
        </span>
        <div className="min-w-0 flex-1">
          <div className="truncate font-medium">{row.referrer_name ?? '—'}</div>
          <div className="truncate text-caption text-muted">
            {t('platformReferrals.arrow', '→')} {row.referee_name ?? '—'}
          </div>
        </div>
        <ChevronRight size={16} className="mt-1 shrink-0 text-muted" />
      </div>
      <div className="mt-3 flex flex-wrap items-center justify-between gap-2 border-t border-line pt-3">
        <Badge tone={row.status === 'converted' ? 'success' : 'warn'}>
          {t(`platformReferrals.status.${row.status}`, row.status)}
        </Badge>
        <span className="tabular-nums text-caption font-medium">
          {money(row.credit_amount)} {t('billing.currency')}
        </span>
        <span className="shrink-0 tabular-nums text-caption text-muted">{shortDate(row.created_at)}</span>
      </div>
    </div>
  );
}

export default function PlatformReferralsPage() {
  const { t } = useTranslation();
  const q = usePlatformReferrals();
  const allRows = useMemo(() => q.data ?? [], [q.data]);
  const linkQ = usePlatformReferralLink();
  const link = linkQ.data?.share_url ?? '';
  const [copied, setCopied] = useState(false);
  const [detail, setDetail] = useState<ReferralRow | null>(null);
  const del = useDeleteReferral();

  const [search, setSearch] = useState('');
  const [status, setStatus] = useState<StatusFilter>('all');
  const [creditRange, setCreditRange] = useState({ min: '', max: '' });
  const [datePreset, setDatePreset] = useState<DatePreset>('all');
  const [sort, setSort] = useState<SortKey>('new');
  const [filterOpen, setFilterOpen] = useState(false);

  // ── Aggregates ────────────────────────────────────────────────────────────
  const counts = useMemo(() => ({
    all: allRows.length,
    pending: allRows.filter((r) => r.status === 'pending').length,
    converted: allRows.filter((r) => r.status === 'converted').length,
  }), [allRows]);


  // ── Filtered + sorted rows ────────────────────────────────────────────────
  const rows = useMemo(() => {
    const needle = search.trim().toLowerCase();
    return allRows
      .filter((r) => status === 'all' || r.status === status)
      .filter((r) => matchesRange(r.credit_amount, creditRange))
      .filter((r) => matchesDatePreset(r.created_at, datePreset))
      .filter((r) => !needle || [r.referrer_name, r.referee_name, r.notes]
        .some((v) => v?.toLowerCase().includes(needle)))
      .slice()
      .sort((a, b) => {
        if (sort === 'credit') return b.credit_amount - a.credit_amount;
        const d = (a.created_at ?? '').localeCompare(b.created_at ?? '');
        return sort === 'old' ? d : -d;
      });
  }, [allRows, search, status, creditRange, datePreset, sort]);

  const activeFilters = (status !== 'all' ? 1 : 0) + (rangeActive(creditRange) ? 1 : 0)
    + (datePreset !== 'all' ? 1 : 0) + (sort !== 'new' ? 1 : 0);
  const clearFilters = () => {
    setStatus('all');
    setCreditRange({ min: '', max: '' });
    setDatePreset('all');
    setSort('new');
  };

  const dateLabel: Record<DatePreset, string> = {
    all: '', today: t('filters.today', 'Bugun'), '7d': t('filters.last7', '7 kun'),
    '30d': t('filters.last30', '30 kun'), '90d': t('filters.last90', '90 kun'),
  };

  const chips = [
    status !== 'all' && { key: 'status', label: t(`platformReferrals.status.${status}`, status), onRemove: () => setStatus('all') },
    rangeActive(creditRange) && { key: 'credit', label: t('platformReferrals.colCredit', 'Bonus'), onRemove: () => setCreditRange({ min: '', max: '' }) },
    datePreset !== 'all' && { key: 'date', label: dateLabel[datePreset], onRemove: () => setDatePreset('all') },
    sort !== 'new' && {
      key: 'sort',
      label: sort === 'old'
        ? t('platformReferrals.sortOld', 'Eng eski')
        : t('platformReferrals.sortCredit', 'Bonus (katta)'),
      onRemove: () => setSort('new'),
    },
  ].filter(Boolean) as { key: string; label: string; onRemove: () => void }[];

  // ── Mutations ─────────────────────────────────────────────────────────────
  const remove = async (r: ReferralRow) => {
    if (del.isPending) return;
    if (!(await confirmDialog({
      message: t('platformReferrals.confirmDelete', "Bu referalni o'chirilsinmi?"),
      danger: true,
      confirmLabel: t('common.delete'),
    }))) return;
    del.mutate(r.id, { onSuccess: () => setDetail(null) });
  };

  // ── Share / copy ──────────────────────────────────────────────────────────
  const flashCopied = () => { setCopied(true); setTimeout(() => setCopied(false), 1800); };

  const share = () => {
    if (!link) return;
    const text = t('platformReferrals.shareText', "Muddato — bo'lib to'lash xizmatiga taklif");
    if (shareViaTelegram(link, text)) return;
    if (typeof navigator !== 'undefined' && navigator.share) {
      void navigator.share({ url: link, text }).catch(() => { void navigator.clipboard?.writeText(link); flashCopied(); });
      return;
    }
    void navigator.clipboard?.writeText(link);
    flashCopied();
  };

  const copy = async () => {
    if (!link) return;
    await navigator.clipboard.writeText(link);
    flashCopied();
  };

  // ── Desktop table columns ─────────────────────────────────────────────────
  // Kept at lg breakpoint (same as Tenants) — below 1024 the sidebar leaves too
  // little width for a comfortable table, so cards are shown through tablet.
  // (H17) memoized so an unrelated re-render doesn't rebuild this array.
  const cols: Column<ReferralRow>[] = useMemo(() => [
    {
      key: 'referrer', label: t('platformReferrals.colReferrer', 'Taklif qilgan'), width: '1.4fr',
      render: (r) => <span className="block truncate font-medium">{r.referrer_name ?? '—'}</span>,
      sortValue: (r) => (r.referrer_name ?? '').toLowerCase(),
    },
    {
      key: 'referee', label: t('platformReferrals.colReferee', 'Taklif qilingan'), width: '1.4fr',
      render: (r) => <span className="block truncate text-small text-muted">{r.referee_name ?? '—'}</span>,
      sortValue: (r) => (r.referee_name ?? '').toLowerCase(),
    },
    {
      key: 'status', label: t('platformReferrals.colStatus', 'Holat'), width: '120px',
      render: (r) => (
        <Badge tone={r.status === 'converted' ? 'success' : 'warn'}>
          {t(`platformReferrals.status.${r.status}`, r.status)}
        </Badge>
      ),
      sortValue: (r) => r.status,
    },
    {
      key: 'credit', label: t('platformReferrals.colCredit', 'Bonus'), width: '130px', align: 'right',
      render: (r) => <span className="tabular-nums text-small">{money(r.credit_amount)}</span>,
      sortValue: (r) => r.credit_amount,
    },
    {
      key: 'date', label: t('platformReferrals.colDate', 'Sana'), width: '110px', align: 'right',
      render: (r) => <span className="tabular-nums text-small text-muted">{shortDate(r.created_at)}</span>,
      sortValue: (r) => r.created_at ?? '',
    },
    {
      key: 'chevron', label: '', width: '32px', align: 'right',
      render: () => <ChevronRight size={16} className="text-muted" />,
    },
  ], [t]);

  // (#6) The summary strip and KpiCard grid are gone: on a list page they are
  // a preamble the reader scrolls past to reach the rows they came for, and on
  // a phone they cost most of the first paint. The figures live on Overview.


  const searchOrFilter = !!search.trim() || activeFilters > 0;
  const showToolbar = q.isLoading || allRows.length > 0 || searchOrFilter;

  // ── List body ─────────────────────────────────────────────────────────────
  let listBody;
  if (q.isLoading) {
    listBody = <SkeletonList rows={5} />;
  } else if (q.isError) {
    listBody = <ErrorCard error={q.error} onRetry={() => q.refetch()} />;
  } else if (rows.length === 0 && searchOrFilter) {
    listBody = (
      <EmptyState
        size="compact"
        icon={<Gift size={24} />}
        title={t('common.noResults', 'Topilmadi')}
        body={t('platformReferrals.emptyFilteredHint', "Filtrlarni tozalab, barcha referallarni ko'ring")}
        action={(
          <Button variant="outline" size="md" onClick={() => { clearFilters(); setSearch(''); }}>
            {t('common.clear', 'Tozalash')}
          </Button>
        )}
      />
    );
  } else if (rows.length === 0) {
    listBody = (
      <EmptyState
        tone="accent"
        size="compact"
        icon={<Gift size={24} />}
        title={t('platformReferrals.emptyTitle')}
        body={t('platformReferrals.emptyBody')}
        action={(
          <Button variant="primary" size="md" onClick={share} disabled={!link}>
            <Share2 size={16} /> {t('common.share', 'Ulashish')}
          </Button>
        )}
      />
    );
  } else {
    listBody = (
      <>
        {/* Desktop (lg+): sortable, paginated table. */}
        <div className="hidden lg:block">
          <DataTable columns={cols} rows={rows} rowKey={(r) => r.id} onRowClick={(r) => setDetail(r)} />
        </div>
        {/* Mobile + tablet: rich cards (never the squeezed table). */}
        <ul className="space-y-2.5 lg:hidden">
          {rows.map((r) => (
            <li key={r.id}>
              <ReferralCard row={r} onOpen={() => setDetail(r)} />
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
          title={t('nav.referrals')}
          subtitle={t('platformReferrals.pageSubtitle', 'Taklif dasturi va hisoblangan bonuslar')}
          actions={<DesktopActionBar />}
        />
      </div>
      {/* Mobile header */}
      <div className="md:hidden">
        <MobileHeader title={t('nav.referrals')} />
        <p className="mb-3 max-w-prose text-small leading-snug text-muted">
          {t('platformReferrals.pageSubtitle', 'Taklif dasturi va hisoblangan bonuslar')}
        </p>
      </div>

      <div className="space-y-4 sm:space-y-5">
        {/* KPI summary — compact strip on phones, full KpiCards from md up. Hidden
            on a failed fetch: a stale/empty count set would read as "0 referrals,
            $0 bonus" — a clean empty program — instead of a load failure. The
            ErrorCard rendered below (via `listBody`) already covers the failure. */}
        {/* Invite link panel — stable canonical link per admin. */}
        <SectionCard
          icon={<Share2 size={16} />}
          title={t('platformReferrals.myLink', 'Mening taklif havolam')}
          meta={t('platformReferrals.myLinkMeta', 'Yangi bizneslarni platformaga jalb qilish uchun ulashing')}
        >
          <div className="flex flex-col gap-2 sm:flex-row sm:items-center">
            <code className="min-w-0 flex-1 truncate rounded-xl border border-line bg-cream px-3 py-2 font-mono text-caption">
              {link || '…'}
            </code>
            <div className="flex shrink-0 items-center gap-2">
              <Button
                size="sm"
                variant={copied ? 'primary' : 'outline'}
                onClick={copy}
                disabled={!link}
                className={copied ? 'border-success bg-success text-white hover:bg-success' : undefined}
              >
                {copied ? <CheckCircle2 size={14} /> : <Copy size={14} />}
                {copied ? t('common.copied', 'Nusxalandi') : t('common.copy', 'Nusxa olish')}
              </Button>
              <Button size="sm" variant="primary" onClick={share} disabled={!link}>
                <Share2 size={14} /> {t('common.share', 'Ulashish')}
              </Button>
            </div>
          </div>
        </SectionCard>

        {/* Referral list — toolbar + table/cards. */}
        {showToolbar && (
          <div>
            <FilterBar>
              <SearchInput
                value={search}
                onChange={setSearch}
                className="flex-1"
                placeholder={t('platformReferrals.searchPh', "Taklif qilgan yoki taklif qilingan bo'yicha…")}
              />
              <FilterButton count={activeFilters} onClick={() => setFilterOpen(true)} />
            </FilterBar>
            <ActiveFilters filters={chips} onClearAll={clearFilters} />
          </div>
        )}

        {listBody}
      </div>

      {/* Filter drawer */}
      <FilterDrawer open={filterOpen} onClose={() => setFilterOpen(false)} onClear={clearFilters}>
        <RadioGroup<StatusFilter>
          label={t('platformReferrals.colStatus', 'Holat')}
          value={status}
          onChange={setStatus}
          options={[
            { value: 'all', label: t('common.all', 'Hammasi'), count: counts.all },
            { value: 'pending', label: t('platformReferrals.status.pending', 'Kutilmoqda'), count: counts.pending },
            { value: 'converted', label: t('platformReferrals.status.converted', 'Faollashgan'), count: counts.converted },
          ]}
        />
        <RangeGroup
          label={t('platformReferrals.colCredit', 'Bonus')}
          unit={t('billing.currency')}
          value={creditRange}
          onChange={setCreditRange}
        />
        <DateRangeGroup value={datePreset} onChange={setDatePreset} />
        <RadioGroup<SortKey>
          label={t('common.sort', 'Saralash')}
          value={sort}
          onChange={setSort}
          options={[
            { value: 'new', label: t('platformReferrals.sortNew', 'Eng yangi') },
            { value: 'old', label: t('platformReferrals.sortOld', 'Eng eski') },
            { value: 'credit', label: t('platformReferrals.sortCredit', 'Bonus (katta)') },
          ]}
        />
      </FilterDrawer>

      {/* Detail modal — read-only; delete is in the DangerZone footer. */}
      <DetailModal
        open={!!detail}
        onClose={() => setDetail(null)}
        title={detail?.referrer_name ?? t('nav.referrals')}
        subtitle={detail ? (
          <Badge tone={detail.status === 'converted' ? 'success' : 'warn'}>
            {t(`platformReferrals.status.${detail.status}`, detail.status)}
          </Badge>
        ) : undefined}
        onDelete={detail ? () => remove(detail) : undefined}
        deleting={del.isPending}
      >
        {detail && (
          <DefList>
            <DefRow label={t('platformReferrals.colReferrer', 'Taklif qilgan')}>{detail.referrer_name}</DefRow>
            <DefRow label={t('platformReferrals.colReferee', 'Taklif qilingan')}>{detail.referee_name}</DefRow>
            <DefRow label={t('platformReferrals.colStatus', 'Holat')}>
              <Badge tone={detail.status === 'converted' ? 'success' : 'warn'}>
                {t(`platformReferrals.status.${detail.status}`, detail.status)}
              </Badge>
            </DefRow>
            <DefRow label={t('platformReferrals.colCredit', 'Bonus')}>
              {money(detail.credit_amount)} {t('billing.currency')}
            </DefRow>
            <DefRow label={t('platformReferrals.colDate', 'Sana')}>{shortDate(detail.created_at)}</DefRow>
            <DefRow label={t('platformReferrals.colNotes', 'Izoh')}>{detail.notes}</DefRow>
          </DefList>
        )}
      </DetailModal>
    </>
  );
}
