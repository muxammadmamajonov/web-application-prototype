import { useTranslation } from 'react-i18next';
import { useParams } from 'react-router';
import { Pencil, Trash2 } from 'lucide-react';
import { DetailDrawer, DetailSection, DetailRow, DangerZone } from '@/components/ui/DetailDrawer';
import { Badge } from '@/components/ui/Badge';
import { Button } from '@/components/ui/Button';
import { useDeletePlan } from '@/lib/mutations';
import { apiErrorMessage } from '@/lib/errors';
import { money } from '@/lib/format';
import { confirmDialog, toast } from '@/lib/feedback';
import { FEATURES } from '@/lib/planFeatures';
import type { PlanRow } from '@/lib/queries';

interface Props {
  plan: PlanRow | null;
  onClose: () => void;
  onEdit: (p: PlanRow) => void;
}

/**
 * Read-only plan detail as a right-side DrawerPanel (desktop) / full-height
 * sheet (mobile) — matches TenantDetailDrawer's structure. Sections: headline
 * price + status, Pricing, Limits, Features, Status flags. Edit button in the
 * footer; Delete in the DangerZone.
 */
export function PlanDetailDrawer({ plan, onClose, onEdit }: Props) {
  const { t, i18n } = useTranslation();
  const { locale } = useParams();
  const lng = (locale ?? i18n.language) as 'uz' | 'ru';
  const del = useDeletePlan();

  if (!plan) return null;

  const onDelete = async () => {
    if (!(await confirmDialog({
      message: t('platformPlans.deleteConfirm', "Bu tarifni o'chirishni istaysizmi?"),
      danger: true, confirmLabel: t('common.delete', "O'chirish"),
    }))) return;
    try {
      await del.mutateAsync(plan.code);
      onClose();
    } catch (ex) {
      const detail = apiErrorMessage(ex);
      toast.error(detail === 'plan_in_use'
        ? t('platformPlans.inUse', "Bu tarifdan foydalanayotgan bizneslar bor — avval ularni boshqa tarifga o'tkazing.")
        : (detail ?? t('common.errorLoading')));
    }
  };

  const yesno = (v: boolean) =>
    v ? t('common.yes', 'Ha') : t('common.no', "Yo'q");

  const description = lng === 'ru' ? plan.description_ru : plan.description_uz;
  // Enabled feature flags, resolved to their localized labels via the canonical
  // FEATURES metadata (data-driven; no plan-name hardcoding).
  const enabledFeatures = FEATURES.filter((f) => !!plan.features?.[f.key]);

  return (
    <DetailDrawer
      open={!!plan}
      onClose={onClose}
      width="lg"
      eyebrow={plan.code.toUpperCase()}
      title={lng === 'ru' ? plan.name_ru : plan.name_uz}
      badge={
        plan.is_active
          ? <Badge tone="success">{t('common.active', 'Faol')}</Badge>
          : <Badge tone="neutral">{t('common.inactive', 'Nofaol')}</Badge>
      }
      footer={
        <>
          <Button variant="outline" size="md" onClick={() => onEdit(plan)}>
            <Pencil size={16} /> {t('common.edit', 'Tahrirlash')}
          </Button>
        </>
      }
    >
      {/* Headline price */}
      <div className="rounded-card border border-line bg-surface p-4">
        <div className="flex items-baseline gap-1.5">
          {plan.is_contact_sales ? (
            <span className="font-mono text-title font-semibold tracking-tight">
              {t('platformPlans.contactPrice', 'Kelishiladi')}
            </span>
          ) : (
            <>
              <span className="font-mono text-title font-semibold tabular-nums tracking-tight">
                {money(plan.price_monthly)}
              </span>
              <span className="text-caption text-muted">
                {t('billing.currency', "so'm")}/{t('platformPlans.perMonthShort', 'oy')}
              </span>
            </>
          )}
          {plan.is_popular && (
            <span className="ml-auto">
              <Badge tone="accent">{t('platformPlans.popular', 'Eng ommabop')}</Badge>
            </span>
          )}
        </div>
        {description && (
          <p className="mt-2 text-small text-ink-soft">{description}</p>
        )}
      </div>

      {/* Limits */}
      <DetailSection title={t('platformPlans.secLimits', 'Limitlar')}>
        <div className="rounded-card border border-line">
          <DetailRow label={t('platformPlans.activeCap', 'Faol shartnoma')}>
            {plan.max_active_installments != null ? String(plan.max_active_installments) : '∞'}
          </DetailRow>
          <DetailRow label={t('tenantStaff.colUser', 'Xodim')}>
            {String(plan.max_staff ?? 1)}
          </DetailRow>
          <DetailRow label={t('platformPlans.broadcasts', 'Eʼlon / oy')}>
            {String(plan.broadcasts_per_month ?? 0)}
          </DetailRow>
          <DetailRow label={t('platformPlans.smsMonth', 'SMS / oy')}>
            {String(plan.sms_included_per_month ?? 0)}
          </DetailRow>
        </div>
      </DetailSection>

      {/* Features */}
      <DetailSection title={t('platformPlans.features', 'Imkoniyatlar')}>
        <div className="rounded-card border border-line">
          <DetailRow label={t('platformPlans.botMode', 'Bot rejimi')}>
            {plan.bot_mode ?? 'shared'}
          </DetailRow>
          <DetailRow label={t('platformPlans.customDomain', 'Custom domen')}>
            {yesno(!!plan.allow_custom_domain)}
          </DetailRow>
          <DetailRow label={t('platformPlans.multiBranch', "Ko'p filial")}>
            {yesno(!!plan.multi_branch)}
          </DetailRow>
          <DetailRow label={t('platformPlans.reportsAdv', 'Kengaytirilgan hisobot')}>
            {yesno(!!plan.reports_advanced)}
          </DetailRow>
        </div>
        {enabledFeatures.length > 0 && (
          <div className="mt-2 flex flex-wrap gap-1.5">
            {enabledFeatures.map((f) => (
              <Badge key={f.key} tone="accent">{lng === 'ru' ? f.ru : f.uz}</Badge>
            ))}
          </div>
        )}
      </DetailSection>

      {/* Status & visibility flags */}
      <DetailSection title={t('platformPlans.secStatus', "Holat va ko'rinish")}>
        <div className="rounded-card border border-line">
          <DetailRow label={t('platformPlans.filterStatus', 'Holat')}>
            {plan.is_active
              ? <Badge tone="success">{t('common.active', 'Faol')}</Badge>
              : <Badge tone="neutral">{t('common.inactive', 'Nofaol')}</Badge>}
          </DetailRow>
          <DetailRow label={t('platformPlans.public', "Landingda ko'rsatish")}>
            {yesno(!!plan.is_public)}
          </DetailRow>
          <DetailRow label={t('platformPlans.popular', 'Eng ommabop')}>
            {yesno(!!plan.is_popular)}
          </DetailRow>
          <DetailRow label={t('platformPlans.contactSales', 'Narx yashirin (kelishiladi)')}>
            {yesno(!!plan.is_contact_sales)}
          </DetailRow>
        </div>
      </DetailSection>

      {/* Danger zone — delete */}
      <DangerZone
        title={t('platformPlans.dangerTitle', 'Xavfli hudud')}
        description={t('platformPlans.dangerBody', "Agar tarifdan foydalanayotgan bizneslar bo'lsa, avval ularni boshqa tarifga o'tkazing.")}
      >
        <Button size="md" variant="danger" loading={del.isPending} onClick={onDelete}>
          <Trash2 size={14} />
          {t('platformPlans.delete', "Tarifni o'chirish")}
        </Button>
      </DangerZone>
    </DetailDrawer>
  );
}
