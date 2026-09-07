import { useMemo, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { ChevronRight, Link2, Users } from 'lucide-react';
import { InvitationLinkModal } from './_modals/InvitationLinkModal';
import { PageHeader } from '@/components/ui/PageHeader';
import { FloatingCreateAction } from '@/components/ui/FloatingCreateAction';
import { Button } from '@/components/ui/Button';
import { Badge } from '@/components/ui/Badge';
import { RowCard } from '@/components/ui/RowCard';
import { DataTable, type Column } from '@/components/ui/DataTable';
import { EmptyState } from '@/components/ui/EmptyState';
import { ErrorCard } from '@/components/ui/ErrorCard';
import { Field } from '@/components/ui/Modal';
import { Select } from '@/components/ui/Select';
import { DetailModal } from '@/components/ui/DetailModal';
import { SearchInput } from '@/components/ui/SearchInput';
import {
  FilterBar, FilterButton, FilterDrawer, RadioGroup, DateRangeGroup,
  matchesDatePreset, type DatePreset,
} from '@/components/ui/FilterDrawer';
import { useTenantStaff, useTenantInvitations, type StaffRow, type InvitationRow } from '@/lib/queries';
import { useUpdateStaff, useRevokeStaff, useRevokeInvitation } from '@/lib/mutations';
import { displayName, shortDate } from '@/lib/format';
import { MobileHeader } from '@/components/mobile/primitives';
import { SkeletonList } from '@/components/ui/Skeleton';
import { confirmDialog, toast } from '@/lib/feedback';
import { apiErrorMessage } from '@/lib/errors';

type Role = 'manager' | 'sales';

type RoleFilter = 'all' | 'owner' | 'manager' | 'sales';
type StatusFilter = 'all' | 'active' | 'inactive';
type InviteFilter = 'all' | 'accepted' | 'pending';

/**
 * Tenant Staff (xodimlar). Invites are single-use token LINKS. Owner can change
 * roles + revoke; manager can invite. Row → detail popup; role/active are edited
 * in the popup body, Revoke is the bottom-right action.
 */
export default function TenantStaffPage() {
  const { t } = useTranslation();
  const q = useTenantStaff();
  const rows = useMemo(() => q.data ?? [], [q.data]);
  const [inviteFor, setInviteFor] = useState<Role | '__pick__' | null>(null);
  const [detail, setDetail] = useState<StaffRow | null>(null);

  // Client-side search + advanced filters over the already-loaded staff list.
  const [search, setSearch] = useState('');
  const [roleFilter, setRoleFilter] = useState<RoleFilter>('all');
  const [statusFilter, setStatusFilter] = useState<StatusFilter>('all');
  const [inviteFilter, setInviteFilter] = useState<InviteFilter>('all');
  const [datePreset, setDatePreset] = useState<DatePreset>('all');
  const [filterOpen, setFilterOpen] = useState(false);

  const isPending = (s: StaffRow) => !!s.invitation_status && s.invitation_status !== 'accepted';
  const counts = {
    owner: rows.filter((s) => s.role === 'owner').length,
    manager: rows.filter((s) => s.role === 'manager').length,
    sales: rows.filter((s) => s.role === 'sales').length,
    active: rows.filter((s) => s.is_active).length,
    inactive: rows.filter((s) => !s.is_active).length,
    accepted: rows.filter((s) => !isPending(s)).length,
    pending: rows.filter((s) => isPending(s)).length,
  };

  const filteredRows = useMemo(() => {
    const needle = search.trim().toLowerCase();
    return rows
      .filter((s) => roleFilter === 'all' || s.role === roleFilter)
      .filter((s) => statusFilter === 'all' || (statusFilter === 'active' ? s.is_active : !s.is_active))
      .filter((s) => inviteFilter === 'all' || (inviteFilter === 'pending' ? isPending(s) : !isPending(s)))
      .filter((s) => matchesDatePreset(s.last_seen_at, datePreset))
      .filter((s) => !needle || `${staffName(s)} ${s.telegram_username ?? ''}`.toLowerCase().includes(needle));
  }, [rows, search, roleFilter, statusFilter, inviteFilter, datePreset]);

  const activeFilters = (roleFilter !== 'all' ? 1 : 0) + (statusFilter !== 'all' ? 1 : 0)
    + (inviteFilter !== 'all' ? 1 : 0) + (datePreset !== 'all' ? 1 : 0);
  const resetAll = () => { setRoleFilter('all'); setStatusFilter('all'); setInviteFilter('all'); setDatePreset('all'); };

  // Opens the invitation modal directly; the role is chosen inside it and the
  // link appears in place — one step, not two modals.
  const startInvite = () => setInviteFor('__pick__');

  // Desktop (md+) table columns; the mobile RowCard list (below) is kept verbatim. (H17)
  // `activeBadge` moved inline — its only caller.
  const cols: Column<StaffRow>[] = useMemo(() => {
    const activeBadge = (s: StaffRow) => s.is_active
      ? <Badge tone="success">{t('status.active')}</Badge>
      : <Badge tone="neutral">{t('status.inactive')}</Badge>;
    return [
    {
      key: 'name', label: t('tenantStaff.colName', 'Ism'), width: '1.6fr',
      render: (s) => <span className="block truncate font-medium">{staffName(s)}</span>,
      sortValue: (s) => staffName(s).toLowerCase(),
    },
    {
      key: 'role', label: t('tenantStaff.colRole', 'Rol'), width: '120px',
      render: (s) => <Badge tone="neutral">{roleLabel(s.role, t)}</Badge>, sortValue: (s) => s.role,
    },
    {
      key: 'status', label: t('tenantStaff.colStatus', 'Holat'), width: '120px',
      render: (s) => activeBadge(s), sortValue: (s) => (s.is_active ? 'active' : 'inactive'),
    },
    {
      key: 'lastSeen', label: t('tenantStaff.colLastSeen', 'Oxirgi faollik'), width: '130px', align: 'right',
      render: (s) => <span className="tabular-nums text-small text-muted">{shortDate(s.last_seen_at)}</span>,
      sortValue: (s) => s.last_seen_at ?? '',
    },
    {
      // "View" is the only row action — the row itself is already clickable
      // (onRowClick below), so a kebab here would just duplicate that one
      // action in a menu (spec §3). A static trailing chevron signals
      // "open" without adding an extra tap, matching platform/AdminsPage.
      key: 'chevron', label: '', width: '28px', align: 'right',
      render: () => <ChevronRight size={16} className="text-muted" />,
    },
    ];
  }, [t]);

  return (
    <>
      <div className="hidden md:block">
        <PageHeader
          title={t('nav.staff')}
          subtitle={t('tenantStaff.subtitle', { count: rows.length })}
        />
      </div>
      <div className="md:hidden">
        <MobileHeader title={t('nav.staff')} />
        <p className="mb-3 max-w-prose text-small leading-snug text-muted">
          {t('tenantStaff.subtitle', { count: rows.length })}
        </p>
      </div>

      {/* Keeps the Link2 glyph — this mints an invite link, which is not the
          same gesture as adding a row, and the icon has to say so. */}
      <FloatingCreateAction
        onClick={startInvite}
        icon={Link2}
        label={t('tenantStaff.inviteLink', 'Havola yaratish')}
      />

      <FilterBar>
        <SearchInput value={search} onChange={setSearch} className="flex-1"
          placeholder={t('tenantStaff.searchPh', 'Ism yoki @username bo‘yicha qidirish…')} />
        <FilterButton count={activeFilters} onClick={() => setFilterOpen(true)} />
      </FilterBar>

      <FilterDrawer open={filterOpen} onClose={() => setFilterOpen(false)} onClear={resetAll}>
        <RadioGroup<RoleFilter>
          label={t('tenantStaff.colRole', 'Rol')}
          value={roleFilter}
          onChange={setRoleFilter}
          options={[
            { value: 'all', label: t('common.all', 'Hammasi') },
            { value: 'owner', label: roleLabel('owner', t), count: counts.owner },
            { value: 'manager', label: roleLabel('manager', t), count: counts.manager },
            { value: 'sales', label: roleLabel('sales', t), count: counts.sales },
          ]}
        />
        <RadioGroup<StatusFilter>
          label={t('tenantStaff.colStatus', 'Holat')}
          value={statusFilter}
          onChange={setStatusFilter}
          options={[
            { value: 'all', label: t('common.all', 'Hammasi') },
            { value: 'active', label: t('status.active'), count: counts.active },
            { value: 'inactive', label: t('status.inactive'), count: counts.inactive },
          ]}
        />
        <RadioGroup<InviteFilter>
          label={t('tenantStaff.filterInvite', 'Taklif holati')}
          value={inviteFilter}
          onChange={setInviteFilter}
          options={[
            { value: 'all', label: t('common.all', 'Hammasi') },
            { value: 'accepted', label: t('tenantStaff.inviteAccepted', 'Qabul qilingan'), count: counts.accepted },
            { value: 'pending', label: t('tenantStaff.pending', 'Havola yuborildi'), count: counts.pending },
          ]}
        />
        <DateRangeGroup label={t('tenantStaff.colLastSeen', 'Oxirgi faollik')} value={datePreset} onChange={setDatePreset} />
      </FilterDrawer>

      {q.isLoading ? (
        <SkeletonList rows={4} />
      ) : q.isError ? (
        <ErrorCard error={q.error} onRetry={() => q.refetch()} />
      ) : rows.length === 0 ? (
        <EmptyStaff />
      ) : filteredRows.length === 0 ? (
        <EmptyState
          size="compact"
          icon={<Users size={24} />}
          title={t('common.noResults', 'Topilmadi')}
          body={t('tenantStaff.emptyFilteredHint', 'Filtrlarni tozalab, barcha xodimlarni ko‘ring')}
          action={(activeFilters > 0 || search.trim())
            ? <Button variant="outline" size="md" onClick={() => { resetAll(); setSearch(''); }}>{t('common.clear', 'Tozalash')}</Button>
            : undefined}
        />
      ) : (
        <>
          {/* Desktop (md+): dense sortable table that uses the wide screen. */}
          <div className="hidden md:block">
            <DataTable columns={cols} rows={filteredRows} rowKey={(s) => s.id} onRowClick={(s) => setDetail(s)} />
          </div>

          {/* Mobile: richer avatar RowCards. */}
          <ul className="space-y-2 md:hidden">
            {filteredRows.map((s) => (
              <li key={s.id}>
                {/* Row opens the detail popup on click — "view" is the only
                    action, so the row itself is the affordance (chevron, no
                    kebab; spec §3). The wrapper is a div[role=button] (not a
                    <button>) so RowCard's own internals nest legally. */}
                <div
                  role="button"
                  tabIndex={0}
                  onClick={() => setDetail(s)}
                  onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); setDetail(s); } }}
                  className="cursor-pointer rounded-card focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ink/20"
                >
                  <RowCard
                    initial={staffName(s)}
                    title={staffName(s)}
                    subtitle={`${roleLabel(s.role, t)} · ${staffSub(s, t('tenantStaff.pending', 'Havola yuborildi'))}`}
                    leftBadge={s.is_active ? <Badge tone="success">{t('status.active')}</Badge> : <Badge tone="neutral">{t('status.inactive')}</Badge>}
                    trailing={
                      <span className="flex items-center gap-1.5">
                        <span className="hidden text-caption tabular-nums text-muted sm:block">{shortDate(s.last_seen_at)}</span>
                        <ChevronRight size={16} className="shrink-0 text-muted" />
                      </span>
                    }
                  />
                </div>
              </li>
            ))}
          </ul>
        </>
      )}

      {/* A staff invitation link grants access to the shop's data, so there
          must be a way to kill one — a link forwarded to the wrong person
          otherwise stays live until it expires. */}
      <OutstandingInvitations />

      {detail && (
        <StaffDetailModal s={detail} onClose={() => setDetail(null)} />
      )}

      <InvitationLinkModal
        open={inviteFor !== null}
        onClose={() => setInviteFor(null)}
        kind="staff"
        role={inviteFor && inviteFor !== '__pick__' ? inviteFor : undefined}
        roleChoices={['sales', 'manager']}
      />
    </>
  );
}

