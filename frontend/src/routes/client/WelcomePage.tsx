import { useEffect, useState } from 'react';
import { greetingFor, WELCOME_GREETING } from '@/lib/greeting';
import { useTranslation } from 'react-i18next';
import { useNavigate } from 'react-router';
import {
  ArrowLeft, ArrowRight, Briefcase, Loader2, MessageCircle, ShoppingBag, Users,
} from 'lucide-react';
import { Button } from '@/components/ui/Button';
import { inputCls } from '@/components/ui/Modal';
import { cn } from '@/lib/cn';
import { startParamToPath } from '@/lib/telegram/deepLinks';
import { api } from '@/lib/api';
import { useSession } from '@/store/session';
import { useMySummary, useOnboardingOptions, usePublicPlans } from '@/lib/queries';
import { money } from '@/lib/format';
import { getTelegramWebApp } from '@/lib/telegram';
import { useSupportUsername } from '@/hooks/useSupportUsername';

/**
 * Shown to authenticated users who have no platform_role and no tenant
 * memberships — i.e. they came in via Telegram but they're not yet
 * connected to any shop.
 *
 * 3 cards / 3 personas:
 *   1. I want to buy on credit          → look for the shop's invite
 *   2. I run a business                 → start a 7-day demo
 *   3. I want to work as a staff member → contact support
 */
export default function ClientWelcomePage() {
  const { t } = useTranslation();
  const support = useSupportUsername();
  const me   = useSession((s) => s.me);
  const nav  = useNavigate();
  const locale = window.location.pathname.split('/')[1] === 'ru' ? 'ru' : 'uz';

  // Business onboarding wizard (shown inside the webapp instead of bouncing to
  // the marketing /demo page + bot round-trip).
  const [showBiz, setShowBiz] = useState(false);

  // If we were opened with a start_param (Telegram Mini-App deep-link),
  // forward to the invitation join page.
  const [forwarded, setForwarded] = useState(false);
  useEffect(() => {
    if (forwarded) return;
    const tg = getTelegramWebApp() as { initDataUnsafe?: { start_param?: string } } | undefined;
    const sp = tg?.initDataUnsafe?.start_param;
    // (#12) Decoded by the shared decoder rather than by a local `startsWith`
    // + `slice(4)`: that hard-coded the `inv_` prefix in a second place, so it
    // would have silently stopped forwarding the bare codes we mint now. The
    // locale is irrelevant here — `/i/:code` is a public, locale-less route —
    // so any value gives the same answer for an invite param.
    const target = startParamToPath(sp, 'uz');
    if (target?.startsWith('/i/')) {
      setForwarded(true);
      nav(target, { replace: true });
    }
  }, [nav, forwarded]);

  // If the user already has installments at some shop, skip the 3-personas
  // hub and take them straight to the home screen. The summary query is
  // cheap and the redirect happens before render so they never see a flash
  // of the placeholder cards.
  const summary = useMySummary();
  useEffect(() => {
    if (forwarded) return;
    const count = summary.data?.installments_count ?? 0;
    if (count > 0) {
      const locale = window.location.pathname.split('/')[1] === 'ru' ? 'ru' : 'uz';
      setForwarded(true);
      nav(`/${locale}/me/my`, { replace: true });
    }
  }, [summary.data, nav, forwarded]);

  if (showBiz) {
    return <BusinessWizard locale={locale} onCancel={() => setShowBiz(false)} />;
  }

  // (#379) Match the peer client pages' 440px column — the previous 640px width
  // and sm: row layouts targeted a desktop breakpoint that never fires inside
  // Telegram Mini-App frames and broke the persona-card rhythm.
  return (
    <div className="mx-auto w-full max-w-md">
      <header className="mb-8 text-center sm:text-left">
        <div className="h-page">
          {greetingFor(t, me, WELCOME_GREETING)}
        </div>
        <p className="mt-2 text-body text-ink-soft">
          {t('welcome.tagline')}
        </p>
        <div className="mt-3 text-small text-muted">{t('welcome.choose')}</div>
      </header>

      <div className="grid gap-4">
        {/* Card 1 — Customer */}
        <article className="card flex flex-col p-6">
          <span className="grid h-12 w-12 shrink-0 place-items-center rounded-2xl bg-[color:var(--c-accent-soft)] text-accent">
            <ShoppingBag size={24} />
          </span>
          <div className="mt-3 min-w-0 flex-1">
            <h3 className="text-subsection font-semibold tracking-tight">{t('welcome.customer.title')}</h3>
            <p className="mt-1 text-sm text-ink-soft">{t('welcome.customer.body')}</p>
          </div>
          <a
            href={`https://t.me/${support}`}
            target="_blank" rel="noreferrer"
            className="mt-4 inline-flex h-10 items-center justify-center gap-1 rounded-pill border border-line bg-surface px-4 text-small font-medium text-ink-soft hover:bg-ink/5"
          >
            {t('welcome.customer.cta')} <ArrowRight size={14} />
          </a>
        </article>

        {/* Card 2 — Business owner (primary CTA) */}
        <article className="rounded-card bg-ink text-white p-6 flex flex-col">
          <span className="grid h-12 w-12 shrink-0 place-items-center rounded-2xl bg-white/10 text-accent">
            <Briefcase size={24} />
          </span>
          <div className="mt-3 min-w-0 flex-1">
            <h3 className="text-subsection font-semibold tracking-tight">{t('welcome.business.title')}</h3>
            <p className="mt-1 text-sm text-white/70">{t('welcome.business.body')}</p>
          </div>
          <Button
            variant="primary"
            size="md"
            className="mt-4"
            onClick={() => setShowBiz(true)}
          >
            {t('welcome.business.cta')} <ArrowRight size={14} />
          </Button>
        </article>

        {/* Card 3 — Staff */}
        <article className="card flex flex-col p-6">
          <span className="grid h-12 w-12 shrink-0 place-items-center rounded-2xl bg-cream text-ink">
            <Users size={24} />
          </span>
          <div className="mt-3 min-w-0 flex-1">
            <h3 className="text-subsection font-semibold tracking-tight">{t('welcome.staff.title')}</h3>
            <p className="mt-1 text-sm text-ink-soft">{t('welcome.staff.body')}</p>
          </div>
          <a
            href={`https://t.me/${support}`}
            target="_blank" rel="noreferrer"
            className="mt-4 inline-flex h-10 items-center justify-center gap-1 rounded-pill border border-line bg-surface px-4 text-small font-medium text-ink-soft hover:bg-ink/5"
          >
            <MessageCircle size={14} /> {t('welcome.staff.cta')}
          </a>
        </article>
      </div>

      <p className="mt-8 text-center text-caption text-muted">
        {t('welcome.footer')}{' '}
        <a href={`https://t.me/${support}`} target="_blank" rel="noreferrer" className="text-accent hover:underline">
          @{support}
        </a>
      </p>
    </div>
  );
}

