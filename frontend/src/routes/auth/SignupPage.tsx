import { useEffect, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Link, useNavigate, useParams, useSearchParams } from 'react-router';
import { AlertCircle, Loader2 } from 'lucide-react';
import { Logo } from '@/components/layout/Logo';
import { LoadingSplash } from '@/components/ui/LoadingSplash';
import { Field, inputCls } from '@/components/ui/Modal';
import { Button } from '@/components/ui/Button';
import { TelegramIcon } from '@/components/ui/TelegramIcon';
import { api, hasAuthFlag } from '@/lib/api';
import { apiErrorMessage } from '@/lib/errors';
import { isAllowedRedirectUrl } from '@/lib/url';
import { usePublicPlans } from '@/lib/queries';
import { money } from '@/lib/format';

const INTENT_KEY = 'muddato.signupIntent';

interface SignupIntent { business_name: string; locale: 'uz' | 'ru' }

/**
 * Self-service signup (Round 26).
 *
 * Two phases, one route:
 *  1. **Form** — capture the business name, stash it, then send the user
 *     through the same Telegram OIDC flow as login (oidcNext points back
 *     here).
 *  2. **Finish** — on return we have both a session token and the stashed
 *     intent, so we POST `/auth/provision-trial` to create the 14-day trial
 *     tenant and redirect into the dashboard.
 */
