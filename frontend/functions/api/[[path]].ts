// Cloudflare Pages Function — same-origin proxy for the API.
//
// All `https://muddato.uz/api/*` requests land here. We forward them to the
// Fly backend without the browser ever seeing a cross-origin request. That
// kills three classes of failure that produced our `ERR_NETWORK`:
//
//   1. Tracker/extension blockers that quietly drop XHRs to *.fly.dev
//   2. Safari ITP's third-party request restrictions inside Telegram WebView
//   3. CORS preflight overhead on every POST (the bot opens many WebViews
//      and the extra round-trip occasionally times out on poor connections)
//
// `_redirects` with status 200 only works for *internal* paths on Pages;
// proxying to an external origin requires a Pages Function.
//
// Note: hop-by-hop headers and length-modifying headers are stripped before
// forwarding so the response stream the Fly origin emits is preserved as-is.

// (#98) Origin is env-configurable — set `API_ORIGIN` on the Pages project so
// staging/preview can point at a different backend without a code change. The
// default keeps production working out of the box.
const DEFAULT_ORIGIN = 'https://muddato-api.fly.dev';

// (#98) Hard ceiling on the upstream fetch so a hung Fly origin can't hold a
// proxied request open until the platform kills it — return a clean 504.
const UPSTREAM_TIMEOUT_MS = 30_000;

// Headers that must NOT be forwarded across the proxy hop, per RFC 7230 §6.1.
const HOP_BY_HOP = new Set([
  'connection', 'keep-alive', 'proxy-authenticate', 'proxy-authorization',
  'te', 'trailer', 'transfer-encoding', 'upgrade',
  // Cloudflare-injected / origin-specific headers that confuse Fly's router.
  'host', 'cf-connecting-ip', 'cf-ipcountry', 'cf-ray', 'cf-visitor',
  'x-forwarded-host', 'x-forwarded-proto',
  // (#258 / F-08) Drop client-supplied IP-trust headers. None are browser-
  // forbidden, so a hostile client could set them to spoof the source IP the
  // backend rate-limiter keys on. We strip any inbound copies here, then re-set
  // x-real-client-ip / x-proxy-secret below from the edge-authoritative
  // CF-Connecting-IP — see backend/app/core/ratelimit.py:_client_ip.
  'x-forwarded-for', 'x-real-client-ip', 'x-proxy-secret',
]);

function stripHopByHop(src: Headers): Headers {
  const out = new Headers();
  src.forEach((v, k) => {
    const lk = k.toLowerCase();
    if (lk === 'set-cookie') return;          // carried over intact below
    if (!HOP_BY_HOP.has(lk)) out.set(k, v);
  });
  // Set-Cookie must survive as one header PER cookie. `forEach`/`get` coalesce
  // multiple Set-Cookie into a single comma-joined value the browser can't parse
  // — which silently dropped the session + CSRF cookies that the login response
  // sets (so cookie auth never worked through the proxy; only the Bearer fallback
  // did). `getSetCookie()` returns them as a list so each is re-appended intact.
  const cookies = (src as unknown as { getSetCookie?: () => string[] }).getSetCookie?.();
  if (cookies) for (const c of cookies) out.append('set-cookie', c);
  return out;
}

export const onRequest: PagesFunction<{ API_ORIGIN?: string; PROXY_SHARED_SECRET?: string }> = async ({ request, env }) => {
  const origin = env.API_ORIGIN || DEFAULT_ORIGIN;
  const url = new URL(request.url);
  const target = `${origin}${url.pathname}${url.search}`;

  // (F-08 fix) Forward the real end-user IP so the backend rate-limiter keys
  // per-user, not on Cloudflare's shared egress. CF-Connecting-IP is set by the CF
  // edge (authoritative, not client-controllable); we relay it as X-Real-Client-IP
  // authenticated with the shared secret so a direct-to-Fly request can't forge it.
  const fwdHeaders = stripHopByHop(request.headers);
  const realClientIp = request.headers.get('cf-connecting-ip');
  if (realClientIp && env.PROXY_SHARED_SECRET) {
    fwdHeaders.set('x-real-client-ip', realClientIp);
    fwdHeaders.set('x-proxy-secret', env.PROXY_SHARED_SECRET);
  }

  // Body is only present on methods that can carry one. For GET/HEAD we pass
  // undefined; otherwise we stream the original body straight through.
  const hasBody = !['GET', 'HEAD'].includes(request.method);

  // (#98) Abort the upstream fetch after UPSTREAM_TIMEOUT_MS so a slow/hung
  // origin surfaces as a 504 instead of hanging the request.
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), UPSTREAM_TIMEOUT_MS);

  let upstream: Response;
  try {
    upstream = await fetch(target, {
      method: request.method,
      headers: fwdHeaders,
      body: hasBody ? request.body : undefined,
      redirect: 'manual',
      signal: controller.signal,
    });
  } catch (err) {
    const aborted = err instanceof Error && err.name === 'AbortError';
    return new Response(
      JSON.stringify({ detail: aborted ? 'upstream_timeout' : 'upstream_unreachable' }),
      { status: 504, headers: { 'content-type': 'application/json' } },
    );
  } finally {
    clearTimeout(timer);
  }

  // Mirror the upstream response. We drop hop-by-hop headers on the way back
  // too — Fly sometimes emits `transfer-encoding: chunked` which Cloudflare
  // would otherwise refuse.
  return new Response(upstream.body, {
    status: upstream.status,
    statusText: upstream.statusText,
    headers: stripHopByHop(upstream.headers),
  });
};
