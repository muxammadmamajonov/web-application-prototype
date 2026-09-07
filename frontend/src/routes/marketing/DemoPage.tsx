/**
 * Demo request — a lead-capture form.
 *
 * The form is retained (MKT-004); the self-serve provisioning it used to
 * trigger is LATER. It used to POST the form, receive a
 * `t.me/…?start=demo_<slug>` deep link and `window.open` it, so the visitor
 * landed in a bot that had already created their shop. The submission now goes
 * to the Muddato support channel and a human follows up, so there is no link to
 * return and nothing to open.
 */
import { useState, useEffect, useMemo } from 'react';
import { useParams, useSearchParams } from 'react-router';
import { useTranslation } from 'react-i18next';
import { ArrowRight, Check, Loader2, Send } from 'lucide-react';
import { Container } from '@/components/ui/Container';
import { Button } from '@/components/ui/Button';
import { Field, inputCls } from '@/components/ui/Modal';
import { Select } from '@/components/ui/Select';
import { PublicShell } from '@/components/marketing/PublicLayout';
import { SmartContact, isValidPhone, isValidEmail } from '@/components/ui/SmartContact';
import { focusFirstInvalid, hasErrors } from '@/lib/forms';
import { api } from '@/lib/api';
import { apiErrorMessage } from '@/lib/errors';
import { usePublicPlans } from '@/lib/queries';

