import { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Loader2 } from 'lucide-react';
import { Modal, Field, inputCls } from '@/components/ui/Modal';
import { Button } from '@/components/ui/Button';
import { Select } from '@/components/ui/Select';
import { usePlatformSettings } from '@/lib/queries';
import { usePutSetting } from '@/lib/mutations';
import { focusFirstInvalid, hasErrors, numOrNull } from '@/lib/forms';
import { toast } from '@/lib/feedback';

interface Props { open: boolean; onClose: () => void }

interface DemoCfg {
  trial_days?: number;
  max_active_installments?: number | null;
  max_staff?: number | null;
  max_customers?: number | null;
  on_expiry?: 'suspend' | 'block' | 'keep';
}

/** Dedicated editor for the built-in demo (trial) plan. Stored in the
 *  ``demo_plan`` platform setting — not a normal Plan row. Required field
 *  (trial_days) validates inline; Select replaces every native <select>. */
export function DemoPlanModal({ open, onClose }: Props) {
  const { t } = useTranslation();
  const q = usePlatformSettings();
  const put = usePutSetting();

  const [days,     setDays]     = useState('14');
  const [maxInst,  setMaxInst]  = useState('');
  const [maxStaff, setMaxStaff] = useState('1');
  const [maxCust,  setMaxCust]  = useState('');
  const [onExpiry, setOnExpiry] = useState<'suspend' | 'block' | 'keep'>('suspend');
  const [errors,   setErrors]   = useState<Record<string, string>>({});

  useEffect(() => {
    if (!open) return;
    const v = (q.data?.find((s) => s.key === 'demo_plan')?.value ?? {}) as DemoCfg;
    setDays(String(v.trial_days ?? 14));
    setMaxInst(v.max_active_installments != null ? String(v.max_active_installments) : '');
    setMaxStaff(v.max_staff != null ? String(v.max_staff) : '');
    setMaxCust(v.max_customers != null ? String(v.max_customers) : '');
    setOnExpiry(v.on_expiry ?? 'suspend');
    setErrors({});
  }, [open, q.data]);

  const validate = (): Record<string, string> => {
    const errs: Record<string, string> = {};
    const trialDays = Number(days.replace(/\D/g, ''));
    if (days.trim() === '' || !(trialDays >= 1))
      errs.days = t('demoPlan.errTrialDays', 'Sinov muddati kamida 1 kun boʻlishi kerak.');
    return errs;
  };

  const save = async () => {
    const errs = validate();
    setErrors(errs);
    if (hasErrors(errs)) { focusFirstInvalid(); return; }
    try {
      await put.mutateAsync({
        key: 'demo_plan',
        value: {
          trial_days: Number(days.replace(/\D/g, '')) || 14,
          max_active_installments: numOrNull(maxInst),
          max_staff:     numOrNull(maxStaff),
          max_customers: numOrNull(maxCust),
          on_expiry:     onExpiry,
        },
      });
      toast.success(t('common.saved', 'Saqlandi'));
      onClose();
    } catch (ex) {
      const e = ex as { response?: { data?: { detail?: string } } };
      toast.error(e.response?.data?.detail ?? t('common.errorLoading'));
    }
  };

  return (
    <Modal
      open={open}
      onClose={onClose}
      size="md"
      title={t('demoPlan.title', 'Demo (sinov) tarifi')}
      subtitle={t('demoPlan.sub', 'Sinov muddati, cheklovlar va tugaganidan keyingi xatti-harakat')}
      footer={
        <>
          <Button variant="ghost" size="md" onClick={onClose}>
            {t('common.cancel', 'Bekor qilish')}
          </Button>
          <Button variant="primary" size="md" onClick={save} loading={put.isPending}>
            {put.isPending && <Loader2 size={14} className="animate-spin" />}
            {t('common.save', 'Saqlash')}
          </Button>
        </>
      }
    >
      <div className="grid gap-5">
        {/* Trial duration — required */}
        <Field
          label={t('demoPlan.trialDays', 'Sinov muddati (kun)')}
          required
          error={errors.days}
          hint={t('demoPlan.trialDaysHint', 'Yangi tenant necha kun bepul foydalana oladi')}
        >
          <input
            className={inputCls}
            inputMode="numeric"
            value={days}
            onChange={(e) => { setDays(e.target.value.replace(/\D/g, '')); setErrors((p) => ({ ...p, days: '' })); }}
            onBlur={() => setErrors((p) => ({ ...p, ...validate() }))}
            placeholder="14"
            data-invalid={!!errors.days}
          />
        </Field>

        {/* Limits — all optional (blank = unlimited) */}
        <fieldset className="grid gap-3 rounded-card border border-line p-4">
          <legend className="px-1 text-micro font-medium uppercase tracking-kicker text-muted">
            {t('demoPlan.limits', 'Cheklovlar')}
          </legend>
          <p className="text-caption text-muted">{t('demoPlan.blankUnlimited', "Bo'sh qoldirish = cheksiz")}</p>
          <div className="grid gap-3 sm:grid-cols-3">
            <Field label={t('demoPlan.maxInst', 'Maks. faol shartnoma')}>
              <input
                className={inputCls}
                inputMode="numeric"
                value={maxInst}
                onChange={(e) => setMaxInst(e.target.value.replace(/\D/g, ''))}
                placeholder="∞"
              />
            </Field>
            <Field label={t('demoPlan.maxStaff', 'Maks. xodim')}>
              <input
                className={inputCls}
                inputMode="numeric"
                value={maxStaff}
                onChange={(e) => setMaxStaff(e.target.value.replace(/\D/g, ''))}
                placeholder="1"
              />
            </Field>
            <Field label={t('demoPlan.maxCust', 'Maks. mijoz')}>
              <input
                className={inputCls}
                inputMode="numeric"
                value={maxCust}
                onChange={(e) => setMaxCust(e.target.value.replace(/\D/g, ''))}
                placeholder="∞"
              />
            </Field>
          </div>
        </fieldset>

        {/* Expiry behaviour */}
        <Field
          label={t('demoPlan.onExpiry', 'Sinov tugaganda')}
          hint={t('demoPlan.onExpiryHint', 'Sinov muddati tugagach tenant hisobiga nima qilinadi')}
        >
          <Select
            value={onExpiry}
            onChange={(v) => setOnExpiry(v as typeof onExpiry)}
            options={[
              { value: 'suspend', label: t('demoPlan.suspend', "To'xtatish (faqat ko'rish)") },
              { value: 'block',   label: t('demoPlan.block',   'Bloklash (darhol)') },
              { value: 'keep',    label: t('demoPlan.keep',    'Davom ettirish') },
            ]}
          />
        </Field>
      </div>
    </Modal>
  );
}
