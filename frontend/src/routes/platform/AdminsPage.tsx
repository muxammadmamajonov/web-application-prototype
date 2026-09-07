import { useEffect, useMemo, useState, type ReactNode } from 'react';
import { useTranslation } from 'react-i18next';
import {
  ChevronRight, ShieldCheck,
} from 'lucide-react';
import { FloatingCreateAction } from '@/components/ui/FloatingCreateAction';
import { Button } from '@/components/ui/Button';
import { Avatar } from '@/components/ui/Avatar';
import { Badge } from '@/components/ui/Badge';
import { EmptyState } from '@/components/ui/EmptyState';
import { DataTable, type Column } from '@/components/ui/DataTable';
import { SearchInput } from '@/components/ui/SearchInput';
import { SkeletonList } from '@/components/ui/Skeleton';
import { ErrorCard } from '@/components/ui/ErrorCard';
import { DetailModal } from '@/components/ui/DetailModal';
import { Select } from '@/components/ui/Select';
import { Switch } from '@/components/ui/Switch';
import { PageHeader } from '@/components/ui/PageHeader';
import { MobileHeader } from '@/components/mobile/primitives';
import {
  FilterBar, FilterButton, FilterDrawer, ActiveFilters, RadioGroup,
} from '@/components/ui/FilterDrawer';
import { usePlatformAdmins, type AdminRow } from '@/lib/queries';
import { apiErrorMessage } from '@/lib/errors';
import { useUpdateAdmin, useRemoveAdmin } from '@/lib/mutations';
import { confirmDialog, toast } from '@/lib/feedback';
import { displayName, shortDate } from '@/lib/format';
import { useSession } from '@/store/session';
import { AdminInviteLinkModal } from './_modals/AdminInviteLinkModal';

const ROLE_OPTS = ['all', 'super_admin', 'support'] as const;
type RoleOpt = (typeof ROLE_OPTS)[number];
type ActiveOpt = 'all' | 'active' | 'inactive';
type AdminSort = 'default' | 'name' | 'seen';

/** Row → the shared display name. The `tg 12345` tail this used to carry leaked
 *  a Telegram id into the UI and disagreed with every other screen — see
 *  `displayName` for the one chain now used everywhere. */
const adminName = (a: AdminRow) =>
  displayName({ first: a.first_name, last: a.last_name, username: a.telegram_username });

/** Role chip — accent for super_admin, neutral for support. */
function RoleBadge({ role }: { role: string }) {
  const { t } = useTranslation();
  return (
    <Badge tone={role === 'super_admin' ? 'accent' : 'neutral'}>
      {t(`platformAdmins.roles.${role}`, role)}
    </Badge>
  );
}

/** One admin row in the mobile card list — avatar + name + role + activity. */
function AdminCard({ admin, onOpen }: { admin: AdminRow; onOpen: () => void }) {
  const { t } = useTranslation();
  const name = adminName(admin);
  return (
    <div
      role="button"
      tabIndex={0}
      onClick={onOpen}
      onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); onOpen(); } }}
      className="cursor-pointer rounded-card border border-line bg-surface p-4 transition hover:border-ink/15 hover:shadow-card focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ink/20"
    >
      <div className="flex items-start gap-3">
        <Avatar name={name} photoUrl={admin.photo_url} size="md" />
        <div className="min-w-0 flex-1">
          <div className="truncate font-medium">{name}</div>
          <div className="truncate text-caption text-muted">
            {admin.telegram_username ? `@${admin.telegram_username}` : `tg ${admin.telegram_id}`}
          </div>
        </div>
        <ChevronRight size={16} className="mt-1 shrink-0 text-muted" />
      </div>
      <div className="mt-3 flex flex-wrap items-center gap-2">
        <RoleBadge role={admin.platform_role} />
        {admin.is_active
          ? <Badge tone="success">{t('common.active')}</Badge>
          : <Badge tone="neutral">{t('common.inactive')}</Badge>}
      </div>
      <div className="mt-3 flex items-center justify-between gap-2 border-t border-line pt-3 text-caption text-muted">
        <span className="truncate">{t('platformAdmins.colSeen', 'Faollik')}</span>
        <span className="shrink-0 tabular-nums">{shortDate(admin.last_seen_at)}</span>
      </div>
    </div>
  );
}

