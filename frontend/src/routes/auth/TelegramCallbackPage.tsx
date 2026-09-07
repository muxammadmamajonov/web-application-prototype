import { useEffect, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { useNavigate, useSearchParams } from 'react-router';
import { AlertCircle, CheckCircle2 } from 'lucide-react';
import { LoadingSplash } from '@/components/ui/LoadingSplash';
import { Logo } from '@/components/layout/Logo';
import { Button } from '@/components/ui/Button';
import { api, setSessionToken } from '@/lib/api';
import { apiErrorMessage } from '@/lib/errors';
import { safeInternalPath } from '@/lib/url';

/**
 * OIDC callback handler.
 *
 * Telegram redirects the user back to `https://muddato.uz/auth/callback`
 * with `?code=AUTH_CODE&state=OPAQUE_STATE` (or `?error=…&error_description=…`
 * on cancellation). We POST `{code, state}` to the backend which exchanges
 * the code for an `id_token` at `oauth.telegram.org/token`, verifies it, and
 * mints our session JWT.
 *
 * For backwards-compat with the legacy iframe widget that posts a signed
 * `tgAuthResult` JSON in the hash fragment, we still detect that case and
 * forward to `/auth/telegram/oauth` — but new logins always go through OIDC.
 */
export default function TelegramCallbackPage() {
  const nav = useNavigate();
  const { t } = useTranslation();
  const [params] = useSearchParams();
  const [status, setStatus] = useState<'pending' | 'ok' | 'error'>('pending');
  const [detail, setDetail] = useState<string | null>(null);
  const [debug, setDebug] = useState<string | null>(null);
  // One-shot — the OIDC `state` is consumed server-side on the FIRST POST
  // (Redis deletes the key after lookup). If React re-fires this effect for
  // any reason (StrictMode in dev, dependency identity flip, transient
  // remount), the second POST would always 400 with state_expired_or_unknown
  // and surface as a misleading error. Guard against that explicitly.
  const ranRef = useRef(false);

  useEffect(() => {
    if (ranRef.current) return;
    ranRef.current = true;
    (async () => {
      // Telegram OAuth cancellation
      const errParam = params.get('error');
      if (errParam) {
        setStatus('error');
        setDetail(params.get('error_description') ?? errParam);
        return;
      }

      const code  = params.get('code');
      const state = params.get('state');
      const next  = safeInternalPath(sessionStorage.getItem('muddato.oidcNext'));

      // ── Modern OIDC path: ?code=…&state=… ────────────────────────────
      if (code && state) {
        // Strip code+state from the URL immediately so a refresh / back-nav
        // doesn't re-fire the exchange against a one-shot state Redis has
        // already deleted (which gives "state_expired_or_unknown" forever).
        history.replaceState(null, '', location.pathname);
        try {
          const { data } = await api.post<{ token: string }>(
            '/auth/telegram/callback',
            { code, state },
          );
          setSessionToken(data.token);
          sessionStorage.removeItem('muddato.oidcNext');
          setStatus('ok');
          nav(next, { replace: true });
        } catch (err) {
          const e = err as {
            response?: { status?: number; data?: { detail?: string } };
            message?: string;
            code?: string;
            name?: string;
          };
          console.error('telegram/callback failed', e);
          setStatus('error');
          setDetail(apiErrorMessage(err));
          // Include axios `code` (ERR_NETWORK, ECONNABORTED, …) so future
          // "Network Error" reports are diagnosable without browser DevTools.
          setDebug(
            `http=${e.response?.status ?? '-'} ` +
            `code=${e.code ?? '-'} ` +
            `body=${JSON.stringify(e.response?.data ?? null)}`,
          );
        }
        return;
      }

      // ── Legacy iframe widget fallback: hash fragment tgAuthResult ─────
      const hash = location.hash.replace(/^#/, '');
      const legacy = readLegacyHashPayload(hash);
      if (legacy) {
        history.replaceState(null, '', location.pathname);
        try {
          const { data } = await api.post<{ token: string }>('/auth/telegram/oauth', legacy);
          setSessionToken(data.token);
          setStatus('ok');
          nav(next, { replace: true });
        } catch (err) {
          const e = err as { response?: { status?: number; data?: { detail?: string } }; message?: string };
          console.error('legacy oauth failed', e);
          setStatus('error');
          setDetail(apiErrorMessage(err));
        }
        return;
      }

      // Nothing usable in the URL.
      setStatus('error');
      setDetail(t('auth.errCallbackNoData'));
      setDebug(`search=${location.search.slice(0, 200)} hash=${location.hash.slice(0, 200)}`);
    })();
  }, [nav, params, t]);

  if (status === 'pending') {
    return <LoadingSplash label={t('auth.completing')} />;
  }

  return (
    <main className="grid min-h-screen place-items-center bg-cream px-6">
      <div className="card w-full max-w-panel p-8 text-center">
        <Logo className="mx-auto mb-6" />

        {status === 'ok' && (
          <>
            <CheckCircle2 size={32} className="mx-auto text-success" />
            <h1 className="mt-4 text-subsection font-semibold">{t('auth.welcomeTitle')}</h1>
            <p className="mt-2 text-small text-ink-soft">{t('auth.redirecting')}</p>
          </>
        )}

        {status === 'error' && (
          <>
            <AlertCircle size={32} className="mx-auto text-danger" />
            <h1 className="mt-4 text-subsection font-semibold">{t('auth.errSign')}</h1>
            <p className="mt-2 text-small text-ink-soft">{detail}</p>
            {/* Technical detail is for a developer, not for a shop owner who
                cannot sign in — dev builds only. */}
            {debug && import.meta.env.DEV && (
              <details className="mt-3 text-left">
                <summary className="cursor-pointer text-caption text-muted">{t('auth.errDetails', 'Texnik tafsilotlar')}</summary>
                <pre className="mt-2 max-h-32 overflow-auto rounded-icon bg-cream p-2 text-micro font-mono text-muted">
                  {debug}
                </pre>
              </details>
            )}
            <Button
              variant="inverse"
              size="md"
              onClick={() => nav('/auth/login', { replace: true })}
              className="mt-6"
            >
              {t('auth.retry')}
            </Button>
          </>
        )}
      </div>
    </main>
  );
}

interface LegacyTgUser {
  id: number;
  first_name?: string;
  last_name?: string;
  username?: string;
  photo_url?: string;
  auth_date: number;
  hash: string;
}

function readLegacyHashPayload(hash: string): LegacyTgUser | null {
  if (!hash) return null;
  for (const part of hash.split('&')) {
    const [k, v] = part.split('=');
    if (k === 'tgAuthResult' && v) {
      try {
        const padded = v.replace(/-/g, '+').replace(/_/g, '/');
        const json = atob(padded + '='.repeat((4 - (padded.length % 4)) % 4));
        return JSON.parse(decodeURIComponent(escape(json))) as LegacyTgUser;
      } catch {
        return null;
      }
    }
  }
  const params = new URLSearchParams(hash);
  if (params.get('id') && params.get('hash')) {
    return {
      id: Number(params.get('id')),
      first_name: params.get('first_name') ?? undefined,
      last_name: params.get('last_name') ?? undefined,
      username: params.get('username') ?? undefined,
      photo_url: params.get('photo_url') ?? undefined,
      auth_date: Number(params.get('auth_date')),
      hash: params.get('hash')!,
    };
  }
  return null;
}