export default function SignupPage() {
  const { t } = useTranslation();
  const nav = useNavigate();
  const [params] = useSearchParams();
  const { locale: routeLocale } = useParams();
  const lng = (routeLocale === 'ru' ? 'ru' : 'uz') as 'uz' | 'ru';

  const [name, setName] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [nameError, setNameError] = useState<string | null>(null);
  const [phase, setPhase] = useState<'form' | 'finishing'>('form');

  // ── Fetch public plans to resolve the plan param ──
  const { data: plans = [] } = usePublicPlans();

  // ── Finish phase: we came back from OIDC with a token + a stashed intent ──
  const ranRef = useRef(false);
  useEffect(() => {
    if (ranRef.current) return;
    const raw = sessionStorage.getItem(INTENT_KEY);
    // After returning from Telegram OIDC the backend has set the HttpOnly
    // session cookie and we've recorded the authed breadcrumb; both the stashed
    // intent and the flag must be present to enter the finish phase.
    const authed = hasAuthFlag();
    if (!raw || !authed) return; // still in form phase
    ranRef.current = true;
    setPhase('finishing');
    (async () => {
      try {
        const intent = JSON.parse(raw) as SignupIntent;
        const { data } = await api.post<{ tenant_slug: string; created: boolean }>(
          '/auth/provision-trial',
          { business_name: intent.business_name, locale: intent.locale },
        );
        sessionStorage.removeItem(INTENT_KEY);
        void data;
        nav(`/${lng}/tenant/overview`, { replace: true });
      } catch (err) {
        sessionStorage.removeItem(INTENT_KEY);
        setError(apiErrorMessage(err));
        setPhase('form');
        ranRef.current = false;
      }
    })();
  }, [lng, nav, t]);

  const startingRef = useRef(false);
  const onSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (startingRef.current) return;
    const trimmed = name.trim();
    if (trimmed.length < 2) {
      setNameError(t('signup.errName', "Biznes nomini kiriting"));
      return;
    }
    setNameError(null);
    startingRef.current = true;
    try {
      setBusy(true); setError(null);
      const intent: SignupIntent = { business_name: trimmed, locale: lng };
      sessionStorage.setItem(INTENT_KEY, JSON.stringify(intent));
      // Return to this page after Telegram login → finish phase fires.
      sessionStorage.setItem('muddato.oidcNext', `/${lng}/signup`);
      const { data } = await api.post<{ authorize_url: string; state: string }>(
        '/auth/telegram/start',
      );
      // (#F-26) Refuse a server-supplied redirect to a non-allowlisted origin.
      if (!isAllowedRedirectUrl(data.authorize_url)) {
        console.error('refusing off-allowlist authorize_url', data.authorize_url);
        setError(apiErrorMessage(new Error('bad_redirect')));
        setBusy(false);
        startingRef.current = false;
        return;
      }
      window.location.href = data.authorize_url;
    } catch (err) {
      setError(apiErrorMessage(err));
      setBusy(false);
      startingRef.current = false;
    }
  };

  if (phase === 'finishing') {
    return <LoadingSplash label={t('signup.provisioning', 'Hisob tayyorlanmoqda…')} />;
  }

  const planParam = params.get('plan');
  const selectedPlan = planParam ? plans.find(p => p.code === planParam) : null;

  // Helper to get localized plan name
  const planName = selectedPlan ? (lng === 'ru' ? selectedPlan.name_ru : selectedPlan.name_uz) : null;
  const planPrice = selectedPlan?.price_monthly;

  return (
    <main className="grid min-h-screen lg:grid-cols-2">
      <section className="relative hidden bg-ink text-white lg:flex lg:flex-col lg:justify-between lg:p-12">
        <Link to={`/${lng}`} className="text-white"><Logo className="text-white" /></Link>
        <div>
          <div className="kicker text-white/60 mb-3">{t('common.appKicker')}</div>
          <h2 className="h-hero-3 font-semibold leading-[1.05] tracking-tight max-w-[480px]">
            {t('signup.brandHeadline', "14 kun bepul sinab ko'ring")}
          </h2>
          <p className="mt-4 text-white/70 max-w-[440px]">
            {t('signup.brandSub', "Karta kerak emas. Telegram bilan kiring va biznesingizni qo'shing.")}
          </p>
          {/* Social proof chips (repeated from landing page) — visible on desktop */}
          <div className="mt-10 flex flex-wrap items-center gap-x-5 gap-y-2 text-caption text-white/60">
            <span className="inline-flex items-center gap-1.5">
              ✓ {t('signup.socialProof.noCard', 'Karta kerak emas')}
            </span>
            <span className="inline-flex items-center gap-1.5">
              ✓ {t('signup.socialProof.trial', '14 kun bepul')}
            </span>
          </div>
        </div>
        <div className="text-caption text-white/40">© Muddato {new Date().getFullYear()}</div>
      </section>

      <section className="flex flex-col items-center justify-center bg-cream px-6 py-10">
        <div className="w-full max-w-[420px]">
          <Link to={`/${lng}`} className="lg:hidden mb-10 inline-block"><Logo /></Link>
          <div className="kicker mb-2">{t('common.appKicker')}</div>
          <h1 className="h-display font-semibold tracking-tight">
            {t('signup.title', "Ro'yxatdan o'tish")}
          </h1>
          <p className="mt-3 text-ink-soft">
            {t('signup.subtitle', 'Biznes nomini kiriting — keyin Telegram bilan tasdiqlaymiz.')}
          </p>

          {/* Trial reassurance — visible on mobile, restates landing's promise */}
          <div className="mt-6 lg:hidden flex flex-wrap items-center gap-x-5 gap-y-2 text-caption text-ink-soft">
            <span className="inline-flex items-center gap-1.5">
              ✓ {t('signup.socialProof.trial', '14 kun bepul')}
            </span>
            <span className="inline-flex items-center gap-1.5">
              ✓ {t('signup.socialProof.noCard', 'Karta kerak emas')}
            </span>
          </div>

          {/* Plan display: resolve plan code to name + price */}
          {planParam && (
            selectedPlan ? (
              <p className="mt-4 text-small text-accent">
                {t('signup.planPrefill', 'Tanlangan tarif')}: <b>
                  {planName}
                  {planPrice != null && (
                    <> — {money(planPrice)} {t('billing.currency', 'so\'m')}/oy</>
                  )}
                </b>
              </p>
            ) : (
              // Plan param present but not found in catalog (unlikely, but handle gracefully)
              <p className="mt-4 text-small text-accent">
                {t('signup.planPrefill', 'Tanlangan tarif')}: <b>{planParam}</b>
              </p>
            )
          )}

          <form onSubmit={onSubmit} className="mt-8">
            <Field label={t('signup.businessName', 'Biznes nomi')} error={nameError}>
              <input
                value={name}
                onChange={(e) => { setName(e.target.value); if (nameError) setNameError(null); }}
                maxLength={120}
                placeholder={t('signup.businessNamePh', "Masalan: Mobil Savdo")}
                className={inputCls}
              />
            </Field>

            <Button
              type="submit"
              variant="telegram"
              size="lg"
              block
              loading={busy}
              className="mt-6"
            >
              {busy ? (
                <><Loader2 size={18} className="animate-spin" /> {t('auth.checking', 'Tekshirilmoqda…')}</>
              ) : (
                <><TelegramIcon size={18} /> {t('signup.cta', 'Telegram bilan davom etish')}</>
              )}
            </Button>

            {error && (
              <div className="mt-6 flex items-start gap-2 rounded-xl border border-danger/30 bg-[color:var(--c-danger-soft)] px-4 py-3 text-sm text-danger text-left">
                <AlertCircle size={16} className="mt-0.5 shrink-0" />
                <div>{error}</div>
              </div>
            )}
          </form>

          <p className="mt-7 text-small text-ink-soft">
            {t('signup.haveAccount', 'Hisobingiz bormi?')}{' '}
            <Link to="/auth/login" className="text-accent hover:underline">
              {t('signup.login', 'Kirish')}
            </Link>
          </p>
          <Link to={`/${lng}`} className="mt-6 block text-sm text-ink-soft hover:text-ink">
            ← {t('auth.backHome', 'Bosh sahifa')}
          </Link>
        </div>
      </section>
    </main>
  );
}