const ROLE_LABEL_KEY: Record<string, string> = {
  owner: 'menu.roleOwner', manager: 'menu.roleManager', sales: 'menu.roleSales',
};
/** Localized role label — reuses the same `menu.role*` keys MenuPage/ProfilePage
 *  already render, so "Owner"/"Manager"/"Sales" never leak as raw English. */
function roleLabel(role: string, t: ReturnType<typeof useTranslation>['t']) {
  return t(ROLE_LABEL_KEY[role] ?? role, role);
}
/** Row → the shared display name. This copy dropped the `@` from the handle
 *  while the admin/user directories kept it; `displayName` owns that choice
 *  now, so one person reads the same on every screen. */
function staffName(s: StaffRow) {
  return displayName({ first: s.first_name, last: s.last_name, username: s.telegram_username });
}
function staffSub(s: StaffRow, pendingLabel: string) {
  if (s.telegram_username) return `@${s.telegram_username}`;
  if (s.invitation_status && s.invitation_status !== 'accepted') return pendingLabel;
  return '—';
}

/** Detail popup — role + active edited inline in the body; Revoke bottom-right. */
function StaffDetailModal({ s, onClose }: { s: StaffRow; onClose: () => void }) {
  const { t } = useTranslation();
  const upd = useUpdateStaff();
  const del = useRevokeStaff();
  const fail = (ex: unknown) => {
    toast.error(apiErrorMessage(ex));
  };
  // (#450) Privilege changes are consequential and easy to mis-click in a
  // dropdown: confirm escalations (sales < manager < owner) and deactivations
  // BEFORE the existing mutation, and always toast success — silent success
  // left the owner unsure the change took. Reversal is the same control.
  const ROLE_RANK: Record<string, number> = { sales: 0, manager: 1, owner: 2 };
  const onRoleChange = async (role: string) => {
    if (role === s.role) return;
    const escalation = (ROLE_RANK[role] ?? 0) > (ROLE_RANK[s.role] ?? 0);
    if (escalation && !(await confirmDialog({
      message: t('tenantStaff.escalateConfirm', { name: staffName(s), role: roleLabel(role, t), defaultValue: `${staffName(s)} huquqi ${roleLabel(role, t)} darajasiga oshirilsinmi?` }),
      danger: true,
      confirmLabel: t('common.confirm'),
    }))) return;
    try {
      await upd.mutateAsync({ id: s.id, body: { role } });
      toast.success(t('tenantStaff.roleChanged', { role: roleLabel(role, t), defaultValue: `Rol o'zgartirildi: ${roleLabel(role, t)}` }));
    } catch (ex) { fail(ex); }
  };
  const onToggle = async () => {
    const deactivating = s.is_active;
    if (deactivating && !(await confirmDialog({
      message: t('tenantStaff.deactivateConfirm', { name: staffName(s), defaultValue: `${staffName(s)} faolsizlantirilsinmi? U tizimga kira olmaydi.` }),
      danger: true,
      confirmLabel: t('common.confirm'),
    }))) return;
    try {
      await upd.mutateAsync({ id: s.id, body: { is_active: !s.is_active } });
      toast.success(deactivating
        ? t('tenantStaff.deactivated', 'Xodim faolsizlantirildi')
        : t('tenantStaff.activated', 'Xodim faollashtirildi'));
    } catch (ex) { fail(ex); }
  };
  const onDelete = async () => {
    if (!(await confirmDialog({ message: t('tenantStaff.revokeConfirm', { name: staffName(s) }), danger: true, confirmLabel: t('common.delete') }))) return;
    try { await del.mutateAsync(s.id); onClose(); } catch (ex) { fail(ex); }
  };
  const isOwner = s.role === 'owner';

  return (
    <DetailModal
      open
      onClose={onClose}
      title={staffName(s)}
      subtitle={staffSub(s, t('tenantStaff.pending', 'Havola yuborildi'))}
      onDelete={isOwner ? undefined : onDelete}
      deleteLabel={t('tenantStaff.revoke', 'Olib tashlash')}
      deleting={del.isPending}
    >
      <div className="grid gap-4">
        <Field label={t('tenantStaff.colRole', 'Rol')}>
          <Select
            value={s.role}
            disabled={isOwner || upd.isPending}
            onChange={onRoleChange}
            options={[
              { value: 'owner', label: roleLabel('owner', t) },
              { value: 'manager', label: roleLabel('manager', t) },
              { value: 'sales', label: roleLabel('sales', t) },
            ]}
          />
        </Field>
        <Field label={t('tenantStaff.colStatus', 'Holat')}>
          <button type="button" onClick={onToggle} disabled={upd.isPending || isOwner}
            className="inline-flex items-center gap-2 self-start rounded-control border border-line px-3 py-2 text-body disabled:opacity-50">
            {s.is_active ? <Badge tone="success">{t('status.active')}</Badge> : <Badge tone="neutral">{t('status.inactive')}</Badge>}
            <span className="text-muted">{t('common.edit', 'Tahrirlash')}</span>
          </button>
        </Field>
        <div className="flex items-center justify-between border-t border-line/60 pt-3 text-body">
          <span className="text-muted">{t('tenantStaff.colLastSeen', "Oxirgi faollik")}</span>
          <span className="tabular-nums">{shortDate(s.last_seen_at)}</span>
        </div>
      </div>
    </DetailModal>
  );
}

