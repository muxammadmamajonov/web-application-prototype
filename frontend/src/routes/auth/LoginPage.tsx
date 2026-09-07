import { useEffect, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Link, useSearchParams } from 'react-router';
import { AlertCircle, Loader2 } from 'lucide-react';
import { Logo } from '@/components/layout/Logo';
import { LoadingSplash } from '@/components/ui/LoadingSplash';
import { Button } from '@/components/ui/Button';
import { TelegramIcon } from '@/components/ui/TelegramIcon';
import {
  BOT_USERNAME, isInsideTelegramClient, botDeepLink,
  getTelegramWebApp,
} from '@/lib/telegram';
import { botMiniAppLink, startParam } from '@/lib/telegram/deepLinks';
import { loginViaWebAppInitData } from '@/hooks/useMe';
import { useSupportUsername } from '@/hooks/useSupportUsername';
import { api } from '@/lib/api';
import { safeInternalPath, isAllowedRedirectUrl } from '@/lib/url';
import { apiErrorMessage } from '@/lib/errors';

/**
 * Login page — two flows:
 *
 * 1. **Inside Telegram Mini-App** (initData present):
 *    auto-login via `/auth/telegram/webapp`, never shows a button.
 *
 * 2. **Browser** (no initData):
 *    Renders a brand-styled "Telegram bilan kirish" button. Click hits
 *    `POST /auth/telegram/start` to mint an OIDC state, then
 *    `window.location.href = authorize_url` to Telegram. Telegram redirects
 *    back to `/auth/callback?code=&state=` which `TelegramCallbackPage`
 *    finishes.
 *
 * The legacy `telegram-widget.js` script with `data-telegram-login` is gone —
 * its embed redirected through `oauth.telegram.org/auth?embed=1` which shows
 * a "deprecated" warning unless the bot also has a domain registered via
 * @BotFather's separate /setdomain command. OIDC has its own credentials
 * (client_id + client_secret) registered via @BotFather → Web Login and is
 * the path Telegram is actively maintaining.
 */
