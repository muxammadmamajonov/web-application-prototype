import { useMemo, useState, type ReactNode } from 'react';
import { useTranslation } from 'react-i18next';
import { Users } from 'lucide-react';
import { PageHeader } from '@/components/ui/PageHeader';
import { MobileHeader } from '@/components/mobile/primitives';
import { SearchInput } from '@/components/ui/SearchInput';
import {
  FilterBar, FilterButton, FilterDrawer, ActiveFilters, RadioGroup,
} from '@/components/ui/FilterDrawer';
import { ErrorCard } from '@/components/ui/ErrorCard';
import { EmptyState } from '@/components/ui/EmptyState';
import { Badge } from '@/components/ui/Badge';
import { Button } from '@/components/ui/Button';
import { Avatar } from '@/components/ui/Avatar';
import { DataTable, type Column } from '@/components/ui/DataTable';
import { SkeletonList } from '@/components/ui/Skeleton';
import { usePlatformSegments, usePlatformUsers, type PlatformUserRow, type SegmentRow } from '@/lib/queries';
import { displayName, relativeFromNow } from '@/lib/format';
import { displayPhone } from '@/lib/phone';

const PAGE = 50;
const LOCALE_OPTS = ['all', 'uz', 'ru'] as const;
type LocaleOpt = (typeof LOCALE_OPTS)[number];
type RoleOpt = 'all' | 'client' | 'platform' | 'tenant';
type UserSort = 'default' | 'name' | 'seen';

function segLabel(s: SegmentRow, lng: string): string {
  return lng.startsWith('ru') ? s.label_ru : s.label_uz;
}

/** Row → the shared display name. The `#12345` tail this used to carry put a
 *  raw Telegram id on screen for a nameless, handle-less user; `displayName`
 *  ends at the em-dash instead — see it for the one chain used everywhere. */
function userName(u: PlatformUserRow): string {
  return displayName({ first: u.first_name, last: u.last_name, username: u.telegram_username });
}

/** Mobile user card — identity, role/status badges, phone, last-seen. Read-only
 *  directory entry (no detail surface to open yet), so it carries no
 *  ChevronRight drill-in affordance — see DataTable's `cols` below for the
 *  matching desktop rationale. */
function UserCard({ u, t }: { u: PlatformUserRow; t: ReturnType<typeof useTranslation>['t'] }) {
  const name = userName(u);
  return (
    <div className="rounded-card border border-line bg-surface p-4 transition hover:border-ink/15 hover:shadow-card">
      <div className="flex items-start gap-3">
        <Avatar name={name} size="md" />
        <div className="min-w-0 flex-1">
          <div className="truncate font-medium">{name}</div>
          <div className="truncate text-caption text-muted">
            {displayPhone(u.phone) || '—'}
            {u.telegram_username ? ` · @${u.telegram_username}` : ''}
          </div>
        </div>
      </div>
      <div className="mt-3 flex flex-wrap items-center gap-2">
        {u.is_client && <Badge tone="success">{t('platformUsers.client', 'Mijoz')}</Badge>}
        {u.platform_role && (
          <Badge tone="neutral">{t(`platformAdmins.roles.${u.platform_role}`, u.platform_role)}</Badge>
        )}
        {u.memberships.length > 0 && (
          <Badge tone="info">{t('platformUsers.roleTenant', 'Doʻkon aʻzosi')}</Badge>
        )}
        {u.locale && (
          <span className="text-micro font-semibold uppercase tracking-wide text-muted">{u.locale}</span>
        )}
      </div>
      {(u.memberships.length > 0 || u.last_seen_at) && (
        <div className="mt-3 flex items-center justify-between gap-2 border-t border-line pt-3 text-caption text-muted">
          <span className="min-w-0 truncate">
            {u.memberships.length > 0
              ? u.memberships.map((m) => m.tenant).join(', ')
              : '—'}
          </span>
          {u.last_seen_at && (
            <span className="shrink-0 tabular-nums">{relativeFromNow(u.last_seen_at)}</span>
          )}
        </div>
      )}
    </div>
  );
}

/** Platform → the full Muddato user directory, filterable by segment + search.
 *  Doubles as the audience picker reference for the broadcast composer. */