function EmptyStaff() {
  const { t } = useTranslation();
  // No CTA: the floating invite action is already on screen behind this card.
  return (
    <EmptyState
      tone="accent"
      size="compact"
      icon={<Users size={24} />}
      title={t('tenantStaff.emptyTitle')}
      body={t('tenantStaff.emptyBody')}
    />
  );
}

/** Live staff invitations, with revocation. */
function OutstandingInvitations() {
  const { t } = useTranslation();
  const q = useTenantInvitations({ kind: 'staff', only_open: true });
  const revoke = useRevokeInvitation();
  const [revoking, setRevoking] = useState<string | null>(null);
  const rows = q.data ?? [];

  if (q.isLoading || rows.length === 0) return null;

  const onRevoke = async (inv: InvitationRow) => {
    if (!(await confirmDialog({
      title: t('tenantStaff.revokeInviteTitle', 'Taklif havolasini bekor qilish'),
      message: t('tenantStaff.revokeInviteBody', {
        role: t(`tenantStaff.role_${inv.role ?? 'sales'}`, inv.role ?? ''),
        defaultValue: 'Bu havola darhol ishlamay qoladi. {{role}} sifatida hali hech kim qo‘shilmagan.',
      }),
      danger: true,
      confirmLabel: t('tenantStaff.revokeInvite', 'Bekor qilish'),
    }))) return;
    setRevoking(inv.id);
    try {
      await revoke.mutateAsync(inv.id);
      toast.success(t('tenantStaff.inviteRevoked', 'Taklif havolasi bekor qilindi'));
    } catch (ex) { toast.error(apiErrorMessage(ex)); }
    finally { setRevoking(null); }
  };

  return (
    <section className="card mt-6 p-0">
      <div className="border-b border-line px-5 py-4">
        <h3 className="text-subsection font-semibold">
          {t('tenantStaff.outstandingInvites', 'Faol taklif havolalari')}
        </h3>
        <p className="mt-0.5 text-caption text-muted">
          {t('tenantStaff.outstandingInvitesHint', 'Hali ishlatilmagan havolalar — kerak bo‘lmasa bekor qiling.')}
        </p>
      </div>
      <ul className="divide-y divide-line">
        {rows.map((inv) => (
          <li key={inv.id} className="flex items-center justify-between gap-3 px-5 py-3">
            <span className="min-w-0">
              <span className="block text-body font-medium">
                {t(`tenantStaff.role_${inv.role ?? 'sales'}`, inv.role ?? '—')}
              </span>
              <span className="block text-caption text-muted">
                {t('invite.expiresAt')}: {shortDate(inv.expires_at)}
              </span>
            </span>
            <Button variant="outline" size="sm" loading={revoking === inv.id}
                    onClick={() => void onRevoke(inv)}
                    className="border-danger/40 text-danger hover:bg-[color:var(--c-danger-soft)]">
              {t('tenantStaff.revokeInvite', 'Bekor qilish')}
            </Button>
          </li>
        ))}
      </ul>
    </section>
  );
}
