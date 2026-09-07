import { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Loader2 } from 'lucide-react';
import { Modal, Field, FormSection, inputCls } from '@/components/ui/Modal';
import { Button } from '@/components/ui/Button';
import { MoneyInput } from '@/components/ui/FormFields';
import { useCreatePlan } from '@/lib/mutations';
import { apiErrorMessage, errorCode } from '@/lib/errors';
import { focusFirstInvalid, hasErrors } from '@/lib/forms';
import { toast } from '@/lib/feedback';

interface Props { open: boolean; onClose: () => void }

/** Create an owner-defined plan with a free-form code (R36). The backend caps
 *  the total at 5 and reserves "trial". Price uses MoneyInput (groups thousands,
 *  never accepts garbage). */
export function CreatePlanModal({ open, onClose }: Props) {
  const { t } = useTranslation();
  const [code, setCode] = useState('');
  const [nameUz, setNameUz] = useState('');
  const [nameRu, setNameRu] = useState('');
  const [price, setPrice] = useState(0);
  const [errors, setErrors] = useState<Record<string, string>>({});
  const m = useCreatePlan();

  useEffect(() => {
    if (open) { setCode(''); setNameUz(''); setNameRu(''); setPrice(0); setErrors({}); }
  }, [open]);

  // Keep the code a safe slug: lowercase letters/digits/hyphen only.
  const onCode = (v: string) =>
    setCode(v.toLowerCase().replace(/[^a-z0-9-]/g, '').slice(0, 32));

  const validate = (): Record<string, string> => {
    const errs: Record<string, string> = {};
    if (!code.trim())    errs.code    = t('platformPlans.errCode',    'Tarif kodini kiriting (masalan: lite).');
    if (!nameUz.trim())  errs.nameUz  = t('platformPlans.errNameUz',  'Tarif nomini (uz) kiriting.');
    if (!nameRu.trim())  errs.nameRu  = t('platformPlans.errNameRu',  'Tarif nomini (ru) kiriting.');
    if (!Number.isFinite(price) || price < 0)
      errs.price = t('platformPlans.errPrice', 'Oylik narx 0 yoki undan katta son boʻlishi kerak.');
    return errs;
  };

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    const errs = validate();
    setErrors(errs);
    if (hasErrors(errs)) { focusFirstInvalid(); return; }
    try {
      await m.mutateAsync({ code, name_uz: nameUz, name_ru: nameRu, price_monthly: price });
      onClose();
    } catch (ex) {
      // Branch on the machine CODE. `apiErrorMessage` returns a translated
      // sentence, so comparing it against a snake_case literal was never true
      // and every one of these cases fell through to the generic message.
      const code = errorCode(ex);
      toast.error(
        code === 'platform_plan_limit_reached' ? t('platformPlans.limitReached', "Eng ko'pi 5 ta tarif yaratish mumkin.")
        : code === 'plan_exists'    ? t('platformPlans.exists',   'Bunday kod bilan tarif allaqachon mavjud.')
        : code === 'reserved_code'  ? t('platformPlans.reserved', "\"trial\" — tizim kodi, uni yaratib bo'lmaydi.")
        : apiErrorMessage(ex),
      );
    }
  };

  return (
    <Modal
      open={open}
      onClose={onClose}
      title={t('platformPlans.newTitle', 'Yangi tarif')}
      subtitle={t('platformPlans.newSub', 'Kod, nom va oylik narxni kiriting')}
      size="md"
      footer={
        <>
          <Button variant="ghost" size="md" type="button" onClick={onClose}>
            {t('common.cancel', 'Bekor qilish')}
          </Button>
          <Button
            variant="primary"
            size="md"
            type="submit"
            form="create-plan-form"
            loading={m.isPending}
          >
            {m.isPending && <Loader2 size={14} className="animate-spin" />}
            {t('common.create', 'Yaratish')}
          </Button>
        </>
      }
    >
      <form id="create-plan-form" onSubmit={submit} className="grid gap-5">
        {/* Identification — code + names */}
        <FormSection
          title={t('platformPlans.secBasic', 'Asosiy')}
          hint={t('platformPlans.secBasicHint', "Kod bir marta belgilanadi va keyinchalik o'zgartirilmaydi.")}
        >
          <Field
            label={t('platformPlans.code', 'Kod')}
            required
            error={errors.code}
            hint={t('platformPlans.codeHint', 'masalan: lite, pro, biznes — kichik harf/raqam/chiziqcha')}
          >
            <input
              value={code}
              onChange={(e) => { onCode(e.target.value); setErrors((p) => ({ ...p, code: '' })); }}
              className={inputCls}
              placeholder="lite"
              autoCapitalize="none"
              autoCorrect="off"
              spellCheck={false}
              data-invalid={!!errors.code}
            />
          </Field>

          <div className="grid gap-4 sm:grid-cols-2">
            <Field label={t('platformPlans.nameUz', 'Nomi (uz)')} required error={errors.nameUz}>
              <input
                value={nameUz}
                onChange={(e) => { setNameUz(e.target.value); setErrors((p) => ({ ...p, nameUz: '' })); }}
                onBlur={(e) => { if (!e.target.value.trim()) setErrors((p) => ({ ...p, nameUz: t('platformPlans.errNameUz', 'Tarif nomini (uz) kiriting.') })); }}
                className={inputCls}
                placeholder={t('platformPlans.nameUzPh', 'Masalan: Lite')}
                data-invalid={!!errors.nameUz}
              />
            </Field>
            <Field label={t('platformPlans.nameRu', 'Nomi (ru)')} required error={errors.nameRu}>
              <input
                value={nameRu}
                onChange={(e) => { setNameRu(e.target.value); setErrors((p) => ({ ...p, nameRu: '' })); }}
                onBlur={(e) => { if (!e.target.value.trim()) setErrors((p) => ({ ...p, nameRu: t('platformPlans.errNameRu', 'Tarif nomini (ru) kiriting.') })); }}
                className={inputCls}
                placeholder={t('platformPlans.nameRuPh', 'Например: Лайт')}
                data-invalid={!!errors.nameRu}
              />
            </Field>
          </div>
        </FormSection>

        {/* Pricing */}
        <FormSection title={t('platformPlans.secPricing', 'Narx')}>
          <Field
            label={t('platformPlans.priceMonthly', 'Oylik narx')}
            required
            error={errors.price}
            hint={t('platformPlans.priceHint', "0 = bepul yoki \"kelishiladi\" deb belgilash mumkin")}
          >
            <MoneyInput
              value={price}
              onChange={(v) => { setPrice(v); setErrors((p) => ({ ...p, price: '' })); }}
              placeholder={t('platformPlans.pricePh', "Oylik to'lov miqdori")}
            />
          </Field>
        </FormSection>

        <p className="text-caption text-muted">
          {t('platformPlans.editLater', 'Limitlar, imkoniyatlar va marketing belgisini yaratilgandan keyin tahrirlashingiz mumkin.')}
        </p>

        {m.isError && (
          <div className="rounded-control border border-danger/30 bg-[color:var(--c-danger-soft)] px-4 py-3 text-body text-danger">
            {apiErrorMessage(m.error)}
          </div>
        )}
      </form>
    </Modal>
  );
}
