import { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Loader2 } from 'lucide-react';
import { Modal, Field, inputCls, inputErrCls } from '@/components/ui/Modal';
import { Switch } from '@/components/ui/Switch';
import { Select } from '@/components/ui/Select';
import { Button } from '@/components/ui/Button';
import { useSetPlanOverrides } from '@/lib/mutations';
import { apiErrorMessage } from '@/lib/errors';
import type { OverageRule, TenantDetail } from '@/lib/queries';
import { LIMIT_KEYS, LIMIT_LABEL, type LimitKey } from '@/lib/planFeatures';
import { focusFirstInvalid, hasErrors, numOrNull } from '@/lib/forms';
import { toast } from '@/lib/feedback';

interface Props { tenant: TenantDetail | null; onClose: () => void }

const emptyOv = (): OverageRule => ({ price: 0, enabled: false, mode: 'hard' });

/** Per-customer plan overrides (R39): custom subscription price, included
 *  limits, and overage prices for one business. Empty = inherit the plan. */
export function PlanOverridesModal({ tenant, onClose }: Props) {
  const { t } = useTranslation();
  const save = useSetPlanOverrides();
  const [price, setPrice] = useState('');
  const [limits, setLimits] = useState<Record<LimitKey, string>>({ contract: '', staff: '', branch: '', custom_field: '', broadcast: '' });
  const [overage, setOverage] = useState<Record<LimitKey, OverageRule>>({
    contract: emptyOv(), staff: emptyOv(), branch: emptyOv(), custom_field: emptyOv(), broadcast: emptyOv(),
  });
  const [errors, setErrors] = useState<Partial<Record<LimitKey, string>>>({});

  useEffect(() => {
    if (!tenant) return;
    const ov = tenant.plan_overrides ?? {};
    setPrice(ov.price_monthly != null ? String(ov.price_monthly) : '');
    const lim = {} as Record<LimitKey, string>;
    for (const k of LIMIT_KEYS) { const v = ov.limits?.[k]; lim[k] = v == null ? '' : String(v); }
    setLimits(lim);
    const o = {} as Record<LimitKey, OverageRule>;
    for (const k of LIMIT_KEYS) o[k] = { ...emptyOv(), ...(ov.overage?.[k] ?? {}) };
    setOverage(o);
    setErrors({});
  }, [tenant]);

  if (!tenant) return null;

  const submit = async () => {
    // An enabled overage rule must carry a price — otherwise it bills at 0.
    const errs: Partial<Record<LimitKey, string>> = {};
    for (const k of LIMIT_KEYS) {
      if (overage[k].enabled && !(overage[k].price > 0))
        errs[k] = t('platformPlans.errOveragePrice', 'Qoʻshimcha haq yoqilgan — narxini (0 dan katta) kiriting.');
    }
    setErrors(errs);
    if (hasErrors(errs)) { focusFirstInvalid(); return; }
    // Only send keys the operator actually set; empty values clear the override.
    const limitsBody: Record<string, number | null> = {};
    for (const k of LIMIT_KEYS) if (limits[k].trim() !== '') limitsBody[k] = Number(limits[k].replace(/\D/g, ''));
    const overageBody: Record<string, OverageRule> = {};
    for (const k of LIMIT_KEYS) if (overage[k].price || overage[k].enabled) overageBody[k] = overage[k];
    try {
      await save.mutateAsync({ id: tenant.id, body: {
        price_monthly: numOrNull(price),
        limits: Object.keys(limitsBody).length ? limitsBody : undefined,
        overage: Object.keys(overageBody).length ? overageBody : undefined,
      } });
      toast.success(t('common.saved', 'Saqlandi'));
      onClose();
    } catch (ex) {
      toast.error(apiErrorMessage(ex));
    }
  };

  return (
    <Modal open={!!tenant} onClose={onClose} size="lg"
           title={t('platformTenants.customPlan', 'Maxsus shartlar')}
           subtitle={tenant.name}
           footer={<>
             <Button variant="outline" size="md" onClick={onClose}>{t('common.cancel', 'Bekor qilish')}</Button>
             <Button variant="primary" size="md" onClick={submit} loading={save.isPending}>
               {save.isPending && <Loader2 size={14} className="animate-spin" />} {t('common.save', 'Saqlash')}
             </Button>
           </>}>
      <div className="grid gap-4">
      {/* An override replaces a value that came from a plan, so name the plan —
          otherwise the operator cannot tell what they are departing from — and
          say that a blank field inherits. */}
      <p className="mb-3 text-caption text-muted">
        {t('platformTenants.overridesBase', {
          plan: (tenant.plan_code ?? '').toUpperCase() || '—',
          defaultValue: '“{{plan}}” tarifi qiymatlari ustidan yoziladi. Bo‘sh qoldirilgan maydon tarifdan meros oladi.',
        })}
      </p>
        <Field label={t('platformTenants.customPrice', 'Maxsus oylik narx')} hint={t('platformTenants.blankInherit', 'Bo‘sh = tarif narxi')}>
          <input className={inputCls} inputMode="numeric" value={price}
                 onChange={(e) => setPrice(e.target.value.replace(/\D/g, ''))} placeholder="—" />
        </Field>
        <div className="rounded-card border border-line p-3">
          <div className="mb-2 text-body font-semibold">{t('platformPlans.limitsOverage', 'Limitlar va qoʻshimcha haq')}</div>
          <div className="space-y-2">
            {LIMIT_KEYS.map((k) => (
              <div key={k}>
                <div className="grid grid-cols-1 items-center gap-2 sm:grid-cols-[1.3fr_1fr_1fr_1.1fr]">
                  <span className="text-small font-medium">{LIMIT_LABEL[k].uz}</span>
                  <input className={inputCls + ' h-9'} inputMode="numeric" value={limits[k]}
                         onChange={(e) => setLimits((p) => ({ ...p, [k]: e.target.value.replace(/\D/g, '') }))}
                         placeholder={t('platformTenants.inherit', 'tarif') ?? ''} />
                  <input className={inputCls + ' h-9' + (errors[k] ? ' ' + inputErrCls : '')} inputMode="numeric"
                         aria-invalid={errors[k] ? 'true' : undefined}
                         value={overage[k].price || ''}
                         onChange={(e) => { setOverage((p) => ({ ...p, [k]: { ...p[k], price: Number(e.target.value.replace(/\D/g, '')) || 0 } })); setErrors((p) => ({ ...p, [k]: '' })); }}
                         placeholder={t('platformPlans.overagePh', 'qoʻshimcha haq')} />
                  <div className="flex items-center gap-1.5">
                    <Select className="flex-1" value={overage[k].mode} onChange={(v) => setOverage((p) => ({ ...p, [k]: { ...p[k], mode: v as 'soft' | 'hard' } }))}
                            options={[{ value: 'soft', label: t('platformPlans.overageSoft', 'Yumshoq') }, { value: 'hard', label: t('platformPlans.overageHard', 'Qatʼiy') }]} />
                    <Switch size="sm" checked={overage[k].enabled}
                            onChange={(v) => { setOverage((p) => ({ ...p, [k]: { ...p[k], enabled: v } })); setErrors((p) => ({ ...p, [k]: '' })); }} />
                  </div>
                </div>
                {errors[k] && <span role="alert" className="mt-1 block text-micro font-medium text-danger">{errors[k]}</span>}
              </div>
            ))}
          </div>
        </div>
      </div>
    </Modal>
  );
}
