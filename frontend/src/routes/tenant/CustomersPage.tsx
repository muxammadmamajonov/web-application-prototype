import { useCallback, useEffect, useMemo, useState, lazy, Suspense } from 'react';
import { useTranslation } from 'react-i18next';
import { Check, Eye, Pencil, Send, Trash2, Users, X } from 'lucide-react';
import { shareViaTelegram } from '@/lib/telegram';
import { PageHeader } from '@/components/ui/PageHeader';
import { FloatingCreateAction } from '@/components/ui/FloatingCreateAction';
import { Button } from '@/components/ui/Button';
import { Avatar } from '@/components/ui/Avatar';
import { StatusBadge } from '@/components/ui/Badge';
import { RowCard } from '@/components/ui/RowCard';
import { DataTable, type Column } from '@/components/ui/DataTable';
import { RowActionMenu, type RowAction } from '@/components/ui/RowActionMenu';
import { MobileHeader } from '@/components/mobile/primitives';
import { SearchInput } from '@/components/ui/SearchInput';
import { SkeletonList } from '@/components/ui/Skeleton';
import {
  FilterBar, FilterButton, FilterDrawer, RadioGroup, DateRangeGroup, RangeGroup,
  matchesDatePreset, matchesRange, rangeActive, type DatePreset,
} from '@/components/ui/FilterDrawer';
import { DetailModal } from '@/components/ui/DetailModal';
import { EmptyState } from '@/components/ui/EmptyState';
import { ErrorCard } from '@/components/ui/ErrorCard';
import { confirmDialog, toast } from '@/lib/feedback';
import { apiErrorMessage, isNotFound } from '@/lib/errors';
import { useTenantCustomers, type CustomerRow } from '@/lib/queries';
import { useApproveCustomer, useDeleteCustomer, useRejectCustomer, useResendCustomerInvite } from '@/lib/mutations';
import { shortDate, money } from '@/lib/format';
import { useCurrency } from '@/lib/currency';
import { useSubmitGuard } from '@/hooks/useSubmitGuard';
import { displayPhone } from '@/lib/phone';
import { TelegramStatusBadge } from '@/components/ui/TelegramStatusBadge';
const CustomerModal = lazy(() => import('./_modals/CustomerModal').then(m => ({ default: m.CustomerModal })));

type StatusFilter = '' | 'pending' | 'active' | 'blocked';
type TgFilter = 'all' | 'linked' | 'unlinked';
type SortKey = 'new' | 'name' | 'incomeDesc';

