import { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { useNavigate } from 'react-router';
import {
  Check, Copy, CreditCard, Link2, Loader2, LogIn, Pencil, Plus, ShieldCheck, Trash2, Wallet,
} from 'lucide-react';
import { DetailDrawer, DetailSection, DangerZone } from '@/components/ui/DetailDrawer';
import { Button } from '@/components/ui/Button';
import { Select } from '@/components/ui/Select';
import { MoneyInput } from '@/components/ui/FormFields';
import { StatusBadge } from '@/components/ui/Badge';
import { Avatar } from '@/components/ui/Avatar';
import { Skeleton } from '@/components/ui/Skeleton';
import {
  useSetTenantStatus, useDeleteTenant, useTenantOwnerInvite,
  useTopupBalance, useSetTenantPlan,
} from '@/lib/mutations';
import { useTenantDetail, usePlatformPlans, type TenantRow } from '@/lib/queries';
import { apiErrorMessage } from '@/lib/errors';
import { PlanOverridesModal } from './PlanOverridesModal';
import { useSession } from '@/store/session';
import { money, shortDate } from '@/lib/format';
import { confirmDialog, toast } from '@/lib/feedback';
import { V2_SURFACES_ENABLED } from '@/routes/_scope';
import { displayPhone } from '@/lib/phone';

interface Props { tenant: TenantRow | null; onClose: () => void }

const ALL_STATUSES = ['active', 'demo', 'suspended', 'blocked'] as const;
type TenantStatusValue = typeof ALL_STATUSES[number];

/**
 * Statuses an operator may MOVE a tenant to.
 *
 * `demo` is the trial lifecycle: `expire_demos` suspends or blocks those tenants
 * when their trial elapses. Trials are deferred, so an operator must not be able
 * to push a production shop into that lifecycle. A tenant already in demo still
 * shows its own status (so legacy rows read correctly) and can move out.
 */
function selectableStatuses(current: string | null | undefined): readonly TenantStatusValue[] {
  if (V2_SURFACES_ENABLED) return ALL_STATUSES;
  return ALL_STATUSES.filter((s) => s !== 'demo' || current === 'demo');
}

/**
 * Tenant detail/edit as a right-side drawer (desktop) / full-height sheet
 * (mobile) — replaces the old cramped centered modal. Sections: identity header,
 * primary "enter dashboard", metric grid, balance top-up, plan/status settings,
 * owner link, and a separated danger zone. Every action has its own loading/error.
 */
export function TenantDetailDrawer({ tenant, onClose }: Props) {
  const { t, i18n } = useTranslation();
  const nav = useNavigate();
  const lng = i18n.language?.startsWith('ru') ? 'ru' : 'uz';
  const enterTenant = useSession((s) => s.enterTenant);
  const canManage = useSession((s) => s.canManagePlatform());

  const detail = useTenantDetail(tenant?.id ?? null);
  const d = detail.data;
  const plans = usePlatformPlans();
  // Free-form plans (R36) + the built-in "trial" tier.
  //
  // Moving a live shop ONTO a trial starts a deferred trial lifecycle, so it is
  // not offered. A tenant already on trial keeps `trial` as an option for that
  // row only, so legacy tenants stay readable instead of rendering blank.
  const currentPlan = d?.plan_code ?? tenant?.plan_code ?? null;
  const planOptions = [
    ...(V2_SURFACES_ENABLED || currentPlan === 'trial' ? [{ value: 'trial', label: 'TRIAL' }] : []),
    ...(plans.data ?? []).map((p) => ({ value: p.code, label: p.code.toUpperCase() })),
  ];

  const setStatus = useSetTenantStatus();
  const del = useDeleteTenant();
  const ownerInvite = useTenantOwnerInvite();
  const topup = useTopupBalance();
  const setPlan = useSetTenantPlan();

  const [copied, setCopied] = useState(false);
  const [link, setLink] = useState<string | null>(null);
  const [topupAmt, setTopupAmt] = useState(0);
  const [overridesOpen, setOverridesOpen] = useState(false);

  useEffect(() => { setLink(null); setCopied(false); setTopupAmt(0); }, [tenant?.id]);
  if (!tenant) return null;

  const fail = (ex: unknown) => {
    toast.error(apiErrorMessage(ex));
  };

  const generate = async () => {
    try { setLink((await ownerInvite.mutateAsync(tenant.id)).share_url); } catch (ex) { fail(ex); }
  };
  const copyShare = async () => {
    if (!link) return;
    await navigator.clipboard.writeText(link);
    setCopied(true); setTimeout(() => setCopied(false), 1500);
  };
  // Crediting a tenant's balance moves real money: restate tenant, amount and
  // resulting balance before the write, and state the new balance only after the
  // server confirms it.
  const onTopup = async () => {
    if (!topupAmt) return;
    const before = d?.balance ?? 0;
    if (!(await confirmDialog({
      title: t('platformTenants.topupConfirmTitle', 'Balansni to‘ldirishni tasdiqlang'),
      message: t('platformTenants.topupConfirmBody', {
        tenant: tenant.name,
        amount: `${money(topupAmt)} ${t('billing.currency')}`,
        after: `${money(before + topupAmt)} ${t('billing.currency')}`,
        defaultValue: '{{tenant}} balansiga {{amount}} qo‘shiladi. Yangi balans: {{after}}.',
      }),
      confirmLabel: t('platformTenants.topup', 'To‘ldirish'),
    }))) return;
    try {
      await topup.mutateAsync({ id: tenant.id, amount: topupAmt });
      setTopupAmt(0);
      toast.success(t('platformTenants.toppedUpWithBalance', {
        tenant: tenant.name,
        balance: `${money(before + topupAmt)} ${t('billing.currency')}`,
        defaultValue: '{{tenant}} balansi to‘ldirildi. Yangi balans: {{balance}}.',
      }));
    } catch (ex) { fail(ex); }
  };
  const onPlan = async (code: string) => {
    if (code === d?.plan_code) return;
    const cur = (d?.plan_code ?? tenant.plan_code ?? '').toUpperCase() || '—';
    if (!(await confirmDialog({
      message: `${t('platformTenants.planChangeConfirm', "Tarifni o'zgartirilsinmi?")}  ${cur} → ${code.toUpperCase()}`,
      confirmLabel: t('common.change', "O'zgartirish"),
    }))) return;
    try { await setPlan.mutateAsync({ id: tenant.id, plan_code: code }); toast.success(t('common.saved', 'Saqlandi')); }
    catch (ex) { fail(ex); }
  };
  const onStatus = async (s: string) => {
    if (s === d?.status) return;
    const cur = d?.status ?? tenant.status;
    const danger = s === 'blocked' || s === 'suspended';
    if (!(await confirmDialog({
      message: `${t('platformTenants.statusChangeConfirm', "Holatni o'zgartirilsinmi?")}  ${t(`status.${cur}`, cur)} → ${t(`status.${s}`, s)}`,
      danger,
      confirmLabel: t('common.change', "O'zgartirish"),
    }))) return;
    try { await setStatus.mutateAsync({ id: tenant.id, status: s }); toast.success(t('common.saved', 'Saqlandi')); }
    catch (ex) { fail(ex); }
  };
  // Entering a tenant opens another business's live data, so this must not read
  // as a routine control: name the tenant and state that actions inside are
  // recorded.
  const onEnter = async () => {
    if (!(await confirmDialog({
      title: t('platformTenants.enterConfirmTitle', 'Do‘kon paneliga kirish'),
      message: t('platformTenants.enterConfirmBody', {
        tenant: tenant.name,
        defaultValue: '“{{tenant}}” do‘konining haqiqiy ma’lumotlariga kirasiz. Bu yerdagi barcha amallaringiz audit jurnaliga yoziladi.',
      }),
      confirmLabel: t('platformTenants.enterDashboard', 'Boshqaruv paneliga kirish'),
    }))) return;
    enterTenant(tenant.id, tenant.name);
    onClose();
    nav(`/${lng}/tenant/overview`);
  };
  const onDelete = async () => {
    if (!(await confirmDialog({ message: t('platformTenants.deleteConfirm', "Bu biznesni butunlay o'chirishni istaysizmi? Bu amalni qaytarib bo'lmaydi."), danger: true, confirmLabel: t('common.delete') }))) return;
    try { await del.mutateAsync(tenant.id); onClose(); } catch (ex) { fail(ex); }
  };

  const metrics: { label: string; value: string }[] = d ? [
    { label: t('platformTenants.statCustomers', 'Mijozlar'), value: money(d.stats.customers) },
    { label: t('platformTenants.statContracts', 'Faol shartnoma'), value: money(d.stats.active_contracts) },
    { label: t('platformTenants.statStaff', 'Xodimlar'), value: money(d.stats.staff) },
    { label: t('platformTenants.statFinanced', 'Moliyalashtirilgan'), value: money(d.stats.financed) },
    { label: t('platformTenants.statReceived', 'Qabul qilingan'), value: money(d.stats.received) },
    { label: t('platformTenants.statCreated', 'Yaratilgan'), value: shortDate(d.created_at) },
  ] : [];

  return (
    <DetailDrawer
      open={!!tenant}
      onClose={onClose}
      width="xl"
      leading={<Avatar name={tenant.name} size="md" />}
      eyebrow={(d?.plan_code ?? tenant.plan_code)?.toUpperCase()}
      title={tenant.name}
      subtitle={tenant.slug}
      badge={<StatusBadge status={d?.status ?? tenant.status} />}
    >
      {/* Primary action — support access into the tenant's own dashboard. */}
      <Button variant="primary" size="md" className="w-full" onClick={() => void onEnter()}>
        <LogIn size={16} /> {t('platformTenants.enterDashboard', 'Boshqaruv paneliga kirish')}
      </Button>

      {/* Contact line — phone / billing email when present. */}
      {(d?.contact_phone || d?.billing_email) && (
        <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-caption text-ink-soft">
          {d.contact_phone && <span className="tabular-nums">{displayPhone(d.contact_phone)}</span>}
          {d.billing_email && <span className="min-w-0 truncate">{d.billing_email}</span>}
        </div>
      )}

      {/* Metric grid */}
      <DetailSection title={t('platformTenants.secMetrics', "Ko'rsatkichlar")}>
        <div className="grid grid-cols-2 gap-2 sm:grid-cols-3">
          {detail.isLoading
            ? Array.from({ length: 6 }).map((_, i) => (
                <div key={i} className="rounded-control border border-line bg-cream px-3 py-2.5">
                  <Skeleton className="h-2.5 w-2/3" /><Skeleton className="mt-2 h-4 w-1/2" />
                </div>
              ))
            : metrics.map((m) => (
                <div key={m.label} className="rounded-control border border-line bg-cream px-3 py-2.5">
                  <div className="truncate text-micro uppercase tracking-wider text-muted">{m.label}</div>
                  <div className="mt-0.5 truncate text-body font-semibold tabular-nums">{m.value}</div>
                </div>
              ))}
        </div>
      </DetailSection>

      {canManage ? (
        <>
          {/* Balance + top-up */}
          <DetailSection title={t('platformTenants.secBalance', 'Balans')}>
            <div className="rounded-card border border-line bg-surface p-4">
              <div className="flex items-center justify-between gap-3">
                <span className="inline-flex items-center gap-2 text-caption uppercase tracking-wider text-muted"><Wallet size={14} /> {t('platformTenants.balance', 'Balans')}</span>
                <span className="font-mono text-section font-semibold tabular-nums">{money(d?.balance ?? 0)} <span className="text-micro text-muted">{t('billing.currency')}</span></span>
              </div>
              <div className="mt-3 flex flex-col gap-2 sm:flex-row">
                <MoneyInput className="flex-1" value={topupAmt} onChange={setTopupAmt}
                            placeholder={t('platformTenants.topupPh', 'Summa, so‘m')} />
                {/* `loading` and `disabled` are separate axes: `loading` means a write is
                    in flight (Button draws the only spinner), `disabled` means the amount
                    is not a valid one yet. Folding `!topupAmt` into `loading` spun the
                    button forever on an empty field. */}
                <Button variant="outline" size="md" className="sm:w-auto" onClick={onTopup}
                        loading={topup.isPending} disabled={!topupAmt}>
                  {topup.isPending ? null : <Plus size={14} />}
                  {t('platformTenants.topup', 'To‘ldirish')}
                </Button>
              </div>
            </div>
          </DetailSection>

          {/* Plan + status settings */}
          <DetailSection title={t('platformTenants.secSettings', 'Sozlamalar')}>
            <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
              <label className="flex flex-col gap-1.5 text-caption font-medium text-muted">
                <span className="inline-flex items-center gap-1.5"><CreditCard size={14} /> {t('platformTenants.colPlan')}</span>
                <Select value={d?.plan_code ?? tenant.plan_code} disabled={setPlan.isPending} onChange={onPlan} options={planOptions} />
              </label>
              <label className="flex flex-col gap-1.5 text-caption font-medium text-muted">
                <span className="inline-flex items-center gap-1.5"><ShieldCheck size={14} /> {t('platformTenants.colStatus')}</span>
                <Select value={d?.status ?? tenant.status} disabled={setStatus.isPending}
                        onChange={(v) => onStatus(v as TenantStatusValue)}
                        options={selectableStatuses(d?.status ?? tenant.status)
                          .map((s) => ({ value: s, label: t(`status.${s}`, s) }))} />
              </label>
            </div>
            {d && (
              <Button variant="outline" size="md" className="mt-3 w-full" onClick={() => setOverridesOpen(true)}>
                <Pencil size={16} /> {t('platformTenants.customPlan', 'Maxsus shartlar (narx / limit / overage)')}
              </Button>
            )}
          </DetailSection>

          {/* Owner invite link */}
          <DetailSection title={t('platformTenants.shareLink', 'Boshqaruv havolasi')}>
            {link ? (
              <div className="flex items-center gap-2">
                <code className="min-w-0 flex-1 truncate rounded-control border border-line bg-cream px-3 py-2.5 font-mono text-caption">{link}</code>
                <Button size="md" variant="outline" className="shrink-0" onClick={copyShare}>
                  {copied ? <Check size={14} /> : <Copy size={14} />}
                  {copied ? t('common.copied', 'Nusxalandi') : t('common.copy', 'Nusxa olish')}
                </Button>
              </div>
            ) : (
              <Button size="md" variant="outline" className="w-full" onClick={generate} loading={ownerInvite.isPending}>
                {ownerInvite.isPending ? <Loader2 size={14} className="animate-spin" /> : <Link2 size={14} />}
                {t('platformTenants.genOwnerLink', 'Egasi uchun havola yaratish')}
              </Button>
            )}
            <p className="mt-2 text-micro text-muted">{t('platformTenants.shareHintToken')}</p>
          </DetailSection>

          {/* Destroying a live shop's data is not an operator capability here —
              the retained way to stop a shop is the status control above. */}
          {V2_SURFACES_ENABLED && (
          <DangerZone title={t('platformTenants.dangerTitle', 'Xavfli hudud')} description={t('platformTenants.dangerBody', "Bu amal qaytarilmaydi.")}>
            <Button size="md" variant="danger" loading={del.isPending} onClick={onDelete}>
              {del.isPending ? <Loader2 size={14} className="animate-spin" /> : <Trash2 size={14} />}
              {t('platformTenants.delete', "Biznesni o'chirish")}
            </Button>
          </DangerZone>
          )}
        </>
      ) : (
        <div className="rounded-control border border-line bg-cream px-4 py-3 text-caption text-muted">
          {t('platform.supportReadOnly', 'Support rejimida — ko‘rish mumkin, o‘zgartirish faqat super-admin uchun.')}
        </div>
      )}

      {overridesOpen && <PlanOverridesModal tenant={d ?? null} onClose={() => setOverridesOpen(false)} />}
    </DetailDrawer>
  );
}
