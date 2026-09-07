import { lazy, type ComponentType, type LazyExoticComponent } from 'react';

const RELOAD_FLAG = 'muddato.chunkReloaded';

/**
 * How long to wait for the cache-busting reload to actually take over.
 *
 * A real reload replaces the document within a few hundred ms and discards this
 * timer with it, so this only ever fires when the navigation did NOT happen.
 * Generous on purpose: a slow cold start must not race it.
 */
const RELOAD_GRACE_MS = 6_000;

/** sessionStorage throws (SecurityError) when storage is blocked or partitioned
 *  — which some embedded WebViews do. A bookkeeping flag must never be able to
 *  fail a module load, so every access is best-effort. Before this, a throw from
 *  `removeItem` on the SUCCESS path discarded an already-loaded module and sent
 *  it down the retry/reload ladder, and a throw from `getItem` inside the catch
 *  escaped as the module's own rejection — reporting a storage error as a
 *  failure to load the page. */
function flagGet(): boolean {
  try { return !!sessionStorage.getItem(RELOAD_FLAG); } catch { return false; }
}
function flagSet(): void {
  try { sessionStorage.setItem(RELOAD_FLAG, '1'); } catch { /* storage blocked */ }
}
function flagClear(): void {
  try { sessionStorage.removeItem(RELOAD_FLAG); } catch { /* storage blocked */ }
}

/**
 * `lazy()` that survives stale-chunk failures — critical inside the Telegram
 * Mini-App WebView, which caches `index.html` aggressively and, after a
 * redeploy, can keep pointing at chunk hashes that no longer exist on the new
 * build. A dynamic `import()` for a missing/stalled chunk would otherwise leave
 * the Suspense splash spinning forever (the "stuck on loading" report).
 *
 * Recovery ladder:
 *   1. one in-place retry — covers a transient network blip;
 *   2. a single hard reload with a cache-busting `_r` param — forces the WebView
 *      to re-fetch a fresh `index.html` that references the CURRENT chunks;
 *   3. if it STILL fails after that one reload, rethrow so the RouteErrorBoundary
 *      shows a real error instead of reload-looping.
 *
 * The `_r` query param is ignored by the path-based router and by the OIDC flow.
 *
 * NOTHING HERE MAY HANG. Step 2 used to `return new Promise(() => {})` — a
 * promise that can never settle — on the assumption that the reload always takes
 * over and disposes of it. When the reload does NOT take effect (navigation
 * blocked or swallowed by the host WebView, or the reload lands right back on
 * the same cached HTML) that assumption strands the route in a permanently
 * pending state: no error, no retry, no way out, and — because the failure is a
 * pending promise rather than a rejection — nothing for the error boundary to
 * catch and nothing in the console. The user sees a navigation control that
 * simply does nothing, forever. A bounded wait turns that into an ordinary
 * recoverable error.
 */
// eslint-disable-next-line @typescript-eslint/no-explicit-any
export function lazyWithRetry<T extends ComponentType<any>>(
  factory: () => Promise<{ default: T }>,
): LazyExoticComponent<T> {
  return lazy(async () => {
    try {
      const mod = await factory();
      flagClear(); // clean slate once a chunk loads
      return mod;
    } catch {
      // 1) quick retry
      try {
        const mod = await factory();
        flagClear();
        return mod;
      } catch (err) {
        // 2) one cache-busting reload (guarded so we never loop)
        if (!flagGet()) {
          flagSet();
          try {
            const url = new URL(window.location.href);
            url.searchParams.set('_r', Date.now().toString(36));
            window.location.replace(url.toString());
          } catch {
            window.location.reload();
          }
          // Hand the tab to the reload — but do not bet the route on it. If the
          // document is still here after the grace period the navigation never
          // happened, so surface the original load error rather than hanging.
          return new Promise<{ default: T }>((_resolve, reject) => {
            setTimeout(() => reject(err), RELOAD_GRACE_MS);
          });
        }
        // 3) already reloaded once and still broken → surface the real error
        throw err;
      }
    }
  });
}
