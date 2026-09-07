import { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Loader2 } from 'lucide-react';
import { Modal, Field, FormSection, inputCls } from '@/components/ui/Modal';
import { Switch } from '@/components/ui/Switch';
import { Select } from '@/components/ui/Select';
import { Button } from '@/components/ui/Button';
import { MoneyInput } from '@/components/ui/FormFields';
import { useUpdatePlan } from '@/lib/mutations';
import { apiErrorMessage } from '@/lib/errors';
import { focusFirstInvalid, hasErrors, numOrNull } from '@/lib/forms';
import type { PlanRow, OverageRule } from '@/lib/queries';
import { FEATURES, LIMIT_KEYS, LIMIT_LABEL, type LimitKey } from '@/lib/planFeatures';

interface Props { open: boolean; onClose: () => void; plan: PlanRow | null }

// limit_key → PlanRow column for reading current included quota.
const LIMIT_FIELD: Record<LimitKey, keyof PlanRow> = {
  contract:     'max_active_installments',
  staff:        'max_staff',
  branch:       'max_branches',
  custom_field: 'max_custom_fields',
  broadcast:    'broadcasts_per_month',
};

const emptyOverage = (): OverageRule => ({ price: 0, enabled: false, mode: 'hard' });

/** Full subscription-plan editor (R39): price (via MoneyInput), included limits,
 *  per-limit overage (price + enabled + hard/soft), feature toggles, and
 *  landing flags. Every text field validates inline; blocked submit + spinner
 *  while pending. */