export default function PlatformAdminsPage() {
  const { t } = useTranslation();
  const [linkOpen, setLinkOpen] = useState(false);
  const [detail, setDetail] = useState<AdminRow | null>(null);
  const [role, setRole] = useState<RoleOpt>('all');
  const [activeOpt, setActiveOpt] = useState<ActiveOpt>('all');
  const [sort, setSort] = useState<AdminSort>('default');
  const [filterOpen, setFilterOpen] = useState(false);
  const [q, setQ] = useState('');
  const [debouncedQ, setDebouncedQ] = useState('');
  useEffect(() => {
    const id = setTimeout(() => setDebouncedQ(q.trim().toLowerCase()), 250);
    return () => clearTimeout(id);
  }, [q]);

  const query = usePlatformAdmins();
  const allRows = useMemo(() => query.data ?? [], [query.data]);
  const canManage = useSession((s) => s.canManagePlatform());

  const rows = useMemo(() => allRows.filter((a) => {
    if (role !== 'all' && a.platform_role !== role) return false;
    if (activeOpt === 'active' && !a.is_active) return false;
    if (activeOpt === 'inactive' && a.is_active) return false;
    if (debouncedQ && !(`${adminName(a)} ${a.telegram_username ?? ''}`.toLowerCase().includes(debouncedQ))) return false;
    return true;
  }).slice().sort((a, b) => {
    if (sort === 'name') return adminName(a).localeCompare(adminName(b));
    if (sort === 'seen') return (b.last_seen_at ?? '').localeCompare(a.last_seen_at ?? '');
    return 0;
  }), [allRows, role, activeOpt, debouncedQ, sort]);

  const activeFilters = (role !== 'all' ? 1 : 0) + (activeOpt !== 'all' ? 1 : 0) + (sort !== 'default' ? 1 : 0);
  const clearAll = () => { setRole('all'); setActiveOpt('all'); setSort('default'); };
  const searchOrFilter = !!debouncedQ || activeFilters > 0;

  const chips = [
    role !== 'all' && { key: 'role', label: t(`platformAdmins.roles.${role}`, role), onRemove: () => setRole('all') },
    activeOpt !== 'all' && {
      key: 'active',
      label: activeOpt === 'active' ? t('common.active', 'Faol') : t('common.inactive', 'Nofaol'),
      onRemove: () => setActiveOpt('all'),
    },
    sort !== 'default' && {
      key: 'sort',
      label: sort === 'name' ? t('platformAdmins.sortName', 'Ism (A–Z)') : t('platformAdmins.sortSeen', 'Oxirgi faollik'),
      onRemove: () => setSort('default'),
    },
  ].filter(Boolean) as { key: string; label: string; onRemove: () => void }[];

  // ── Derived counts for KPI cards ─────────────────────────────────────────
  const totalAdmins = allRows.length;

  // ── Desktop table columns ───────────────────────────────────────────────── (H17)
  const cols: Column<AdminRow>[] = useMemo(() => [
    {
      key: 'name', label: t('platformAdmins.colName', 'Ism'), width: '1.6fr', truncate: false,
      render: (a) => (
        <span className="flex min-w-0 items-center gap-2.5">
          <Avatar name={adminName(a)} photoUrl={a.photo_url} size="sm" />
          <span className="block truncate font-medium">{adminName(a)}</span>
        </span>
      ),
      sortValue: (a) => adminName(a).toLowerCase(),
    },
    {
      key: 'username', label: t('platformAdmins.colUsername', 'Username'), width: '1.2fr',
      render: (a) => (
        <span className="block truncate text-small text-muted">
          {a.telegram_username ? `@${a.telegram_username}` : `tg ${a.telegram_id}`}
        </span>
      ),
    },
    {
      key: 'role', label: t('platformAdmins.colRole', 'Rol'), width: '148px', truncate: false,
      render: (a) => <RoleBadge role={a.platform_role} />,
      sortValue: (a) => a.platform_role,
    },
    {
      key: 'active', label: t('platformAdmins.activeToggle', 'Holati'), width: '110px', truncate: false,
      render: (a) => a.is_active
        ? <Badge tone="success">{t('common.active')}</Badge>
        : <Badge tone="neutral">{t('common.inactive')}</Badge>,
      sortValue: (a) => (a.is_active ? 1 : 0),
    },
    {
      key: 'seen', label: t('platformAdmins.colSeen', 'Faollik'), width: '110px', align: 'right',
      render: (a) => <span className="tabular-nums text-small text-muted">{shortDate(a.last_seen_at)}</span>,
      sortValue: (a) => a.last_seen_at ?? '',
    },
    {
      key: 'chevron', label: '', width: '28px', align: 'right',
      render: () => <ChevronRight size={16} className="text-muted" />,
    },
  ], [t]);

  // (#6) The summary strip and KpiCard grid are gone: on a list page they are
  // a preamble the reader scrolls past to reach the rows they came for, and on
  // a phone they cost most of the first paint. The figures live on Overview.


  const showToolbar = query.isLoading || totalAdmins > 0 || searchOrFilter;

  // ── Body states ───────────────────────────────────────────────────────────
  let body: ReactNode;
  if (query.isLoading) {
    body = <SkeletonList rows={5} />;
  } else if (query.isError) {
    body = <ErrorCard error={query.error} onRetry={() => query.refetch()} />;
  } else if (rows.length === 0 && searchOrFilter) {
    body = (
      <EmptyState
        size="compact"
        icon={<ShieldCheck size={24} />}
        title={t('common.noResults', 'Natija topilmadi')}
        body={t('platformAdmins.noResultsBody', 'Filtr yoki qidiruvga mos admin topilmadi.')}
        action={
          <Button variant="outline" size="md" onClick={() => { clearAll(); setQ(''); }}>
            {t('common.clear', 'Tozalash')}
          </Button>
        }
      />
    );
  } else if (rows.length === 0) {
    // First-run: accent-toned. No CTA here — the floating invite action is
    // already on screen for anyone with `canManage`, and hidden for anyone
    // without, which is exactly what this card's button used to encode.
    body = (
      <EmptyState
        tone="accent"
        size="compact"
        icon={<ShieldCheck size={24} />}
        title={t('platformAdmins.emptyTitle')}
        body={t('platformAdmins.emptyBody')}
      />
    );
  } else {
    body = (
      <>
        {/* Desktop (lg+): sortable DataTable. Below lg the sidebar leaves too
            little width, so we render rich cards through tablet. */}
        <div className="hidden lg:block">
          <DataTable
            columns={cols}
            rows={rows}
            rowKey={(a) => a.id}
            onRowClick={(a) => setDetail(a)}
          />
        </div>

        {/* Mobile + tablet: richer avatar cards. */}
        <ul className="space-y-2.5 lg:hidden">
          {rows.map((a) => (
            <li key={a.id}>
              <AdminCard admin={a} onOpen={() => setDetail(a)} />
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
          title={t('nav.admins')}
          subtitle={t('platformAdmins.pageSubtitle', 'Platforma adminlarini boshqaring va kirish huquqlarini nazorat qiling.')}
        />
      </div>
      <div className="md:hidden">
        <MobileHeader title={t('nav.admins')} />
        <p className="mb-3 max-w-prose text-small leading-snug text-muted">
          {t('platformAdmins.pageSubtitle', 'Platforma adminlarini boshqaring va kirish huquqlarini nazorat qiling.')}
        </p>
      </div>

      {/* Gated, not dimmed: a read-only admin will never gain this right, and
          `FloatingCreateAction`'s own docs reserve `disabled` for reasons that
          can change. So the button is absent for them, exactly as before. */}
      {canManage && (
        <FloatingCreateAction
          onClick={() => setLinkOpen(true)}
          label={t('platformAdmins.inviteLink', 'Havola yaratish')}
        />
      )}

      <div className="space-y-4 sm:space-y-5">
        {/* Hidden on a failed fetch: a stale/empty count set would read as
            "0 admins / 0 active" — a clean, deliberately-empty team — instead
            of a load failure. The ErrorCard rendered below (via `body`)
            already covers the failure. */}
        {showToolbar && (
          <div>
            <FilterBar>
              <SearchInput
                value={q}
                onChange={setQ}
                className="flex-1"
                placeholder={t('platformAdmins.searchPh', 'Ism yoki username…')}
              />
              <FilterButton count={activeFilters} onClick={() => setFilterOpen(true)} />
            </FilterBar>
            <ActiveFilters filters={chips} onClearAll={clearAll} />
          </div>
        )}

        {body}
      </div>

      <FilterDrawer open={filterOpen} onClose={() => setFilterOpen(false)} onClear={clearAll}>
        <RadioGroup<RoleOpt>
          label={t('platformAdmins.colRole', 'Rol')}
          value={role}
          onChange={setRole}
          options={ROLE_OPTS.map((r) => ({
            value: r,
            label: r === 'all' ? t('common.all', 'Hammasi') : t(`platformAdmins.roles.${r}`, r),
          }))}
        />
        <RadioGroup<ActiveOpt>
          label={t('platformAdmins.activeToggle', 'Holati')}
          value={activeOpt}
          onChange={setActiveOpt}
          options={[
            { value: 'all',      label: t('common.all', 'Hammasi') },
            { value: 'active',   label: t('common.active', 'Faol') },
            { value: 'inactive', label: t('common.inactive', 'Nofaol') },
          ]}
        />
        <RadioGroup<AdminSort>
          label={t('common.sort', 'Saralash')}
          value={sort}
          onChange={setSort}
          options={[
            { value: 'default', label: t('platformAdmins.sortDefault', 'Standart') },
            { value: 'name',    label: t('platformAdmins.sortName', 'Ism (A–Z)') },
            { value: 'seen',    label: t('platformAdmins.sortSeen', 'Oxirgi faollik') },
          ]}
        />
      </FilterDrawer>

      <AdminInviteLinkModal open={linkOpen} onClose={() => setLinkOpen(false)} />
      <AdminDetailModal admin={detail} onClose={() => setDetail(null)} />
    </>
  );
}

// ── Admin detail modal (inline — reads the open admin profile) ────────────

function AdminDetailModal({ admin, onClose }: { admin: AdminRow | null; onClose: () => void }) {
  const { t } = useTranslation();
  const upd = useUpdateAdmin();
  const del = useRemoveAdmin();
  const canManage = useSession((s) => s.canManagePlatform());
  const meId = useSession((s) => s.me?.id);
  const [role, setRole] = useState('support');
  const [active, setActive] = useState(true);
  const [roleErr, setRoleErr] = useState<string | null>(null);

  useEffect(() => {
    if (admin) { setRole(admin.platform_role); setActive(admin.is_active); setRoleErr(null); }
  }, [admin]);

  if (!admin) return null;
  const name = adminName(admin);
  const isSelf = admin.id === meId;

  const fail = (ex: unknown) => {
    toast.error(apiErrorMessage(ex));
  };

  const saveRole = async (next: string) => {
    setRole(next);
    setRoleErr(null);
    if (next === admin.platform_role) return;
    try {
      await upd.mutateAsync({ id: admin.id, body: { role: next } });
      toast.success(t('common.saved', 'Saqlandi'));
    } catch (ex) {
      setRole(admin.platform_role);
      fail(ex);
    }
  };

  const toggleActive = async () => {
    const next = !active;
    setActive(next);
    try {
      await upd.mutateAsync({ id: admin.id, body: { is_active: next } });
    } catch (ex) {
      setActive(!next);
      fail(ex);
    }
  };

  // Revoking operator access is the safety-critical action on this screen, so
  // the prompt must name the person and state when access ends — not read like
  // any other row action.
  const onRemove = async () => {
    if (!(await confirmDialog({
      title: t('platformAdmins.removeConfirmTitle', 'Admin huquqini bekor qilish'),
      message: t('platformAdmins.removeConfirmBody', {
        name,
        defaultValue: '{{name}} platformaga kirish huquqini yo‘qotadi. Bu darhol kuchga kiradi va joriy seansi ham tugatiladi.',
      }),
      danger: true,
      confirmLabel: t('platformAdmins.remove', 'Adminni olib tashlash'),
    }))) return;
    try {
      await del.mutateAsync(admin.id);
      toast.success(t('platformAdmins.removed', { name, defaultValue: '{{name}} huquqi bekor qilindi.' }));
      onClose();
    }
    catch (ex) { fail(ex); }
  };

  const roleOptions = [
    { value: 'support',     label: t('platformAdmins.roles.support', "Qo'llab-quvvatlash") },
    { value: 'super_admin', label: t('platformAdmins.roles.super_admin', 'Bosh admin') },
  ];

  return (
    <DetailModal
      open={!!admin}
      onClose={onClose}
      title={t('platformAdmins.profileTitle', 'Admin profili')}
      onDelete={canManage && !isSelf ? onRemove : undefined}
      deleteLabel={t('platformAdmins.remove', 'Adminni olib tashlash')}
      deleting={del.isPending}
    >
      {/* Identity card */}
      <div className="flex flex-col items-center gap-3 rounded-card border border-line bg-cream p-5 text-center">
        <Avatar name={name} photoUrl={admin.photo_url} size="lg" />
        <div className="min-w-0">
          <div className="text-subsection font-semibold">{name}</div>
          {admin.telegram_username && (
            <a
              href={`https://t.me/${admin.telegram_username}`}
              target="_blank"
              rel="noreferrer"
              className="text-small text-accent hover:underline"
            >
              @{admin.telegram_username}
            </a>
          )}
          <div className="mt-1 text-caption text-muted">
            {t('platformAdmins.colSeen', 'Faollik')}: {shortDate(admin.last_seen_at)}
          </div>
        </div>
        <div className="flex items-center gap-2">
          <RoleBadge role={admin.platform_role} />
          {active
            ? <Badge tone="success">{t('common.active')}</Badge>
            : <Badge tone="neutral">{t('common.inactive')}</Badge>}
        </div>
      </div>

      {/* Self-guard note */}
      {canManage && isSelf && (
        <div className="rounded-control border border-line bg-cream px-4 py-3 text-caption text-muted">
          {t('platformAdmins.selfNote', "Bu sizning hisobingiz — o'zingizning rolingizni o'zgartirib yoki o'chirib bo'lmaydi.")}
        </div>
      )}

      {/* Management controls — only for other users */}
      {canManage && !isSelf && (
        <div className="space-y-4">
          {/* Role — use canonical Select, no hand-rolled toggle row */}
          <div>
            <div className="mb-1.5 text-micro font-semibold uppercase tracking-wider text-muted">
              {t('platformAdmins.colRole', 'Rol')}
            </div>
            <Select
              value={role}
              onChange={(v) => { void saveRole(v); }}
              options={roleOptions}
              disabled={upd.isPending}
            />
            {roleErr && <p className="mt-1 text-caption text-danger">{roleErr}</p>}
          </div>

          {/* Active toggle — proper Switch component */}
          <div className="flex items-center justify-between gap-4 rounded-control border border-line bg-cream px-4 py-3">
            <div>
              <div className="text-small font-medium">{t('platformAdmins.activeToggle', 'Faol holati')}</div>
              <div className="text-caption text-muted">
                {active
                  ? t('platformAdmins.activeHint', 'Admin tizimga kira oladi')
                  : t('platformAdmins.inactiveHint', 'Admin tizimdan chiqarilgan')}
              </div>
            </div>
            <Switch
              checked={active}
              onChange={() => { void toggleActive(); }}
              disabled={upd.isPending}
              label={t('platformAdmins.activeToggle', 'Faol holati')}
            />
          </div>
        </div>
      )}
    </DetailModal>
  );
}