export default function LoginPage() {
  const { t, i18n } = useTranslation();
  const support = useSupportUsername();
  const locale = i18n.resolvedLanguage === 'ru' ? 'ru' : 'uz';
  const [params] = useSearchParams();
  const next = safeInternalPath(params.get('next'));
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const insideTg = isInsideTelegramClient();

  // ── Mini-App auto-login (when launched inside Telegram) ─────────────────
  useEffect(() => {
    if (!insideTg) return;
    let cancelled = false;
    const wa = getTelegramWebApp() ?? window.Telegram?.WebApp;
    type WaExt = {
      ready?: () => void; expand?: () => void; requestFullscreen?: () => void;
      setHeaderColor?: (c: string) => void; setBackgroundColor?: (c: string) => void;
      disableVerticalSwipes?: () => void;
    };
    const tg = wa as unknown as WaExt | undefined;
    try {
      tg?.ready?.(); tg?.expand?.(); tg?.requestFullscreen?.();
      tg?.setHeaderColor?.('#f7f4ef'); tg?.setBackgroundColor?.('#f7f4ef');
      tg?.disableVerticalSwipes?.();
    } catch { /* ignore */ }
    setBusy(true);
    (async () => {
      const r = await loginViaWebAppInitData();
      if (cancelled) return;
      if (r.status === 'logged-in') location.replace(next);
      // The outcome carries the error, not a message: it is localized here
      // through the shared vocabulary, so no backend prose or Axios transport
      // string can reach the screen.
      else if (r.status === 'failed') { setError(apiErrorMessage(r.error)); setBusy(false); }
      else { setError(t('auth.errInitData')); setBusy(false); }
    })();
    return () => { cancelled = true; };
  }, [insideTg, next, t]);

  // ── Browser OIDC login click ────────────────────────────────────────────
  // One-shot ref — defends against React firing the click twice (StrictMode
  // double-invoke, fast-double-tap on touch devices, accessibility tools
  // synthesising events). The user reported seeing the Telegram consent
  // prompt twice per click; this guard makes a second `/auth/telegram/start`
  // a no-op, so only one PKCE state is minted per intent.
  const startingRef = useRef(false);
  const onTelegramClick = async () => {
    if (startingRef.current) return;
    startingRef.current = true;
    try {
      setBusy(true); setError(null);
      // Stash where the user wanted to go BEFORE leaving the page.
      sessionStorage.setItem('muddato.oidcNext', next);
      const { data } = await api.post<{ authorize_url: string; state: string }>(
        '/auth/telegram/start',
      );
      // (#F-26) Only follow a server-supplied redirect to an allowlisted origin
      // (oauth.telegram.org / our own). Refuse anything else rather than trust
      // the backend blindly.
      if (!isAllowedRedirectUrl(data.authorize_url)) {
        console.error('refusing off-allowlist authorize_url', data.authorize_url);
        setError(t('auth.errSign'));
        setBusy(false);
        startingRef.current = false;
        return;
      }
      window.location.href = data.authorize_url;
    } catch (err) {
      setError(apiErrorMessage(err));
      setBusy(false);
      startingRef.current = false; // allow retry after a real failure
    }
  };

  // Inside the Telegram Mini-App we NEVER render the login UI — the user came
  // in already authenticated via initData. Show the branded splash for the
  // whole bootstrap, including any retry-or-error display.
  if (insideTg) {
    if (error) {
      // initData failed — show a compact error on top of the splash
      return (
        <main className="grid min-h-screen place-items-center bg-cream px-6">
          <div className="card w-full max-w-narrow p-6 text-center">
            <Logo className="mx-auto mb-4" />
            <AlertCircle size={28} className="mx-auto text-danger" />
            <h1 className="mt-3 text-subsection font-semibold">{t('auth.errSign')}</h1>
            <p className="mt-1 text-small text-ink-soft">{error}</p>
            <a
              href={botDeepLink('login')}
              className="mt-4 inline-block text-small text-accent hover:underline"
            >{t('auth.openInBot')}</a>
          </div>
        </main>
      );
    }
    // Brand-only splash (no "Telegram orqali kirilmoqda…" label) so the Mini-App
    // auto-login shows the SAME loading screen as RequireAuth's /auth/me wait —
    // one seamless splash instead of two visibly different ones.
    return <LoadingSplash />;
  }

  return (
    <main className="grid min-h-screen lg:grid-cols-2">
      <section className="relative hidden bg-ink text-white lg:flex lg:flex-col lg:justify-between lg:p-12">
        <Link to={`/${locale}`} className="text-white"><Logo className="text-white" /></Link>
        <div>
          <div className="kicker text-white/60 mb-3">{t('common.appKicker')}</div>
          <h2 className="h-hero-3 font-semibold leading-[1.05] tracking-tight max-w-[480px]">
            {t('auth.brandHeadline')}
          </h2>
          <p className="mt-4 text-white/70 max-w-[440px]">{t('auth.brandSub')}</p>
        </div>
        <div className="text-caption text-white/40">© Muddato {new Date().getFullYear()}</div>
      </section>

      <section className="flex flex-col items-center justify-center bg-cream px-6 py-10">
        <div className="w-full max-w-[420px] text-center">
          <Link to={`/${locale}`} className="lg:hidden mb-10 inline-block"><Logo /></Link>
          <div className="kicker mb-2">{t('common.appKicker')}</div>
          <h1 className="h-display font-semibold tracking-tight">{t('auth.loginTitle')}</h1>
          <p className="mt-3 text-ink-soft text-left">
            {insideTg ? t('auth.signingInTelegram') : t('auth.openInTelegramHint', 'Muddato Telegram ichida ishlaydi. Mini-Appni ochish uchun pastdagi tugmani bosing.')}
          </p>

          {/* Primary path: open the Mini App inside Telegram (the product is
              Telegram-native). The browser OIDC login below stays as a fallback. */}
          <a
            href={botMiniAppLink(BOT_USERNAME, startParam.home())}
            className="mt-8 inline-flex w-full items-center justify-center gap-3 rounded-control bg-[color:var(--c-brand-telegram)] px-6 py-3 text-body font-semibold text-white shadow-card transition hover:brightness-110"
          >
            <TelegramIcon size={18} />
            {t('auth.openInTelegram', 'Telegramda ochish')}
          </a>

          <div className="my-5 flex items-center gap-3 text-micro uppercase tracking-wider text-muted">
            <span className="h-px flex-1 bg-line" /> {t('auth.or')} <span className="h-px flex-1 bg-line" />
          </div>

          <Button
            type="button"
            variant="outline"
            size="lg"
            block
            onClick={onTelegramClick}
            loading={busy}
            className="text-body text-ink-soft shadow-card hover:text-ink"
          >
            {busy ? (
              <><Loader2 size={18} className="animate-spin" /> {t('auth.checking')}</>
            ) : (
              <><TelegramIcon size={18} /> {t('auth.loginButton')}</>
            )}
          </Button>

          {error && (
            <div role="alert" aria-live="assertive" className="mt-6 flex items-start gap-2 rounded-control border border-danger/30 bg-[color:var(--c-danger-soft)] px-4 py-3 text-body text-danger text-left">
              <AlertCircle size={16} className="mt-0.5 shrink-0" />
              <div>
                <div className="font-medium">{t('auth.errSign')}</div>
                <div className="mt-0.5 opacity-90">{error}</div>
                <a
                  href={botDeepLink('login')}
                  target="_blank" rel="noreferrer"
                  className="mt-2 inline-block underline"
                >{t('auth.openInBot')}</a>
              </div>
            </div>
          )}

          <div className="my-7 flex items-center gap-3 text-micro uppercase tracking-wider text-muted">
            <span className="h-px flex-1 bg-line" /> {t('auth.or')} <span className="h-px flex-1 bg-line" />
          </div>

          <p className="text-small text-ink-soft">
            {t('auth.helpPrompt')}{' '}
            <a
              href={`https://t.me/${support}`}
              target="_blank" rel="noreferrer"
              className="text-accent hover:underline"
            >@{support}</a>
          </p>

          {/* Optional secondary entry: open the bot directly (Mini-App). */}
          <p className="mt-3 text-caption text-muted">
            {t('auth.orOpenBot')}{' '}
            <a
              href={botDeepLink('login')}
              target="_blank" rel="noreferrer"
              className="text-accent hover:underline"
            >@{BOT_USERNAME}</a>
          </p>

          <Link to={`/${locale}`} className="mt-6 block text-body text-ink-soft hover:text-ink">
            ← {t('auth.backHome')}
          </Link>
        </div>
      </section>
    </main>
  );
}