// ── Business onboarding wizard — one question per screen ──────────────────────

const RESERVED_GENERAL = 'general';
const RESERVED_OTHER = 'other';

function BusinessWizard({ locale, onCancel }: { locale: 'uz' | 'ru'; onCancel: () => void }) {
  const { t, i18n } = useTranslation();
  const lng = i18n.language?.startsWith('ru') ? 'ru' : 'uz';
  const options = useOnboardingOptions();
  const plansQ = usePublicPlans();

  const [step, setStep] = useState(0);
  const [name, setName] = useState('');
  const [bizType, setBizType] = useState('');
  const [bizTypeOther, setBizTypeOther] = useState('');
  const [plan, setPlan] = useState<string>('');
  const [howFound, setHowFound] = useState('');
  const [howFoundOther, setHowFoundOther] = useState('');
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  // Plans come from the real catalog (/plans/public), not a hardcoded list, so
  // the wizard never drifts from the DB/owner plans. Exclude contact-sales.
  const selectablePlans = (plansQ.data ?? []).filter((p) => !p.is_contact_sales);
  useEffect(() => {
    if (selectablePlans.length && !selectablePlans.some((p) => p.code === plan)) {
      setPlan((selectablePlans.find((p) => p.is_popular) ?? selectablePlans[0]).code);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [plansQ.data]);

  const STEPS = 4;
  const canNext =
    step === 0 ? name.trim().length >= 2 :
    step === 1 ? (!!bizType && (bizType !== RESERVED_OTHER || bizTypeOther.trim().length > 0)) :
    step === 2 ? true :
    (!!howFound && (howFound !== RESERVED_OTHER || howFoundOther.trim().length > 0));

  const submit = async () => {
    setBusy(true); setErr(null);
    try {
      await api.post('/auth/provision-trial', {
        business_name: name.trim(),
        plan_code: plan,
        locale,
        business_type: bizType || undefined,
        business_type_other: bizType === RESERVED_OTHER ? bizTypeOther.trim() || undefined : undefined,
        how_found: howFound || undefined,
        how_found_other: howFound === RESERVED_OTHER ? howFoundOther.trim() || undefined : undefined,
      });
      window.location.replace(`/${locale}/tenant/overview`);
    } catch (ex) {
      const e = ex as { response?: { data?: { detail?: string } } };
      setErr(e.response?.data?.detail ?? t('common.errorLoading'));
      setBusy(false);
    }
  };

  const next = () => { if (step < STEPS - 1) setStep((s) => s + 1); else void submit(); };
  const back = () => { if (step === 0) onCancel(); else setStep((s) => s - 1); };

  const bizTypes = options.data?.business_types ?? [];
  const howFoundOpts = options.data?.how_found_options ?? [];

  return (
    <div className="mx-auto w-full max-w-md">
      {/* Progress + back */}
      <div className="mb-5 flex items-center gap-3">
        <button onClick={back} aria-label={t('common.back', 'Orqaga')} className="grid h-9 w-9 shrink-0 place-items-center rounded-xl border border-line text-ink-soft hover:bg-ink/5">
          <ArrowLeft size={16} />
        </button>
        <div className="flex flex-1 gap-1.5">
          {Array.from({ length: STEPS }).map((_, i) => (
            <span key={i} className={cn('h-1.5 flex-1 rounded-full transition', i <= step ? 'bg-ink' : 'bg-line')} />
          ))}
        </div>
        <span className="shrink-0 text-caption tabular-nums text-muted">{step + 1}/{STEPS}</span>
      </div>

      <div className="card p-6">
        {/* Step 0 — name */}
        {step === 0 && (
          <Step title={t('welcome.wiz.nameTitle', 'Biznesingiz nomi nima?')}
                hint={t('welcome.wiz.nameHint', '14 kunlik bepul demo. Karta shart emas.')}>
            <input className={inputCls} value={name} maxLength={120} autoFocus
                   aria-labelledby={wizStepTitleId}
                   onKeyDown={(e) => { if (e.key === 'Enter' && canNext) next(); }}
                   onChange={(e) => setName(e.target.value)}
                   placeholder={t('welcome.business.namePh', 'Masalan: Demo Electronics')} />
          </Step>
        )}

        {/* Step 1 — business type */}
        {step === 1 && (
          <Step title={t('welcome.wiz.typeTitle', 'Qaysi sohada ishlaysiz?')}
                hint={t('welcome.wiz.typeHint', 'Shu asosda katalogni sizga moslab beramiz.')}>
            {options.isLoading ? (
              <div className="py-6 text-center text-sm text-muted"><Loader2 size={18} className="mx-auto animate-spin" /></div>
            ) : (
              <>
                <div className="grid grid-cols-2 gap-2">
                  {bizTypes.map((bt) => (
                    <TypeCard key={bt.slug} active={bizType === bt.slug} onClick={() => setBizType(bt.slug)}
                              emoji={bt.emoji} label={lng === 'ru' ? bt.name_ru : bt.name_uz} />
                  ))}
                  <TypeCard active={bizType === RESERVED_GENERAL} onClick={() => setBizType(RESERVED_GENERAL)}
                            emoji="📦" label={t('welcome.wiz.typeGeneral', 'Umumiy (hammasi)')} />
                  <TypeCard active={bizType === RESERVED_OTHER} onClick={() => setBizType(RESERVED_OTHER)}
                            emoji="✍️" label={t('welcome.wiz.typeOther', 'Boshqa')} />
                </div>
                {bizType === RESERVED_OTHER && (
                  <input className={inputCls + ' mt-3'} value={bizTypeOther} maxLength={120} autoFocus
                         aria-labelledby={wizStepTitleId}
                         onChange={(e) => setBizTypeOther(e.target.value)}
                         placeholder={t('welcome.wiz.typeOtherPh', 'Soha nomini yozing')} />
                )}
              </>
            )}
          </Step>
        )}

        {/* Step 2 — interested plan */}
        {step === 2 && (
          <Step title={t('welcome.wiz.planTitle', 'Qaysi tarif sizni qiziqtiradi?')}
                hint={t('welcome.wiz.planHint', 'Hozir tanlash shart emas — demo baribir bepul.')}>
            {plansQ.isLoading && selectablePlans.length === 0 ? (
              <div className="py-6 text-center text-sm text-muted"><Loader2 size={18} className="mx-auto animate-spin" /></div>
            ) : (
              <div className="grid grid-cols-2 gap-2">
                {selectablePlans.map((p) => (
                  <button key={p.code} type="button" onClick={() => setPlan(p.code)}
                          className={cn('flex flex-col items-center gap-0.5 rounded-xl border py-3 text-small font-semibold transition',
                            plan === p.code ? 'border-ink bg-ink text-white' : 'border-line text-ink-soft hover:bg-ink/5')}>
                    <span>{lng === 'ru' ? p.name_ru : p.name_uz}</span>
                    <span className={cn('text-micro font-normal', plan === p.code ? 'text-white/70' : 'text-muted')}>
                      {money(p.price_monthly)} {t('common.sum', "so'm")}{t('billing.monthly', '/oy')}
                    </span>
                  </button>
                ))}
              </div>
            )}
          </Step>
        )}

        {/* Step 3 — how did you find us */}
        {step === 3 && (
          <Step title={t('welcome.wiz.foundTitle', 'Bizni qanday topdingiz?')}
                hint={t('welcome.wiz.foundHint', 'Bu bizga yaxshilanishga yordam beradi.')}>
            <div className="grid gap-2">
              {howFoundOpts.map((o) => (
                <RowOption key={o.value} active={howFound === o.value} onClick={() => setHowFound(o.value)}
                           label={lng === 'ru' ? o.label_ru : o.label_uz} />
              ))}
              <RowOption active={howFound === RESERVED_OTHER} onClick={() => setHowFound(RESERVED_OTHER)}
                         label={t('welcome.wiz.foundOther', 'Boshqa')} />
            </div>
            {howFound === RESERVED_OTHER && (
              <input className={inputCls + ' mt-3'} value={howFoundOther} maxLength={120} autoFocus
                     aria-labelledby={wizStepTitleId}
                     onChange={(e) => setHowFoundOther(e.target.value)}
                     placeholder={t('welcome.wiz.foundOtherPh', 'Qayerdan?')} />
            )}
          </Step>
        )}

        {err && <div className="mt-4 rounded-xl border border-danger/30 bg-[color:var(--c-danger-soft)] px-3 py-2 text-sm text-danger">{err}</div>}

        <Button variant="primary" size="lg" className="mt-6 w-full" onClick={next} disabled={!canNext} loading={busy}>
          {step < STEPS - 1 ? t('common.next', 'Keyingisi') : t('welcome.business.formCta', 'Demoni boshlash')}
          <ArrowRight size={14} />
        </Button>
      </div>
    </div>
  );
}

/** The step's question doubles as each input's accessible label — `wizStepTitleId`
 *  is a fixed id (safe because only one Step is ever mounted at a time) that
 *  inputs reference via `aria-labelledby` instead of a redundant visible label. */
const wizStepTitleId = 'welcome-wiz-step-title';

function Step({ title, hint, children }: { title: string; hint?: string; children: React.ReactNode }) {
  return (
    <div>
      <h2 id={wizStepTitleId} className="text-section font-semibold tracking-tight">{title}</h2>
      {hint && <p className="mt-1.5 text-sm text-ink-soft">{hint}</p>}
      <div className="mt-5">{children}</div>
    </div>
  );
}

function TypeCard({ active, onClick, emoji, label }: { active: boolean; onClick: () => void; emoji?: string; label: string }) {
  return (
    <button type="button" onClick={onClick}
            className={cn('flex items-center gap-2.5 rounded-xl border p-3 text-left text-small font-medium transition',
              active ? 'border-ink bg-ink text-white' : 'border-line hover:border-ink/30')}>
      <span className="text-lg leading-none">{emoji ?? '📦'}</span>
      <span className="min-w-0 leading-tight">{label}</span>
    </button>
  );
}

function RowOption({ active, onClick, label }: { active: boolean; onClick: () => void; label: string }) {
  return (
    <button type="button" onClick={onClick}
            className={cn('flex items-center justify-between rounded-xl border px-4 py-3 text-left text-sm font-medium transition',
              active ? 'border-ink bg-ink text-white' : 'border-line hover:border-ink/30')}>
      {label}
      {active && <span className="text-micro">✓</span>}
    </button>
  );
}
