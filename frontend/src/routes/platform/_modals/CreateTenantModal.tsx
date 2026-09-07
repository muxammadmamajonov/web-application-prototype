import { useEffect, useMemo, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { CheckCircle2, Copy, Loader2, Share2 } from 'lucide-react';
import { Modal, Field, inputCls } from '@/components/ui/Modal';
import { Button } from '@/components/ui/Button';
import { Select } from '@/components/ui/Select';
import { PhoneInput, EmailInput } from '@/components/ui/FormFields';
import { phoneOrEmpty } from '@/lib/phone';
import { apiErrorMessage } from '@/lib/errors';
import { focusFirstInvalid } from '@/lib/forms';
import { shareViaTelegram } from '@/lib/telegram';
import { useCreateTenant } from '@/lib/mutations';
import { usePlatformPlans, useOnboardingOptions } from '@/lib/queries';
import { V2_SURFACES_ENABLED } from '@/routes/_scope';

interface Props { open: boolean; onClose: () => void; onCreated?: (id: string) => void }

export function CreateTenantModal({ open, onClose, onCreated }: Props) {
  const { t, i18n } = useTranslation();
  const lng = i18n.language === 'ru' ? 'ru' : 'uz';
  const [name, setName] = useState('');
  // Empty until the catalogue answers — never a literal. The seed here was
  // `'lite'`, a code the catalogue dropped in R36, so a shop created without
  // touching the Select was stamped with a plan no `plans` row matches: no
  // limits, no price, no subscription row. A default can only come from the
  // live catalogue (see the effect below), so it cannot outlive it.
  const [plan, setPlan] = useState<string>('');
  const plans = usePlatformPlans();
  // Owner-defined plans (R36, free-form), plus the built-in trial tier.
  //
  // Provisioning onto a trial plan is the one action that actually starts the
  // deferred trial lifecycle, so it is not offered. Existing trial tenants stay
  // readable elsewhere for legacy compatibility.
  // Memoised because the seeding effect below depends on it: a fresh array
  // every render would re-run the effect on every render.
  const planOptions = useMemo(() => [
    ...(V2_SURFACES_ENABLED ? [{ value: 'trial', label: 'TRIAL' }] : []),
    ...(plans.data ?? []).map((p) => ({ value: p.code, label: p.code.toUpperCase() })),
  ], [plans.data]);
  // Seed the selection from the catalogue once it arrives, so the form opens on
  // a plan that exists. Only while untouched — re-seeding after that would undo
  // an operator's choice on a background refetch.
  useEffect(() => {
    if (!plan && planOptions.length > 0) setPlan(planOptions[0].value);
  }, [plan, planOptions]);

  // Canonical onboarding business types (single select — the store's own type).
  const onboarding = useOnboardingOptions();
  const [bizType, setBizType] = useState('');
  const bizOptions = [
    { value: '', label: t('platform.createTenant.bizTypeNone', 'Tanlanmagan') },
    ...(onboarding.data?.business_types ?? []).map((b) => ({
      value: b.slug,
      label: `${b.emoji ?? '📦'} ${lng === 'ru' ? b.name_ru : b.name_uz}`,
    })),
  ];
  const [phone, setPhone] = useState('');
  const [email, setEmail] = useState('');
  const [nameErr, setNameErr] = useState<string | null>(null);
  const [created, setCreated] = useState<{ name: string; link: string } | null>(null);
  const [copied, setCopied] = useState(false);
  const m = useCreateTenant();

  const reset = () => { setName(''); setPhone(''); setEmail(''); setPlan(''); setBizType(''); setNameErr(null); setCreated(null); setCopied(false); };
  const close = () => { reset(); onClose(); };

  const validateName = (v: string): string | null =>
    v.trim().length === 0 ? t('platform.createTenant.nameRequired', 'Biznes nomini kiriting')
    : v.trim().length < 2 ? t('platform.createTenant.nameTooShort', 'Kamida 2 ta belgi kiriting')
    : null;

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    // Inline validation — never let a bad value reach the API / generic error screen.
    const err = validateName(name);
    setNameErr(err);
    if (err) { focusFirstInvalid(); return; }
    const r = await m.mutateAsync({
      name,
      plan_code: plan,
      business_type: bizType || undefined,
      contact_phone: phoneOrEmpty(phone) || undefined,
      billing_email: email.trim() || undefined,
    }) as { id: string; share_url: string };
    onCreated?.(r.id);
    // Stay open and surface the owner-claim link immediately — a created tenant
    // is useless until the owner gets the link, so don't make it a second step.
    setCreated({ name, link: r.share_url });
  };

  const copy = async () => {
    if (!created) return;
    await navigator.clipboard.writeText(created.link);
    setCopied(true); setTimeout(() => setCopied(false), 1600);
  };
  const share = () => {
    if (!created) return;
    if (!shareViaTelegram(created.link)) void copy();
  };

  // ── Success: show the owner invite link ───────────────────────────────────
  if (created) {
    return (
      <Modal open={open} onClose={close} title={t('platform.createTenant.createdTitle')}
             subtitle={t('platform.createTenant.createdSubtitle', { name: created.name })}
             footer={<Button variant="primary" onClick={close}>{t('platform.createTenant.done')}</Button>}>
        <div className="space-y-4">
          <div className="flex items-center gap-2 rounded-control border border-success/30 bg-success-soft px-4 py-3 text-body text-success">
            <CheckCircle2 size={16} /> {t('platform.createTenant.businessCreated')}
          </div>
          <Field label={t('platform.createTenant.ownerLink')} hint={t('platform.createTenant.ownerLinkHint')}>
            <div className="flex gap-2">
              <input value={created.link} readOnly className={inputCls + ' font-mono text-caption'} />
              <button type="button" onClick={copy} aria-label={t('common.copy', 'Nusxalash')}
                      className="inline-flex h-11 w-11 shrink-0 items-center justify-center rounded-control border border-line bg-cream hover:border-ink/30">
                {copied ? <CheckCircle2 size={16} className="text-success" /> : <Copy size={16} />}
              </button>
              <Button variant="primary" size="md" onClick={share}><Share2 size={14} /> {t('platform.createTenant.share')}</Button>
            </div>
          </Field>
        </div>
      </Modal>
    );
  }

  return (
    <Modal
      open={open}
      onClose={close}
      title={t('platform.createTenant.newTitle')}
      subtitle={t('platform.createTenant.newSubtitle')}
      footer={
        <>
          <Button variant="ghost" onClick={close} type="button">{t('common.cancel')}</Button>
          <Button
            variant="primary"
            type="submit"
            form="create-tenant-form"
            disabled={m.isPending || name.length < 2}
          >
            {m.isPending ? (<><Loader2 size={16} className="animate-spin" /> {t('platform.createTenant.creating')}</>) : t('platform.createTenant.create')}
          </Button>
        </>
      }
    >
      <form id="create-tenant-form" onSubmit={submit} className="flex flex-col gap-4">
        <Field label={t('platform.createTenant.name')} required error={nameErr}>
          <input
            required minLength={2} maxLength={120}
            value={name}
            onChange={(e) => { setName(e.target.value); if (nameErr) setNameErr(validateName(e.target.value)); }}
            onBlur={(e) => setNameErr(validateName(e.target.value))}
            className={inputCls} placeholder="Demo Electronics"
          />
        </Field>

        <Field label={t('platform.createTenant.plan')} required>
          <Select value={plan} onChange={setPlan} options={planOptions} />
        </Field>

        {/* The business-type taxonomy belongs to the deferred onboarding
            capability — creating a tenant needs a name and a plan. */}
        {V2_SURFACES_ENABLED && (
        <Field label={t('platform.createTenant.businessType', 'Biznes turi')} hint={t('platform.createTenant.optional')}>
          <Select value={bizType} onChange={setBizType} options={bizOptions}
                  placeholder={t('platform.createTenant.businessTypePh', 'Biznes turini tanlang')} />
        </Field>
        )}

        <Field label={t('platform.createTenant.phone')} hint={t('platform.createTenant.optional')}>
          <PhoneInput value={phone} onChange={setPhone} />
        </Field>

        <Field label={t('platform.createTenant.billingEmail')} hint={t('platform.createTenant.optional')}>
          <EmailInput value={email} onChange={setEmail} placeholder="billing@gmail.com" />
        </Field>

        {m.isError && (
          <div className="rounded-control border border-danger/30 bg-[color:var(--c-danger-soft)] px-4 py-3 text-body text-danger">
            {t('platform.createTenant.createFailed')} · {apiErrorMessage(m.error)}
          </div>
        )}
      </form>
    </Modal>
  );
}