export default function DemoPage() {
  const { i18n } = useTranslation();
  const { locale } = useParams<{ locale?: string }>();
  const lng = (locale ?? i18n.language ?? 'uz') as 'uz' | 'ru';
  const [params] = useSearchParams();
  const { t } = useTranslation('translation', { lng, keyPrefix: 'demo' });

  // The visitor DOES pick a plan — it is the tier they want to be shown, and it
  // is what sales opens the conversation on. The catalog is read live so the
  // options are the real ones; picking a tier here still buys nothing and
  // provisions nothing.
  const { data: allPlans } = usePublicPlans();

  const [form, setForm] = useState({
    businessName: '',
    ownerName: '',
    contact: '+998 ',
    plan: params.get('plan') ?? '',
    consent: false,
  });

  // Selectable plans — contact-sales tiers are not something a visitor can ask
  // to demo, so they never appear as an option.
  const selectablePlans = useMemo(
    () => (allPlans ?? []).filter((p) => !p.is_contact_sales),
    [allPlans],
  );

  // (#18) A `?plan=` that names a real self-serve plan is honoured; anything
  // else is CLEARED, not substituted.
  //
  // This used to fall back to the popular plan whenever the code was absent or
  // stale — so a visitor who opened the general demo form, or followed a link to
  // a tier that had since been renamed, silently submitted a plan they never
  // chose. Sales then called them about the wrong tier, and the lead record said
  // it was their pick. A blank field the visitor must fill is worth more than a
  // confident wrong answer.
  useEffect(() => {
    if (!selectablePlans.length) return;
    setForm((s) => (selectablePlans.some((p) => p.code === s.plan) ? s : { ...s, plan: '' }));
  }, [selectablePlans]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);          // server/submit error banner
  const [errors, setErrors] = useState<Record<string, string>>({}); // per-field client validation
  const [submitted, setSubmitted] = useState(false);

  const update = <K extends keyof typeof form>(k: K, v: (typeof form)[K]) => {
    setForm((s) => ({ ...s, [k]: v }));
    // Clear a field's error as soon as the user edits it.
    setErrors((e) => (e[k as string] ? { ...e, [k as string]: '' } : e));
  };

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (busy) return;
    // Field-level validation: collect every problem, show each under its field,
    // focus the first invalid one — never a single vague banner. The contact may
    // be a phone OR an email (the field accepts both), so accept either.
    const errs: Record<string, string> = {};
    if (form.businessName.trim().length < 2) errs.businessName = t('errBusiness');
    if (form.ownerName.trim().length < 2) errs.ownerName = t('errOwner');
    if (!(isValidPhone(form.contact) || isValidEmail(form.contact))) errs.contact = t('errContact');
    // (#18) Required, and never defaulted. Only enforced once the catalog has
    // loaded — blocking on a field whose options have not arrived would strand
    // the visitor on a slow connection with an error they cannot clear.
    if (selectablePlans.length > 0 && !form.plan) errs.plan = t('errPlan');
    if (!form.consent) errs.consent = t('consentRequired');
    setErrors(errs);
    if (hasErrors(errs)) { focusFirstInvalid(); return; }
    setBusy(true); setError(null);
    try {
      await api.post('/demo/intent', {
        business_name: form.businessName,
        owner_name: form.ownerName,
        // `contact`, not `phone`: the field above accepts either a number or an
        // email, and the server types it the same way. Posting it as `phone`
        // made every email lead a 422 the visitor never saw.
        contact: form.contact,
        plan_code: form.plan,
        locale: lng,
        referrer: document.referrer || null,
      });
      setSubmitted(true);
    } catch (err) {
      setError(apiErrorMessage(err));   // localized; never raw backend text
    } finally {
      setBusy(false);
    }
  };

  return (
    <PublicShell>
      <section className="border-b border-line section-hero">
        <Container>
          <div className="kicker text-accent">{t('kicker')}</div>
          <h1 className="mt-2 h-hero-3 font-semibold tracking-tight leading-[1.05]">
            {t('title')}
          </h1>
          <p className="mt-3 max-w-lead text-ink-soft text-subsection">{t('sub')}</p>
        </Container>
      </section>

      <section className="section-band">
        <Container>
          {submitted ? (
            <SuccessPanel lng={lng} />
          ) : (
            <div className="mx-auto w-full max-w-xl">
              <form onSubmit={submit} className="card flex flex-col gap-5 p-6 sm:p-8">
                <Field label={t('fields.businessName')} required error={errors.businessName}>
                  <input
                    maxLength={120}
                    value={form.businessName}
                    onChange={(e) => update('businessName', e.target.value)}
                    className={inputCls}
                    placeholder="Demo Electronics"
                  />
                </Field>

                <Field label={t('fields.ownerName')} required error={errors.ownerName}>
                  <input
                    maxLength={80}
                    value={form.ownerName}
                    onChange={(e) => update('ownerName', e.target.value)}
                    className={inputCls}
                    placeholder="Sardor Mamatov"
                  />
                </Field>

                <div>
                  <SmartContact
                    required
                    label={t('fields.contact')}
                    value={form.contact}
                    onChange={(v) => update('contact', v)}
                    error={!!errors.contact}
                  />
                  {errors.contact && (
                    <span role="alert" className="mt-1 block text-micro font-medium text-danger">{errors.contact}</span>
                  )}
                </div>

                {/* (#18) Always visible, never pre-filled with a guess. Arriving
                    from a pricing card preselects that tier; opening the form
                    directly leaves it empty and required, so the plan on the lead
                    is always one the visitor actually picked. */}
                {selectablePlans.length > 0 && (
                  <Field label={t('fields.plan')} required error={errors.plan}>
                    <Select
                      value={form.plan}
                      onChange={(v) => update('plan', v)}
                      placeholder={t('fields.planPlaceholder')}
                      options={selectablePlans.map((p) => ({
                        value: p.code,
                        label: lng === 'ru' ? p.name_ru : p.name_uz,
                      }))}
                    />
                  </Field>
                )}

                <div>
                  <label className="mt-2 flex items-start gap-3 text-body text-ink-soft">
                    <input
                      type="checkbox"
                      checked={form.consent}
                      onChange={(e) => update('consent', e.target.checked)}
                      aria-invalid={errors.consent ? 'true' : undefined}
                      className="mt-1 h-4 w-4 rounded-micro border-line text-accent focus:ring-accent"
                    />
                    <span>{t('consent')}</span>
                  </label>
                  {errors.consent && (
                    <span role="alert" className="mt-1 block text-micro font-medium text-danger">{errors.consent}</span>
                  )}
                </div>

                {error && (
                  <div role="alert" className="rounded-control border border-danger/30 bg-[color:var(--c-danger-soft)] px-4 py-3 text-body text-danger">
                    {error}
                  </div>
                )}

                <Button
                  type="submit"
                  variant="primary"
                  size="lg"
                  loading={busy}
                >
                  {busy
                    ? (<><Loader2 size={18} className="animate-spin" /> {t('submitting')}</>)
                    : (<><Send size={18} /> {t('submit')} <ArrowRight size={16} /></>)}
                </Button>

                <div className="text-caption text-muted">
                  {t('disclaimer')}
                </div>
              </form>
            </div>
          )}
        </Container>
      </section>
    </PublicShell>
  );
}

/** Confirms receipt. It promises follow-up — never an already-open shop. */
function SuccessPanel({ lng }: { lng: 'uz' | 'ru' }) {
  const { t } = useTranslation('translation', { lng, keyPrefix: 'demo' });
  return (
    <div role="status" className="card mx-auto max-w-xl p-8 sm:p-10 text-center">
      <div className="mx-auto grid h-14 w-14 place-items-center rounded-pill bg-[color:var(--c-success-soft)] text-success">
        <Check size={28} />
      </div>
      <h2 className="mt-5 text-page-title font-semibold tracking-tight">{t('success.title')}</h2>
      <p className="mt-3 text-ink-soft">{t('success.body')}</p>
      <p className="mt-6 text-caption text-muted">{t('success.hint')}</p>
    </div>
  );
}