export function EditPlanModal({ open, onClose, plan }: Props) {
  const { t } = useTranslation();
  const m = useUpdatePlan();

  const [nameUz,    setNameUz]    = useState('');
  const [nameRu,    setNameRu]    = useState('');
  const [price,     setPrice]     = useState(0);
  const [limits,    setLimits]    = useState<Record<LimitKey, string>>({
    contract: '', staff: '', branch: '', custom_field: '', broadcast: '',
  });
  const [overage, setOverage] = useState<Record<LimitKey, OverageRule>>({
    contract:     emptyOverage(),
    staff:        emptyOverage(),
    branch:       emptyOverage(),
    custom_field: emptyOverage(),
    broadcast:    emptyOverage(),
  });
  const [features,   setFeatures]   = useState<Record<string, boolean>>({});
  const [isPopular,  setIsPopular]  = useState(false);
  const [isPublic,   setIsPublic]   = useState(true);
  const [isContact,  setIsContact]  = useState(false);
  const [active,     setActive]     = useState(true);
  const [errors,     setErrors]     = useState<Record<string, string>>({});

  useEffect(() => {
    if (!plan) return;
    setNameUz(plan.name_uz); setNameRu(plan.name_ru);
    setPrice(plan.price_monthly ?? 0);
    const lim = {} as Record<LimitKey, string>;
    for (const k of LIMIT_KEYS) {
      const v = plan[LIMIT_FIELD[k]] as number | null;
      lim[k] = v == null ? '' : String(v);   // '' = unlimited
    }
    setLimits(lim);
    const ov = {} as Record<LimitKey, OverageRule>;
    for (const k of LIMIT_KEYS) ov[k] = { ...emptyOverage(), ...(plan.overage?.[k] ?? {}) };
    setOverage(ov);
    setFeatures({ ...(plan.features ?? {}) });
    setIsPopular(plan.is_popular); setIsPublic(plan.is_public);
    setIsContact(plan.is_contact_sales); setActive(plan.is_active);
    setErrors({});
  }, [plan]);

  if (!plan) return null;

  const setLimit = (k: LimitKey, v: string) =>
    setLimits((p) => ({ ...p, [k]: v.replace(/\D/g, '') }));

  const setOv = (k: LimitKey, patch: Partial<OverageRule>) =>
    setOverage((p) => ({ ...p, [k]: { ...p[k], ...patch } }));

  const toggleFeat = (key: string) =>
    setFeatures((p) => ({ ...p, [key]: !p[key] }));

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    const errs: Record<string, string> = {};
    if (!nameUz.trim()) errs.nameUz = t('platformPlans.errNameUz', 'Tarif nomini (uz) kiriting.');
    if (!nameRu.trim()) errs.nameRu = t('platformPlans.errNameRu', 'Tarif nomini (ru) kiriting.');
    if (!Number.isFinite(price) || price < 0)
      errs.price = t('platformPlans.errPrice', 'Oylik narx 0 yoki undan katta son boʻlishi kerak.');
    setErrors(errs);
    if (hasErrors(errs)) { focusFirstInvalid(); return; }

    await m.mutateAsync({
      code: plan.code,
      body: {
        name_uz: nameUz, name_ru: nameRu,
        price_monthly: price,
        max_active_installments: numOrNull(limits.contract),
        max_staff:        numOrNull(limits.staff),
        max_branches:     numOrNull(limits.branch),
        max_custom_fields:numOrNull(limits.custom_field),
        broadcasts_per_month: numOrNull(limits.broadcast),
        overage,
        features,
        is_popular: isPopular, is_public: isPublic, is_contact_sales: isContact,
        is_active: active,
        multi_branch:            !!features.multi_branch,
        reports_advanced:        !!(features.advanced_reports || features.better_reports),
        commission_management:   !!features.commission,
        risk_network_access:     !!features.risk_monitoring,
        allow_pdf_template_upload: !!features.pdf_template_upload,
        allow_api_keys:          !!features.api_keys,
        allow_outbound_webhooks: !!features.webhooks,
        allow_custom_domain:     !!features.custom_domain,
      },
    });
    onClose();
  };

  return (
    <Modal
      open={open}
      onClose={onClose}
      size="lg"
      title={`${t('platformPlans.editTitle', 'Tarif')}: ${plan.code.toUpperCase()}`}
      subtitle={t('platformPlans.editSub', 'Narx, limitlar, qoʻshimcha haq va imkoniyatlar')}
      footer={
        <>
          <Button variant="ghost" size="md" type="button" onClick={onClose}>
            {t('common.cancel', 'Bekor qilish')}
          </Button>
          <Button
            variant="primary"
            size="md"
            type="submit"
            form="edit-plan-form"
            loading={m.isPending}
          >
            {m.isPending && <Loader2 size={14} className="animate-spin" />}
            {t('common.save', 'Saqlash')}
          </Button>
        </>
      }
    >
      <form id="edit-plan-form" onSubmit={submit} className="grid gap-5">
        {/* Basic — names */}
        <FormSection title={t('platformPlans.secBasic', 'Asosiy')}>
          <div className="grid gap-3 md:grid-cols-2">
            <Field label={t('platformPlans.nameUz', 'Nomi (uz)')} required error={errors.nameUz}>
              <input
                className={inputCls}
                value={nameUz}
                onChange={(e) => { setNameUz(e.target.value); setErrors((p) => ({ ...p, nameUz: '' })); }}
                onBlur={(e) => { if (!e.target.value.trim()) setErrors((p) => ({ ...p, nameUz: t('platformPlans.errNameUz', 'Tarif nomini (uz) kiriting.') })); }}
                data-invalid={!!errors.nameUz}
              />
            </Field>
            <Field label={t('platformPlans.nameRu', 'Nomi (ru)')} required error={errors.nameRu}>
              <input
                className={inputCls}
                value={nameRu}
                onChange={(e) => { setNameRu(e.target.value); setErrors((p) => ({ ...p, nameRu: '' })); }}
                onBlur={(e) => { if (!e.target.value.trim()) setErrors((p) => ({ ...p, nameRu: t('platformPlans.errNameRu', 'Tarif nomini (ru) kiriting.') })); }}
                data-invalid={!!errors.nameRu}
              />
            </Field>
          </div>
        </FormSection>

        {/* Pricing — MoneyInput, never a raw number input */}
        <FormSection title={t('platformPlans.secPricing', 'Narx')}>
          <Field
            label={t('platformPlans.priceMonthly', 'Oylik narx')}
            required
            error={errors.price}
            hint={t('platformPlans.priceEditHint', "0 = bepul. \"Kelishiladi\" ni quyida yoqing.")}
          >
            <MoneyInput
              value={price}
              onChange={(v) => { setPrice(v); setErrors((p) => ({ ...p, price: '' })); }}
            />
          </Field>
        </FormSection>

        {/* Limits + overage table */}
        <FormSection
          title={t('platformPlans.limitsOverage', "Limitlar va qo'shimcha haq")}
          hint={t('platformPlans.limitHint', "Bo'sh = cheksiz. Yumshoq = limitdan oshsa qo'shimcha haq olinadi; Qatʼiy = kirish bloklanadi.")}
        >
          {/* Column header row (hidden on mobile — fields stack vertically there) */}
          <div className="hidden grid-cols-[1.3fr_1fr_1fr_1.1fr] gap-2 pb-1 sm:grid">
            <span className="text-micro font-medium uppercase tracking-kicker text-muted">{t('platformPlans.limitColName', 'Limit')}</span>
            <span className="text-micro font-medium uppercase tracking-kicker text-muted">{t('platformPlans.limitColIncluded', 'Miqdor')}</span>
            <span className="text-micro font-medium uppercase tracking-kicker text-muted">{t('platformPlans.limitColOverage', "Qo'shimcha narx")}</span>
            <span className="text-micro font-medium uppercase tracking-kicker text-muted">{t('platformPlans.limitColMode', 'Tur / yoq')}</span>
          </div>
          <div className="space-y-2">
            {LIMIT_KEYS.map((k) => (
              <div key={k} className="grid grid-cols-1 items-center gap-2 sm:grid-cols-[1.3fr_1fr_1fr_1.1fr]">
                <span className="text-small font-medium">{LIMIT_LABEL[k].uz}</span>
                <input
                  className={inputCls + ' h-9'}
                  inputMode="numeric"
                  value={limits[k]}
                  onChange={(e) => setLimit(k, e.target.value)}
                  placeholder="∞"
                  aria-label={`${LIMIT_LABEL[k].uz} ${t('platformPlans.limitColIncluded', 'miqdor')}`}
                />
                <input
                  className={inputCls + ' h-9'}
                  inputMode="numeric"
                  value={overage[k].price || ''}
                  onChange={(e) => setOv(k, { price: Number(e.target.value.replace(/\D/g, '')) || 0 })}
                  placeholder={t('platformPlans.overagePrice', "qo'sh. narx") ?? ''}
                  aria-label={`${LIMIT_LABEL[k].uz} ${t('platformPlans.overagePrice', "qo'shimcha narx")}`}
                />
                <div className="flex items-center gap-1.5">
                  <Select
                    className="flex-1"
                    value={overage[k].mode}
                    onChange={(v) => setOv(k, { mode: v as 'soft' | 'hard' })}
                    options={[
                      { value: 'soft', label: t('platformPlans.overageSoft', 'Yumshoq') },
                      { value: 'hard', label: t('platformPlans.overageHard', 'Qatʼiy') },
                    ]}
                  />
                  <label
                    className="inline-flex items-center"
                    title={t('platformPlans.overageEnabled', "Qo'shimcha haq yoqilgan") ?? ''}
                  >
                    <Switch size="sm" checked={overage[k].enabled} onChange={(v) => setOv(k, { enabled: v })} />
                  </label>
                </div>
              </div>
            ))}
          </div>
        </FormSection>

        {/* Features */}
        <FormSection title={t('platformPlans.features', 'Imkoniyatlar')}>
          <div className="grid gap-2 sm:grid-cols-2">
            {FEATURES.map((f) => (
              <label key={f.key} className="flex min-h-[40px] cursor-pointer items-center gap-2.5 text-small">
                <Switch size="sm" checked={!!features[f.key]} onChange={() => toggleFeat(f.key)} />
                <span>{f.uz}</span>
              </label>
            ))}
          </div>
        </FormSection>

        {/* Status & visibility flags */}
        <FormSection title={t('platformPlans.secStatus', "Holat va ko'rinish")}>
          <div className="grid gap-2 sm:grid-cols-2">
            {([
              ['is_popular',      t('platformPlans.popular',      'Eng ommabop'),                  isPopular, setIsPopular],
              ['is_public',       t('platformPlans.public', "Landingda ko'rsatish"),          isPublic,  setIsPublic],
              ['is_contact_sales',t('platformPlans.contactSales', 'Kelishiladi (narx yashirin)'),   isContact, setIsContact],
              ['is_active',       t('common.active',              'Faol'),                          active,    setActive],
            ] as const).map(([key, label, val, set]) => (
              <label key={key} className="flex min-h-[40px] cursor-pointer items-center gap-3 text-small">
                <Switch checked={val} onChange={set as (b: boolean) => void} />
                <span>{label}</span>
              </label>
            ))}
          </div>
        </FormSection>

        {m.isError && (
          <div className="rounded-control border border-danger/30 bg-[color:var(--c-danger-soft)] px-4 py-3 text-body text-danger">
            {apiErrorMessage(m.error)}
          </div>
        )}
      </form>
    </Modal>
  );
}
