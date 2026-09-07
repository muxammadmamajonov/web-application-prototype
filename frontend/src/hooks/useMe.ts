import { useQuery } from '@tanstack/react-query';
import { useEffect } from 'react';
import { api, hasAuthFlag, setSessionToken } from '@/lib/api';
import { queryClient } from '@/lib/queryClient';
import { queryKeys } from '@/lib/queryKeys';
import { useSession } from '@/store/session';
import { getTelegramWebApp, isInsideTelegramClient, waitForInitData } from '@/lib/telegram';
import type { MeResponse } from '@/types';

async function fetchMe(): Promise<MeResponse> {
  const { data } = await api.get<MeResponse>('/auth/me');
  return data;
}

/** Single source of truth for the current user. Drives persona-aware routing. */
export function useMe() {
  const setMe = useSession((s) => s.setMe);
  // Gate the bootstrap on a NON-sensitive breadcrumb, not a JS-readable token.
  // The HttpOnly cookie is the real credential; a stale flag just costs one
  // /auth/me round-trip that the 401 path cleans up.
  const enabled = hasAuthFlag();

  const query = useQuery({
    queryKey: queryKeys.me.root(),
    queryFn: fetchMe,
    enabled,
    retry: false,
    staleTime: 60_000,
    refetchOnWindowFocus: false,
    refetchOnReconnect: false,
    refetchOnMount: false,
  });

  // On error the response interceptor has already cleared localStorage; mirror
  // it in the store so the next render of RequireAuth bails out cleanly
  // instead of looping into yet another /me request.
  useEffect(() => {
    if (query.data) setMe(query.data);
    if (query.error) setMe(null);
  }, [query.data, query.error, setMe]);

  return query;
}

/**
 * The failure carries the ERROR, never a message.
 *
 * Rendering is the caller's job, through `apiErrorMessage`, which is the only
 * thing that turns a failure into user-facing text. A `message: string` field
 * here would inevitably be filled from `response.data.detail` or `Error.message`
 * — untranslated backend prose, or Axios's English "Network Error" — and a
 * caller could not tell it apart from something safe to display. Keeping the raw
 * error in the type makes that mistake unrepresentable.
 */
type BootstrapOutcome =
  | { kind: 'no-telegram' }
  | { kind: 'logged-in'; via: 'session' | 'webapp' }
  | { kind: 'init-data-missing'; debug: string }
  | { kind: 'error'; status?: number; error: unknown; debug?: string };

/** Outcome enum used by the new LoginPage. */
type WebAppBootstrap =
  | { status: 'logged-in' }
  | { status: 'no-init-data'; debug: string }
  | { status: 'failed'; error: unknown; debug?: string };

/** Translates the legacy outcome to the new enum used by LoginPage. */
export async function loginViaWebAppInitData(): Promise<WebAppBootstrap> {
  const r = await bootstrapTelegramAutoLogin();
  if (r.kind === 'logged-in') return { status: 'logged-in' };
  if (r.kind === 'init-data-missing') return { status: 'no-init-data', debug: r.debug };
  if (r.kind === 'error') return { status: 'failed', error: r.error, debug: r.debug };
  return { status: 'no-init-data', debug: 'not inside Telegram client' };
}

/**
 * True when a 401 body carries the backend's replay code (MUDD-76) — i.e. this
 * exact initData has already been exchanged for a session, rather than being
 * invalid.
 */
function isInitDataReplay(data: unknown): boolean {
  const detail = (data as { detail?: unknown } | undefined)?.detail;
  return detail === 'telegram_initdata_replayed';
}

/** Diagnostic-only serialisation of a response body. Never rendered. */
function safeDebug(data: unknown): string {
  try {
    return JSON.stringify(data ?? null);
  } catch {
    return '[unserialisable]';
  }
}

/**
 * Try to auto-login using the Mini-App initData if we're inside Telegram.
 *
 * Detection is driven by **presence of usable initData**, not the noisier
 * "are we inside Telegram?" check. That way a stale session from an older
 * deploy is *replaced* with a fresh HttpOnly cookie signed by the current
 * SECRET_KEY — instead of being trusted forever and bouncing the user through
 * the 401-redirect loop.
 */
async function bootstrapTelegramAutoLogin(): Promise<BootstrapOutcome> {
  try {
    // First read what the SDK has already populated.
    let wa = getTelegramWebApp();
    // If we're plausibly inside Telegram but initData hasn't landed yet, wait.
    if (!wa && isInsideTelegramClient()) wa = await waitForInitData();

    if (!wa) {
      // Browser visitor — if a prior session breadcrumb exists, assume the
      // HttpOnly cookie is still valid and let useMe/401 confirm it. We can't
      // read the cookie from JS, so this flag is the only client-side signal.
      if (hasAuthFlag()) return { kind: 'logged-in', via: 'session' };
      return { kind: 'no-telegram' };
    }

    try { wa.ready(); wa.expand?.(); } catch { /* SDK quirks */ }

    // Always exchange fresh initData for a fresh session, even if a prior
    // breadcrumb exists. The backend sets a new HttpOnly session cookie on this
    // call, guaranteeing the session matches the current SECRET_KEY after a
    // redeploy. We only record the non-sensitive authed flag client-side.
    const { data } = await api.post<{ token: string }>('/auth/telegram/webapp', {
      init_data: wa.initData,
    });
    setSessionToken(data.token);
    // (#58) A freshly minted token can carry a new identity context — most
    // importantly a tenant just provisioned out-of-band via the demo→bot flow.
    // Drop any cached ['me'] so the next read refetches the real tenant list
    // instead of serving a stale pre-demo `me` (tenants: []) that would route the
    // new owner to the non-client page.
    await queryClient.invalidateQueries({ queryKey: queryKeys.me.root() });
    return { kind: 'logged-in', via: 'webapp' };
  } catch (err) {
    const e = err as { response?: { status?: number; data?: unknown } };
    // Technical detail goes to the console for diagnostics — never to the page.
    console.error('bootstrapTelegramAutoLogin failed', e);
    // (MUDD-76) The backend now spends each initData once, so a *second*
    // exchange within one Mini-App launch — a reload, a bounce back through
    // /auth/login — is refused with `telegram_initdata_replayed`. Telegram hands
    // the same string back for the whole launch, so the client cannot mint a
    // fresh one; the session established by the FIRST exchange is the credential
    // to keep. Clearing it here would log a legitimate user out of their own
    // reload. This is not a hole: an attacker replaying a captured initData has
    // no session cookie, so falling through to /auth/me leaves them 401'd.
    if (isInitDataReplay(e.response?.data)) {
      return hasAuthFlag()
        ? { kind: 'logged-in', via: 'session' }
        : { kind: 'error', status: e.response?.status, error: err, debug: safeDebug(e.response?.data) };
    }
    // Drop a stale token so the user lands on a fresh /auth/login.
    if (e.response?.status === 401) setSessionToken(null);
    return {
      kind: 'error',
      status: e.response?.status,
      error: err,
      debug: safeDebug(e.response?.data),
    };
  }
}
