import { useCallback, useMemo, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { ChevronRight, FileClock, ShieldCheck } from 'lucide-react';
import { PageHeader } from '@/components/ui/PageHeader';
import { Avatar } from '@/components/ui/Avatar';
import { Button } from '@/components/ui/Button';
import { EmptyState } from '@/components/ui/EmptyState';
import { DataTable, type Column } from '@/components/ui/DataTable';
import { SkeletonList } from '@/components/ui/Skeleton';
import { ErrorCard } from '@/components/ui/ErrorCard';
import { DetailModal, DefList, DefRow } from '@/components/ui/DetailModal';
import { SearchInput } from '@/components/ui/SearchInput';
import {
  FilterBar, FilterButton, FilterDrawer, ActiveFilters,
  CheckGroup, RadioGroup,
} from '@/components/ui/FilterDrawer';
import { DateRangePicker } from '@/components/ui/DateRangePicker';
import { usePlatformAudit, usePlatformTenants, type AuditRow } from '@/lib/queries';
import { shortDate, relativeFromNow, dateTime } from '@/lib/format';
import { MobileHeader } from '@/components/mobile/primitives';

type SortKey = 'new' | 'old' | 'action';

const todayISO = () => new Date().toISOString().slice(0, 10);

export default function PlatformAuditPage() {
  const { t } = useTranslation();
  const [search, setSearch] = useState('');
  const [actions, setActions] = useState<string[]>([]);
  const [tenantIds, setTenantIds] = useState<string[]>([]);
  const [from, setFrom] = useState('');
  const [to, setTo] = useState('');
  const [sort, setSort] = useState<SortKey>('new');

  const [filterOpen, setFilterOpen] = useState(false);
  const q = usePlatformAudit({ action: actions.join(','), tenant_id: tenantIds.join(','), date_from: from, date_to: to, limit: 200 });
  const tenants = usePlatformTenants({ limit: 200 });
  const allRows = useMemo(() => q.data?.items ?? [], [q.data]);
  const [detail, setDetail] = useState<AuditRow | null>(null);

  // (#506) Human-readable audit action label — falls back to the raw enum for
  // any new backend action that has no translation yet. Dots become underscores
  // so the lookup doesn't collide with i18next's nesting separator.
  // (H17) stable across renders so the `cols` useMemo below doesn't rebuild
  // every render — this closes over `t`, its only real dependency.
  const actionLabel = useCallback((a: string) => t(`audit.actions.${a.replace(/\./g, '_')}`, a), [t]);

  // Action options: distinct actions from the server, plus category prefixes
  // (e.g. "tenant." matches every tenant.* action) derived from those actions.
  const actionOptions = useMemo(() => {
    const list = q.data?.actions ?? [];
    const cats = Array.from(new Set(list.map((a) => a.split('.')[0]))).sort();
    return [
      ...cats.map((c) => ({ value: `${c}.`, label: `${t(`audit.cats.${c}`, c)} — ${c}.*` })),
      ...list.map((a) => ({ value: a, label: actionLabel(a) })),
    ];
  }, [q.data?.actions, t]);  // eslint-disable-line react-hooks/exhaustive-deps -- actionLabel derives from t

  const tenantOptions = useMemo(() =>
    (tenants.data?.items ?? []).map((tn) => ({ value: tn.id, label: tn.name })),
  [tenants.data]);

  // Client-side search + sort over the loaded page (server already applied the
  // action/tenant/date filters). Search spans the human label and the raw
  // action, plus entity/actor/IP — the fields a reviewer scans by.
  const rows = useMemo(() => {
    const needle = search.trim().toLowerCase();
    const matched = !needle ? allRows : allRows.filter((a) =>
      [actionLabel(a.action), a.action, a.entity_type, a.entity_id, a.actor_name, a.actor_user_id, a.ip]
        .some((v) => v?.toLowerCase().includes(needle)));
    return matched.slice().sort((x, y) => {
      if (sort === 'action') return actionLabel(x.action).localeCompare(actionLabel(y.action));
      const d = (x.created_at ?? '').localeCompare(y.created_at ?? '');
      return sort === 'old' ? d : -d;  // 'new' = newest first
    });
  }, [allRows, search, sort]);  // eslint-disable-line react-hooks/exhaustive-deps -- actionLabel derives from t

  const activeFilters = actions.length + tenantIds.length + (from || to ? 1 : 0) + (sort !== 'new' ? 1 : 0);
  const clearFilters = () => { setActions([]); setTenantIds([]); setFrom(''); setTo(''); setSort('new'); };

  const tenantName = (id: string) => tenantOptions.find((o) => o.value === id)?.label ?? id.slice(0, 8);
  const sortLabel: Record<SortKey, string> = {
    new: '', old: t('audit.sortOld', 'Eng eski'), action: t('audit.sortAction', 'Amal (A→Z)'),
  };
  // Removable chips for each active facet (default values are omitted).
  const chips = [
    ...actions.map((a) => ({ key: `a-${a}`, label: actionLabel(a), onRemove: () => setActions((xs) => xs.filter((x) => x !== a)) })),
    ...tenantIds.map((id) => ({ key: `tn-${id}`, label: tenantName(id), onRemove: () => setTenantIds((xs) => xs.filter((x) => x !== id)) })),
    (from || to) && { key: 'date', label: `${from || '…'} → ${to || '…'}`, onRemove: () => { setFrom(''); setTo(''); } },
    sort !== 'new' && { key: 'sort', label: sortLabel[sort], onRemove: () => setSort('new') },
  ].filter(Boolean) as { key: string; label: string; onRemove: () => void }[];

  const total = q.data?.total ?? 0;
  const showToolbar = q.isLoading || total > 0 || activeFilters > 0 || !!search.trim();
  const searchOrFilter = !!search.trim() || activeFilters > 0;

  // Desktop (lg+) table columns. Below lg the sidebar leaves too little width
  // for a table, so cards are shown through tablet. The whole row opens detail
  // (no 3-dot for a single "view" action); a trailing chevron signals clickable.
  // (H17) memoized so an unrelated re-render doesn't rebuild this array.
  const cols: Column<AuditRow>[] = useMemo(() => [
    {
      key: 'actor', label: t('audit.colActor', 'Aktor'), width: '200px', truncate: false,
      // The NAME, with the id only as a fallback. This column used to be a
      // truncated UUID and nothing else — for a log whose whole purpose is
      // answering "who did this", that is unreadable, and an operator reviewing
      // an incident had to look every id up by hand. The server resolves the
      // name; when it cannot (a deleted actor) the id is still better than a
      // placeholder that names the wrong person.
      render: (a) => (
        <span className="flex min-w-0 items-center gap-2.5">
          <Avatar name={a.actor_name ?? a.actor_user_id ?? '?'} size="sm" />
          <span className="min-w-0">
            {a.actor_name ? (
              <span className="block truncate text-small font-medium">{a.actor_name}</span>
            ) : (
              <span className="block truncate font-mono text-caption text-muted">
                {a.actor_user_id ? a.actor_user_id.slice(0, 12) : '—'}
              </span>
            )}
          </span>
        </span>
      ),
      sortValue: (a) => (a.actor_name ?? a.actor_user_id ?? '').toLowerCase(),
    },
    {
      key: 'action', label: t('audit.colAction', 'Amal'), width: '1.6fr',
      render: (a) => <span className="block truncate text-small font-medium">{actionLabel(a.action)}</span>,
      sortValue: (a) => actionLabel(a.action).toLowerCase(),
    },
    {
      key: 'entity', label: t('audit.colEntity', 'Obyekt'), width: '1.2fr',
      render: (a) => <span className="block truncate text-small text-muted">{a.entity_type ?? '—'}{a.entity_id ? ` · ${a.entity_id.slice(0, 8)}` : ''}</span>,
    },
    {
      key: 'time', label: t('audit.colTime', 'Vaqt'), width: '120px', align: 'right',
      render: (a) => <span className="tabular-nums text-small text-muted">{shortDate(a.created_at)}</span>,
      sortValue: (a) => a.created_at ?? '',
    },
    {
      key: 'chevron', label: '', width: '32px', align: 'right',
      render: () => <ChevronRight size={16} className="text-muted" />,
    },
  ], [t, actionLabel]);

  const pageSubtitle = t('audit.pageSubtitle', "Foydalanuvchilar harakatlari va tizim o'zgarishlarining to'liq tarixi");

  return (
    <>
      {/* Header — desktop PageHeader / mobile MobileHeader (Telegram-safe). */}
      <div className="hidden md:block">
        <PageHeader
          title={t('nav.audit')}
          subtitle={pageSubtitle}
        />
      </div>
      <div className="md:hidden">
        <MobileHeader title={t('nav.audit')} />
        <p className="mb-3 max-w-prose text-small leading-snug text-muted">{pageSubtitle}</p>
      </div>

      <div className="space-y-4 sm:space-y-5">
        {showToolbar && (
          <div>
            <FilterBar>
              {/* Free-text search runs over the rows this page has loaded, not
                  the whole audit table. Saying so stops a silent "no results"
                  from reading as "this never happened". */}
              <SearchInput
                value={search}
                onChange={setSearch}
                className="flex-1"
                placeholder={t('audit.searchPh', "Amal, obyekt yoki aktor bo'yicha qidirish…")}
              />
              <FilterButton count={activeFilters} onClick={() => setFilterOpen(true)} />
            </FilterBar>
            <p className="mb-2 text-caption text-muted">
              {t('platformAudit.searchScope', 'Qidiruv yuklangan yozuvlar bo‘yicha ishlaydi. Aniqroq natija uchun sana va amal filtridan foydalaning.')}
            </p>
            <ActiveFilters filters={chips} onClearAll={clearFilters} />
          </div>
        )}

        {q.isLoading ? (
          <SkeletonList rows={8} />
        ) : q.isError ? (
          <ErrorCard error={q.error} onRetry={() => q.refetch()} />
        ) : rows.length === 0 ? (
          searchOrFilter ? (
            <EmptyState
              size="compact"
              icon={<FileClock size={24} />}
              title={t('common.noResults', 'Topilmadi')}
              body={t('audit.emptyFilteredHint', "Filtrlarni tozalab, barcha yozuvlarni ko'ring")}
              action={
                <Button
                  variant="outline"
                  size="md"
                  onClick={() => { clearFilters(); setSearch(''); }}
                >
                  {t('common.clear', 'Tozalash')}
                </Button>
              }
            />
          ) : (
            <EmptyState
              size="compact"
              icon={<ShieldCheck size={24} />}
              title={t('audit.emptyTitle', "Audit jurnali bo'sh")}
              body={t('audit.emptyBody', "Foydalanuvchilar harakatlari shu yerda qayd etiladi (login, sotuv yaratish, to'lov tasdiqlash, va h.k.).")}
            />
          )
        ) : (
          <>
            {/* Desktop (lg+): dense sortable table — wide enough once the 280px
                sidebar is subtracted. Below lg we show richer cards. */}
            <div className="hidden lg:block">
              <DataTable
                columns={cols}
                rows={rows}
                rowKey={(a) => a.id}
                onRowClick={(a) => setDetail(a)}
              />
            </div>

            {/* Mobile + tablet: rich audit cards (actor avatar + action + target +
                relative time, tappable). Never the squeezed desktop table below lg. */}
            <ul className="space-y-2 lg:hidden">
              {rows.map((a) => (
                <li key={a.id}>
                  <button
                    type="button"
                    onClick={() => setDetail(a)}
                    className="flex w-full items-center gap-3 rounded-card border border-line bg-surface px-4 py-3 text-left transition hover:border-ink/15 hover:shadow-card focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ink/20"
                  >
                    <Avatar name={a.actor_name ?? a.actor_user_id ?? '?'} size="md" />
                    <div className="min-w-0 flex-1">
                      <div className="truncate text-small font-medium">{actionLabel(a.action)}</div>
                      <div className="truncate text-caption text-muted">
                        {a.entity_type ?? '—'}{a.entity_id ? ` · ${a.entity_id.slice(0, 8)}` : ''}
                      </div>
                    </div>
                    <div className="flex shrink-0 items-center gap-2">
                      <span className="hidden text-caption tabular-nums text-muted sm:block">
                        {relativeFromNow(a.created_at)}
                      </span>
                      <span className="text-caption tabular-nums text-muted sm:hidden">
                        {shortDate(a.created_at)}
                      </span>
                      <ChevronRight size={16} className="text-muted" />
                    </div>
                  </button>
                </li>
              ))}
            </ul>

            {/* Row count footer — useful context for a reviewer. */}
            {total > rows.length && (
              <p className="text-center text-caption text-muted">
                {t('audit.showing', {
                  shown: rows.length,
                  total,
                  defaultValue: "{{total}} ta yozuv · {{shown}} tasi ko'rsatildi",
                })}
              </p>
            )}
          </>
        )}
      </div>

      <FilterDrawer open={filterOpen} onClose={() => setFilterOpen(false)} onClear={clearFilters}>
        <CheckGroup
          label={t('audit.colAction', 'Amal')}
          value={actions}
          onChange={setActions}
          options={actionOptions}
        />
        <CheckGroup
          label={t('audit.tenant', 'Tenant')}
          value={tenantIds}
          onChange={setTenantIds}
          options={tenantOptions}
        />
        <div>
          <div className="mb-2 text-micro uppercase tracking-wider text-muted">
            {t('audit.period', 'Davr')}
          </div>
          <DateRangePicker
            from={from}
            to={to}
            max={todayISO()}
            onChange={(r) => { setFrom(r.from); setTo(r.to); }}
          />
        </div>
        <RadioGroup<SortKey>
          label={t('common.sort', 'Saralash')}
          value={sort}
          onChange={setSort}
          options={[
            { value: 'new', label: t('audit.sortNew', 'Eng yangi') },
            { value: 'old', label: t('audit.sortOld', 'Eng eski') },
            { value: 'action', label: t('audit.sortAction', 'Amal (A→Z)') },
          ]}
        />
      </FilterDrawer>

      <DetailModal
        open={!!detail}
        onClose={() => setDetail(null)}
        title={t('audit.detailTitle', 'Audit yozuvi')}
        subtitle={detail ? <span className="font-mono text-caption">{detail.action}</span> : undefined}
      >
        {detail && (
          <DefList>
            <DefRow label={t('audit.colTime', 'Vaqt')}>
              {dateTime(detail.created_at)}
            </DefRow>
            <DefRow label={t('audit.colAction', 'Amal')}>
              <span className="font-mono text-caption">{detail.action}</span>
            </DefRow>
            <DefRow label={t('audit.colEntity', 'Obyekt')}>{detail.entity_type}</DefRow>
            <DefRow label={t('audit.entityId', 'Obyekt ID')}>
              <span className="font-mono text-caption">{detail.entity_id}</span>
            </DefRow>
            <DefRow label={t('audit.colActor', 'Aktor')}>
              {detail.actor_name ?? (
                <span className="font-mono text-caption">{detail.actor_user_id ?? '—'}</span>
              )}
            </DefRow>
            <DefRow label={t('audit.tenant', 'Tenant')}>
              {detail.tenant_id ? tenantName(detail.tenant_id) : '—'}
            </DefRow>
            <DefRow label="IP">
              <span className="font-mono text-caption">{detail.ip}</span>
            </DefRow>
          </DefList>
        )}
      </DetailModal>
    </>
  );
}