export default function PlatformUsersPage() {
  const { t, i18n } = useTranslation();
  const lng = i18n.language;
  const segs = usePlatformSegments();
  const [segment, setSegment] = useState('all');
  const [loc, setLoc] = useState<LocaleOpt>('all');
  const [role, setRole] = useState<RoleOpt>('all');
  const [sort, setSort] = useState<UserSort>('default');
  const [filterOpen, setFilterOpen] = useState(false);
  const [q, setQ] = useState('');
  const [debouncedQ, setDebouncedQ] = useState('');
  const [page, setPage] = useState(0);

  const users = usePlatformUsers({ segment, q: debouncedQ, limit: PAGE, offset: page * PAGE });
  const total = users.data?.total ?? 0;
  const lastPage = Math.max(0, Math.ceil(total / PAGE) - 1);

  // Segment + search are server-side (paginated). Locale/role/sort refine the
  // loaded page client-side, matching the platform truncation convention.
  const items = useMemo(() => (users.data?.items ?? []).filter((u) => {
    if (loc !== 'all' && (u.locale ?? '').toLowerCase() !== loc) return false;
    if (role === 'client' && !u.is_client) return false;
    if (role === 'platform' && !u.platform_role) return false;
    if (role === 'tenant' && u.memberships.length === 0) return false;
    return true;
  }).slice().sort((a, b) => {
    if (sort === 'name') {
      const an = userName(a);
      const bn = userName(b);
      return an.localeCompare(bn);
    }
    if (sort === 'seen') return (b.last_seen_at ?? '').localeCompare(a.last_seen_at ?? '');
    return 0;
  }), [users.data, loc, role, sort]);

  const activeFilters = (segment !== 'all' ? 1 : 0) + (loc !== 'all' ? 1 : 0) + (role !== 'all' ? 1 : 0) + (sort !== 'default' ? 1 : 0);
  const clearAll = () => { setSegment('all'); setLoc('all'); setRole('all'); setSort('default'); setPage(0); };

  const pick = (key: string) => { setSegment(key); setPage(0); };
  const subtitle = t('platformUsers.subtitle', 'Barcha Muddato foydalanuvchilari');

  const roleLabel: Record<RoleOpt, string> = {
    all: '',
    client: t('platformUsers.client', 'Mijoz'),
    platform: t('platformUsers.roleStaff', 'Platforma'),
    tenant: t('platformUsers.roleTenant', 'Doʻkon aʻzosi'),
  };
  const sortLabel: Record<UserSort, string> = {
    default: '',
    name: t('platformUsers.sortName', 'Ism (A–Z)'),
    seen: t('platformUsers.sortSeen', 'Oxirgi faollik'),
  };
  const segName = (segs.data ?? []).find((s) => s.key === segment);
  const chips = [
    segment !== 'all' && { key: 'segment', label: segName ? segLabel(segName, lng) : segment, onRemove: () => pick('all') },
    role !== 'all' && { key: 'role', label: roleLabel[role], onRemove: () => setRole('all') },
    loc !== 'all' && { key: 'loc', label: loc.toUpperCase(), onRemove: () => setLoc('all') },
    sort !== 'default' && { key: 'sort', label: sortLabel[sort], onRemove: () => setSort('default') },
  ].filter(Boolean) as { key: string; label: string; onRemove: () => void }[];

  const searchOrFilter = !!debouncedQ || activeFilters > 0;

  // ── Derived KPI counts from the current page (server paginates; these
  //    reflect only the loaded window — good enough for the summary bar).

  // (#6) Summary strip and KpiCard grid removed — on a list page they are a
  // preamble the reader scrolls past. The figures live on Overview.

  // ── Desktop DataTable columns ────────────────────────────────────────────── (H17)
  const cols: Column<PlatformUserRow>[] = useMemo(() => [
    {
      key: 'name',
      label: t('platformUsers.colName', 'Foydalanuvchi'),
      width: '2fr',
      truncate: false,
      render: (u) => {
        const name = userName(u);
        return (
          <span className="flex min-w-0 items-center gap-3">
            <Avatar name={name} size="sm" />
            <span className="min-w-0">
              <span className="block truncate font-medium">{name}</span>
              <span className="block truncate text-micro text-muted">
                {displayPhone(u.phone) || '—'}
                {u.telegram_username ? ` · @${u.telegram_username}` : ''}
              </span>
            </span>
          </span>
        );
      },
      sortValue: (u) => userName(u).toLowerCase(),
    },
    {
      key: 'role',
      label: t('platformUsers.colRole', 'Turi'),
      width: '140px',
      truncate: false,
      render: (u) => (
        <span className="flex flex-wrap gap-1">
          {u.is_client && <Badge tone="success">{t('platformUsers.client', 'Mijoz')}</Badge>}
          {u.platform_role && (
            <Badge tone="neutral">{t(`platformAdmins.roles.${u.platform_role}`, u.platform_role)}</Badge>
          )}
          {!u.is_client && !u.platform_role && u.memberships.length > 0 && (
            <Badge tone="info">{t('platformUsers.roleTenant', 'Doʻkon aʻzosi')}</Badge>
          )}
        </span>
      ),
    },
    {
      key: 'memberships',
      label: t('platformUsers.colTenants', 'Doʻkonlar'),
      width: '1fr',
      render: (u) => (
        <span className="truncate text-small text-muted">
          {u.memberships.length > 0
            ? u.memberships.map((m) => m.tenant).join(', ')
            : '—'}
        </span>
      ),
      sortValue: (u) => u.memberships.length,
    },
    {
      key: 'locale',
      label: t('platformTenants.colLocale', 'Til'),
      width: '64px',
      align: 'right',
      render: (u) => (
        <span className="text-micro font-semibold uppercase tabular-nums text-muted">
          {u.locale ?? '—'}
        </span>
      ),
      sortValue: (u) => u.locale ?? '',
    },
    {
      key: 'seen',
      label: t('platformUsers.colSeen', 'Faollik'),
      width: '116px',
      align: 'right',
      render: (u) => (
        <span className="tabular-nums text-small text-muted">
          {u.last_seen_at ? relativeFromNow(u.last_seen_at) : '—'}
        </span>
      ),
      sortValue: (u) => u.last_seen_at ?? '',
    },
    // No trailing chevron column: this directory is read-only (no detail
    // surface to open per row), so a ChevronRight "drill-in" affordance would
    // be a false promise — §3 reserves it for rows that actually open something.
  ], [t]);

  // ── Body ──────────────────────────────────────────────────────────────────
  let body: ReactNode;
  if (users.isLoading) {
    body = <SkeletonList rows={6} />;
  } else if (users.isError) {
    body = <ErrorCard error={users.error} onRetry={() => users.refetch()} />;
  } else if (items.length === 0 && searchOrFilter) {
    body = (
      <EmptyState
        size="compact"
        icon={<Users size={24} />}
        title={t('common.noResults', 'Topilmadi')}
        body={t('platformUsers.noResultsBody', 'Filtr yoki qidiruvga mos foydalanuvchi topilmadi.')}
        action={<Button variant="outline" size="md" onClick={clearAll}>{t('common.clear', 'Tozalash')}</Button>}
      />
    );
  } else if (items.length === 0) {
    body = (
      <EmptyState
        tone="accent"
        size="compact"
        icon={<Users size={24} />}
        title={t('platformUsers.emptyTitle', 'Hali foydalanuvchilar yoʻq')}
        body={t('platformUsers.emptyBody', 'Mijozlar Telegram-bot orqali roʻyxatdan oʻtgach, shu yerda koʻrinadi.')}
      />
    );
  } else {
    body = (
      <>
        {/* Desktop (lg+): dense sortable DataTable. Below lg the sidebar leaves
            too little width for a comfortable table, so we show rich cards. */}
        <div className="hidden lg:block">
          <DataTable
            columns={cols}
            rows={items}
            rowKey={(u) => u.id}
          />
        </div>

        {/* Mobile + tablet: rich user cards (never the squeezed desktop table). */}
        <ul className="space-y-2.5 lg:hidden">
          {items.map((u) => (
            <li key={u.id}>
              <UserCard u={u} t={t} />
            </li>
          ))}
        </ul>

        {/* Server-side pagination controls (only shown when there are multiple pages). */}
        {lastPage > 0 && (
          <div className="flex items-center justify-between gap-3 px-1 py-1 text-small text-muted">
            <span className="tabular-nums">
              {page * PAGE + 1}–{Math.min((page + 1) * PAGE, total)} / {total}
            </span>
            <div className="flex items-center gap-1">
              <Button
                variant="outline"
                size="sm"
                disabled={page <= 0}
                onClick={() => setPage((p) => Math.max(0, p - 1))}
                aria-label={t('common.prev', 'Oldingi') ?? 'prev'}
              >
                {t('common.prev', 'Oldingi')}
              </Button>
              <span className="px-1 tabular-nums">{page + 1} / {lastPage + 1}</span>
              <Button
                variant="outline"
                size="sm"
                disabled={page >= lastPage}
                onClick={() => setPage((p) => Math.min(lastPage, p + 1))}
                aria-label={t('common.next', 'Keyingi') ?? 'next'}
              >
                {t('common.next', 'Keyingi')}
              </Button>
            </div>
          </div>
        )}
      </>
    );
  }

  // Show toolbar whenever we have data, are loading, or a search/filter is active.
  const showToolbar = users.isLoading || total > 0 || searchOrFilter;

  return (
    <>
      {/* Header — desktop PageHeader / mobile MobileHeader (Telegram-safe). */}
      <div className="hidden md:block">
        <PageHeader title={t('nav.users', 'Foydalanuvchilar')} subtitle={subtitle} />
      </div>
      <div className="md:hidden">
        <MobileHeader title={t('nav.users', 'Foydalanuvchilar')} />
        <p className="mb-3 max-w-prose text-small leading-snug text-muted">{subtitle}</p>
      </div>

      <div className="space-y-4 sm:space-y-5">
        {/* A failed fetch with no cached data must never render as a confident
            "0 foydalanuvchi / 0 mijoz" dashboard — that reads as real data
            instead of a load failure to the platform admin. Hide the KPI strip
            entirely instead of zeroing every card; `body` below already
            surfaces the real ErrorCard + retry for this exact case, so we don't
            duplicate it here (same root-cause fix as platform/OverviewPage.tsx
            and tenant/OverviewPage.tsx). A background refetch (isError but
            users.data still cached) keeps showing the last-known counts, since
            total/clientCount/etc. are derived from that cached data. */}

        {/* (#408) Segment filter load failure — previously invisible since the
            segment picker lives inside the drawer. Surface it inline. */}
        {segs.isError && (
          <div
            role="alert"
            className="flex items-center justify-between gap-2 rounded-xl border border-danger/30 bg-[color:var(--c-danger-soft)] px-3 py-2 text-small text-danger"
          >
            {t('platformBroadcasts.segmentsError', 'Segmentlar yuklanmadi')}
            <Button variant="outline" size="sm" onClick={() => segs.refetch()}>
              {t('common.retry', 'Qayta urinish')}
            </Button>
          </div>
        )}

        {showToolbar && (
          <div>
            <FilterBar>
              <SearchInput
                className="flex-1"
                value={q}
                onChange={setQ}
                onDebouncedChange={(v) => { setDebouncedQ(v); setPage(0); }}
                placeholder={t('platformUsers.search', 'Ism, telefon yoki username…') ?? ''}
              />
              <FilterButton count={activeFilters} onClick={() => setFilterOpen(true)} />
            </FilterBar>
            <ActiveFilters filters={chips} onClearAll={clearAll} />
          </div>
        )}

        {body}
      </div>

      <FilterDrawer open={filterOpen} onClose={() => setFilterOpen(false)} onClear={clearAll}>
        {(segs.data ?? []).length > 0 && (
          <RadioGroup<string>
            label={t('platformUsers.segmentFilter', 'Segment')}
            value={segment}
            onChange={pick}
            options={(segs.data ?? []).map((s) => ({ value: s.key, label: `${segLabel(s, lng)} · ${s.count}` }))}
          />
        )}
        <RadioGroup<RoleOpt>
          label={t('platformUsers.roleFilter', 'Turi')}
          value={role}
          onChange={setRole}
          options={[
            { value: 'all',      label: t('common.all', 'Hammasi') },
            { value: 'client',   label: t('platformUsers.client', 'Mijoz') },
            { value: 'platform', label: t('platformUsers.roleStaff', 'Platforma') },
            { value: 'tenant',   label: t('platformUsers.roleTenant', 'Doʻkon aʻzosi') },
          ]}
        />
        <RadioGroup<LocaleOpt>
          label={t('platformTenants.colLocale', 'Til')}
          value={loc}
          onChange={setLoc}
          options={LOCALE_OPTS.map((l) => ({ value: l, label: l === 'all' ? t('common.all') : l.toUpperCase() }))}
        />
        <RadioGroup<UserSort>
          label={t('common.sort', 'Saralash')}
          value={sort}
          onChange={setSort}
          options={[
            { value: 'default', label: t('platformUsers.sortDefault', 'Standart') },
            { value: 'name',    label: t('platformUsers.sortName', 'Ism (A–Z)') },
            { value: 'seen',    label: t('platformUsers.sortSeen', 'Oxirgi faollik') },
          ]}
        />
      </FilterDrawer>
    </>
  );
}