export default function TenantCustomersPage() {
  const { t } = useTranslation();
  const currency = useCurrency();
  const [search, setSearch] = useState('');
  const [debouncedSearch, setDebouncedSearch] = useState('');
  useEffect(() => {
    const id = setTimeout(() => setDebouncedSearch(search.trim()), 250);
    return () => clearTimeout(id);
  }, [search]);

  const [statusFilter, setStatusFilter] = useState<StatusFilter>('');
  const [datePreset, setDatePreset] = useState<DatePreset>('all');
  const [tg, setTg] = useState<TgFilter>('all');
  const [incomeRange, setIncomeRange] = useState({ min: '', max: '' });
  const [sort, setSort] = useState<SortKey>('new');
  const [filterOpen, setFilterOpen] = useState(false);

  const query = useTenantCustomers({
    limit: 200,
    ...(statusFilter ? { status_filter: statusFilter } : {}),
    ...(debouncedSearch ? { q: debouncedSearch } : {}),
  });
  const serverRows = useMemo(() => query.data?.items ?? [], [query.data]);
  // Status + search hit the server; time, Telegram link, income and sort are
  // cheap client-side refinements over the already-loaded rows.
  const rows = useMemo(
    () => serverRows
      .filter((c) => matchesDatePreset(c.created_at, datePreset))
      .filter((c) => (tg === 'all' ? true : tg === 'linked' ? c.telegram_id != null : c.telegram_id == null))
      .filter((c) => matchesRange(c.monthly_income, incomeRange))
      .slice()
      .sort((a, b) => {
        if (sort === 'name') return a.full_name.localeCompare(b.full_name);
        if (sort === 'incomeDesc') return (b.monthly_income ?? 0) - (a.monthly_income ?? 0);
        return 0; // 'new' — keep server order (created desc)
      }),
    [serverRows, datePreset, tg, incomeRange, sort],
  );

  const pendingCountQuery = useTenantCustomers({ status_filter: 'pending' });
  const pendingCount = pendingCountQuery.data?.total ?? 0;
  // Search and status are resolved by the server across the whole book; date,
  // Telegram-link, income and sort refine the loaded page. When the book is
  // larger than what was loaded, say so — a filtered list that silently covers
  // part of the data is worse than one that admits it.
  const serverTotal = query.data?.total ?? 0;
  const loadedIsPartial = serverTotal > serverRows.length;
  const activeFilters = (statusFilter ? 1 : 0) + (datePreset !== 'all' ? 1 : 0)
    + (tg !== 'all' ? 1 : 0) + (rangeActive(incomeRange) ? 1 : 0) + (sort !== 'new' ? 1 : 0);
  const resetAll = () => { setStatusFilter(''); setDatePreset('all'); setTg('all'); setIncomeRange({ min: '', max: '' }); setSort('new'); };

  // Modals
  const [formOpen, setFormOpen] = useState(false);
  const [editing, setEditing] = useState<CustomerRow | null>(null);
  const [detail, setDetail] = useState<CustomerRow | null>(null);

  const del = useDeleteCustomer();
  const approve = useApproveCustomer();
  const reject = useRejectCustomer();
  const resendInvite = useResendCustomerInvite();
  // Synchronous one-shot guards — block a double-tap from firing two
  // approve/reject mutations before react-query's isPending flips.
  const approveGuard = useSubmitGuard();
  const rejectGuard = useSubmitGuard();

  const openCreate = () => { setEditing(null); setFormOpen(true); };
  const openEdit = useCallback((c: CustomerRow) => { setDetail(null); setEditing(c); setFormOpen(true); }, []);

  const onDelete = useCallback(async (c: CustomerRow) => {
    if (!(await confirmDialog({ message: t('customers.deleteConfirm', { name: c.full_name }), danger: true, confirmLabel: t('common.delete') }))) return;
    // Close the profile immediately so the delete button's loading/size-shift
    // flicker is never shown; the request finishes in the background.
    setDetail(null);
    try {
      await del.mutateAsync(c.id);
      toast.success(t('customers.deleted', { defaultValue: "Mijoz o'chirildi" }));
    } catch (ex) {
      if (isNotFound(ex)) { toast.info(t('customers.alreadyDeleted', { defaultValue: "Mijoz allaqachon o'chirilgan" })); return; }
      toast.error(apiErrorMessage(ex));
    }
  }, [t, del]);
  const onApprove = (c: CustomerRow) => approveGuard.run(async () => {
    try { await approve.mutateAsync(c.id); setDetail(null); toast.success(t('customers.approved', { defaultValue: 'Tasdiqlandi' })); }
    catch (ex) { toast.error(apiErrorMessage(ex)); }
  });
  const onReject = async (c: CustomerRow) => {
    if (!(await confirmDialog({ message: t('customers.rejectConfirm', { name: c.full_name, defaultValue: `Reject ${c.full_name}?` }), danger: true, confirmLabel: t('customers.reject', { defaultValue: 'Rad etish' }) }))) return;
    await rejectGuard.run(async () => {
      try { await reject.mutateAsync(c.id); setDetail(null); toast.success(t('customers.rejected', { defaultValue: 'Rad etildi' })); }
      catch (ex) { toast.error(apiErrorMessage(ex)); }
    });
  };

  // (H17) stable across renders (pure function of its argument) so the `cols`
  // useMemo below doesn't rebuild every render.
  const passport = useCallback((c: CustomerRow) =>
    [c.passport_series, c.passport_number].filter(Boolean).join(' ') || c.passport_id || null, []);

  // Shared row "⋯" actions, wired only to existing page-level handlers
  // (open detail, edit, delete). Delete names the customer in the confirm.
  // (H17) stable across renders so the `cols` useMemo below doesn't rebuild
  // every render.
  const rowActions = useCallback((c: CustomerRow): RowAction[] => [
    { key: 'view', label: t('common.viewDetails', 'Batafsil'), icon: Eye, onClick: () => setDetail(c) },
    { key: 'edit', label: t('common.edit', 'Tahrirlash'), icon: Pencil, onClick: () => openEdit(c) },
    { key: 'delete', label: t('common.delete', "O'chirish"), icon: Trash2, destructive: true, onClick: () => onDelete(c) },
  ], [t, openEdit, onDelete]);

  // Desktop (md+) table columns. Mobile keeps the RowCard list below. (H17)
  const cols: Column<CustomerRow>[] = useMemo(() => [
    {
      key: 'name', label: t('customers.colName', { defaultValue: 'Mijoz' }), width: '1.5fr',
      sortValue: (c) => c.full_name.toLowerCase(),
      render: (c) => (
        <span className="flex min-w-0 items-center gap-2">
          <Avatar name={c.full_name} size="sm" />
          <span className="truncate font-medium">{c.full_name}</span>
        </span>
      ),
    },
    {
      key: 'phone', label: t('customers.phone', 'Telefon'), width: '1fr',
      render: (c) => (
        <span className="block min-w-0">
          <span className="block truncate tabular-nums">{displayPhone(c.phone)}</span>
          {c.secondary_phone && <span className="block truncate text-micro tabular-nums text-muted">{displayPhone(c.secondary_phone)}</span>}
        </span>
      ),
    },
    {
      key: 'doc', label: t('customers.passport', 'Passport / JSHSHIR'), width: '1fr',
      render: (c) => <span className="block truncate text-small text-muted">{passport(c) ?? c.jshshir ?? '—'}</span>,
    },
    {
      key: 'status', label: t('customers.colStatus', 'Holat'), width: '112px',
      render: (c) => <StatusBadge status={c.status} />, sortValue: (c) => c.status,
    },
    {
      key: 'tg', label: 'Telegram', width: '120px',
      // Three states, not two — see TelegramStatusBadge. Sorted most-reachable
      // first so the rows needing an operator's attention sink to the bottom.
      sortValue: (c) => (c.telegram_status === 'joined' ? 2 : c.telegram_status === 'known' ? 1 : 0),
      render: (c) => <TelegramStatusBadge status={c.telegram_status} compact />,
    },
    {
      key: 'joined', label: t('customers.colJoined', 'Qo‘shildi'), width: '110px', align: 'right',
      sortValue: (c) => c.created_at ?? '',
      render: (c) => <span className="tabular-nums text-caption text-muted">{shortDate(c.created_at)}</span>,
    },
    {
      key: 'actions', label: '', width: '48px', align: 'right',
      render: (c) => <RowActionMenu actions={rowActions(c)} ariaLabel={t('common.actions', 'Amallar')} />,
    },
  ], [t, rowActions, passport]);

  return (
    <>
      <div className="hidden md:block">
        <PageHeader title={t('nav.customers')} />
      </div>

      <div className="md:hidden">
        <MobileHeader title={t('nav.customers')} />
        <p className="mb-3 max-w-prose text-small leading-snug text-muted">
          {t('customers.subtitle', { count: query.data?.total ?? 0 })}
        </p>
      </div>

      {/* The create action is the floating `+`, identical on every page and every
          breakpoint, so it never moves between the header and a full-width row. */}
      <FloatingCreateAction onClick={openCreate} label={t('customers.create')} />

      {/* Standard toolbar: one search box (searches name, phone, passport, JSHSHIR,
          address) + a single Filtr button that opens the drawer. */}
      <FilterBar>
        <SearchInput value={search} onChange={setSearch} className="flex-1"
          placeholder={t('customers.searchPh', { defaultValue: 'Ism, telefon, passport yoki JSHSHIR…' })} />
        <FilterButton count={activeFilters} onClick={() => setFilterOpen(true)} />
      </FilterBar>

      {/* Name the scope the visible rows cover. */}
      {loadedIsPartial && (
        <p className="mb-2 text-caption text-muted">
          {t('customers.scopeNote', {
            shown: serverRows.length, total: serverTotal,
            defaultValue: 'Jami {{total}} tadan {{shown}} tasi yuklandi — qidiruv butun ro‘yxat bo‘yicha ishlaydi',
          })}
        </p>
      )}

      <FilterDrawer open={filterOpen} onClose={() => setFilterOpen(false)} onClear={resetAll}>
        <RadioGroup<StatusFilter>
          label={t('customers.colStatus', 'Holat')}
          value={statusFilter}
          onChange={setStatusFilter}
          options={[
            { value: '', label: t('common.all', 'Hammasi') },
            { value: 'pending', label: t('status.pending', 'Kutilmoqda'), count: pendingCount },
            { value: 'active', label: t('status.active', 'Faol') },
            { value: 'blocked', label: t('status.blocked', 'Bloklangan') },
          ]}
        />
        <RadioGroup<TgFilter>
          label={t('customers.tgFilter', 'Telegram')}
          value={tg}
          onChange={setTg}
          options={[
            { value: 'all', label: t('common.all', 'Hammasi') },
            { value: 'linked', label: t('customers.tgLinkedShort', { defaultValue: 'Ulangan' }) },
            { value: 'unlinked', label: t('customers.tgUnlinkedShort', { defaultValue: 'Ulanmagan' }) },
          ]}
        />
        <DateRangeGroup value={datePreset} onChange={setDatePreset} />
        <RangeGroup
          label={t('customers.monthlyIncome', 'Oylik daromad')}
          unit={currency}
          value={incomeRange}
          onChange={setIncomeRange}
          step={1000}
        />
        <RadioGroup<SortKey>
          label={t('common.sort', 'Saralash')}
          value={sort}
          onChange={setSort}
          options={[
            { value: 'new', label: t('customers.sortNew', 'Eng yangi') },
            { value: 'name', label: t('customers.sortName', 'Ism (A→Z)') },
            { value: 'incomeDesc', label: t('customers.sortIncomeDesc', 'Daromad (katta)') },
          ]}
        />
      </FilterDrawer>

      {/* ── One list layout, no inline action buttons ─────────────────── */}
      {query.isLoading ? (
        <SkeletonList rows={6} />
      ) : query.isError ? (
        <ErrorCard error={query.error} onRetry={() => query.refetch()} />
      ) : rows.length === 0 ? (
        <EmptyCustomers searching={!!debouncedSearch || activeFilters > 0}
          onClear={activeFilters > 0 ? resetAll : undefined} />
      ) : (
        <>
          {/* Desktop (md+): paginated, sortable table. */}
          <div className="hidden md:block">
            <DataTable<CustomerRow>
              columns={cols}
              rows={rows}
              isError={query.isError}
              error={query.error}
              onRetry={() => query.refetch()}
              rowKey={(c) => c.id}
              onRowClick={(c) => setDetail(c)}
              pageSize={25}
            />
          </div>
          {/* Mobile (<md): rich cards. Row opens detail; the kebab stopPropagations.
              The wrapper is a div[role=button] (not a <button>) so the menu's own
              buttons nest legally — RowCard renders a plain div without onClick. */}
          <ul className="space-y-2 md:hidden">
            {rows.map((c) => (
              <li key={c.id}>
                <div
                  role="button"
                  tabIndex={0}
                  onClick={() => setDetail(c)}
                  onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); setDetail(c); } }}
                  className="cursor-pointer rounded-card focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ink/20"
                >
                  <RowCard
                    initial={c.full_name}
                    title={c.full_name}
                    subtitle={[displayPhone(c.phone), passport(c), c.jshshir].filter(Boolean).join(' · ') || '—'}
                    leftBadge={<StatusBadge status={c.status} />}
                    trailing={
                      <span className="flex items-center gap-1">
                        <span className="hidden text-caption tabular-nums text-muted sm:block">{shortDate(c.created_at)}</span>
                        <RowActionMenu actions={rowActions(c)} ariaLabel={t('common.actions', 'Amallar')} />
                      </span>
                    }
                  />
                </div>
              </li>
            ))}
          </ul>
        </>
      )}

      {/* Detail popup — Close top-right, context-aware actions bottom-right. */}
      <DetailModal
        open={!!detail}
        onClose={() => setDetail(null)}
        title={detail?.full_name ?? ''}
        subtitle={detail ? <StatusBadge status={detail.status} /> : undefined}
        onEdit={detail ? () => openEdit(detail) : undefined}
        onDelete={detail ? () => onDelete(detail) : undefined}
        deleting={del.isPending}
        actions={detail?.status === 'pending' ? (
          <>
            <Button variant="outline" size="md" onClick={() => onReject(detail)} loading={reject.isPending || rejectGuard.pending}
              className="border-danger/40 text-danger hover:bg-[color:var(--c-danger-soft)]">
              <X size={16} /> {t('customers.reject', { defaultValue: 'Rad etish' })}
            </Button>
            <Button variant="primary" size="md" onClick={() => onApprove(detail)} loading={approve.isPending || approveGuard.pending}>
              <Check size={16} /> {t('customers.approve', { defaultValue: 'Tasdiqlash' })}
            </Button>
          </>
        ) : undefined}
      >
        {detail && (
          <div className="space-y-5">
            {/* Identity band */}
            <div className="flex items-center gap-3">
              <Avatar name={detail.full_name} size="lg" />
              {/* No name line here — the modal title directly above IS the
                  customer's name, at nearly this weight and ~60px away. The
                  avatar and phone earn their place; a second copy of the name
                  does not. */}
              <div className="min-w-0 flex-1">
                {detail.phone && <a href={`tel:${detail.phone}`} className="text-body text-accent">{displayPhone(detail.phone)}</a>}
              </div>
              <TelegramStatusBadge status={detail.telegram_status} className="shrink-0" />
            </div>

            <ProfileSection title={t('customers.secPersonal', { defaultValue: 'Shaxsiy' })} rows={[
              [t('customers.passport', 'Passport'), passport(detail)],
              ['JSHSHIR', detail.jshshir],
              [t('customers.birthdate', "Tug'ilgan sana"), detail.birthdate ? shortDate(detail.birthdate) : null],
            ]} />
            <ProfileSection title={t('customers.secContact', { defaultValue: 'Aloqa' })} rows={[
              [t('customers.secondaryPhone', "Qo'shimcha telefon"), detail.secondary_phone ? <a href={`tel:${detail.secondary_phone}`} className="text-accent">{detail.secondary_phone}</a> : null],
              [t('customers.address', 'Manzil'), detail.address],
            ]} />
            <ProfileSection title={t('customers.secWork', { defaultValue: 'Ish / daromad' })} rows={[
              [t('customers.employmentPlace', 'Ish joyi'), detail.employment_place],
              [t('customers.monthlyIncome', 'Oylik daromad'), detail.monthly_income ? money(detail.monthly_income) : null],
            ]} />
            {detail.notes && (
              <section>
                <div className="mb-1.5 text-micro uppercase tracking-wider text-muted">{t('customers.notes', 'Izoh')}</div>
                <p className="rounded-control border border-line bg-surface px-3 py-2 text-body">{detail.notes}</p>
              </section>
            )}
            {detail.telegram_status === 'new' && <ResendInvite key={detail.id} customerId={detail.id} resend={resendInvite} />}
            <div className="text-caption text-muted">{t('customers.colJoined', 'Qo‘shildi')}: {shortDate(detail.created_at)}</div>
          </div>
        )}
      </DetailModal>

      {formOpen && (
        <Suspense fallback={<div className="text-center p-8">...</div>}>
        <CustomerModal open={true} onClose={() => setFormOpen(false)} existing={editing} />
        </Suspense>
      )}
    </>
  );
}

/** Re-mint a one-time join link for a customer who hasn't linked Telegram yet,
 *  then show the link inline with a Copy button. */
function ResendInvite({ customerId, resend }: { customerId: string; resend: ReturnType<typeof useResendCustomerInvite> }) {
  const { t } = useTranslation();
  const [url, setUrl] = useState<string | null>(null);
  const [linked, setLinked] = useState(false);
  const [copied, setCopied] = useState(false);

  const onResend = async () => {
    try {
      const r = await resend.mutateAsync(customerId);
      // They connected between this row rendering and the tap. There is no
      // link to show, and inventing one would be worse than saying so.
      if (r.already_linked) { setLinked(true); return; }
      setUrl(r.url);
    } catch (ex) { toast.error(apiErrorMessage(ex)); }
  };
  const copy = async () => {
    if (!url) return;
    try {
      await navigator.clipboard.writeText(url);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    } catch { /* clipboard unavailable — user can select manually */ }
  };

  return (
    <section>
      {linked ? (
        <div className="rounded-control border border-success/30 bg-[color:var(--c-success-soft)] p-3">
          <p className="text-caption text-muted">
            {t('customers.alreadyJoinedNote',
               { defaultValue: 'Bu mijoz allaqachon Telegram orqali ulangan — havola kerak emas.' })}
          </p>
        </div>
      ) : url ? (
        <div className="rounded-control border border-success/30 bg-[color:var(--c-success-soft)] p-3">
          <p className="mb-2 text-caption text-muted">
            {t('customers.inviteLinkNote', { defaultValue: 'Bu havolani mijozga yuboring — u ochgach Telegram orqali ulanadi.' })}
          </p>
          <div className="flex items-center gap-2">
            <button type="button" onClick={copy} aria-label={t('common.copy', { defaultValue: 'Nusxalash' })}
                    className="min-w-0 flex-1 cursor-pointer truncate rounded-icon border border-line bg-surface px-3 py-2 text-left font-mono text-caption">
              {copied
                ? <span className="inline-flex items-center gap-1 text-success"><Check size={14} /> {t('common.copied', { defaultValue: 'Nusxalandi' })}</span>
                : url}
            </button>
            <Button variant="primary" size="md"
                    onClick={() => shareViaTelegram(url, t('customers.inviteShareText', { defaultValue: 'Muddato — bo‘lib to‘lashga qo‘shiling' }))}>
              <Send size={16} /> {t('common.share', { defaultValue: 'Ulashish' })}
            </Button>
          </div>
        </div>
      ) : (
        <Button variant="outline" size="md" onClick={onResend} loading={resend.isPending}>
          <Send size={16} /> {t('customers.resendInvite', { defaultValue: 'Qo‘shilish havolasini qayta yuborish' })}
        </Button>
      )}
    </section>
  );
}

/** A titled group of label/value cards — only renders fields that have a value,
 *  and hides the whole section when none do. Powers the customer profile card. */
function ProfileSection({ title, rows }: { title: string; rows: [string, React.ReactNode][] }) {
  const visible = rows.filter(([, v]) => v !== null && v !== undefined && v !== '');
  if (visible.length === 0) return null;
  return (
    <section>
      <div className="mb-1.5 text-micro uppercase tracking-wider text-muted">{title}</div>
      <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">
        {visible.map(([label, value]) => (
          <div key={label} className="rounded-control border border-line bg-surface px-3 py-2">
            <div className="text-micro uppercase tracking-wider text-muted">{label}</div>
            <div className="mt-0.5 text-body">{value}</div>
          </div>
        ))}
      </div>
    </section>
  );
}

function EmptyCustomers({ searching, onClear }: { searching?: boolean; onClear?: () => void }) {
  const { t } = useTranslation();
  if (searching) {
    // No-results state — distinct from first-run; offers a clear action.
    return (
      <EmptyState
        size="compact"
        icon={<Users size={24} />}
        title={t('common.noResults', 'Topilmadi')}
        body={t('customers.noResultsBody', { defaultValue: 'Qidiruv yoki filterlarni o‘zgartirib ko‘ring.' })}
        action={onClear
          ? <Button variant="outline" size="md" onClick={onClear}>{t('common.clear', 'Tozalash')}</Button>
          : undefined}
      />
    );
  }
  // First-run empty — no data yet. No CTA here: the floating `+` is already on
  // screen, so an in-card copy of it would be the same action twice.
  return (
    <EmptyState
      tone="accent"
      size="compact"
      icon={<Users size={24} />}
      title={t('customers.emptyTitle')}
      body={t('customers.emptyBody')}
    />
  );
}

