import { useEffect, useState } from 'react';
import { cn } from '@/lib/cn';

/** Force a fresh document fetch (cache-busted) — the manual escape hatch from a
 *  stuck splash, mirroring the automatic recovery in {@link lazyWithRetry}. */
function hardReload() {
  try {
    const url = new URL(window.location.href);
    url.searchParams.set('_r', Date.now().toString(36));
    window.location.replace(url.toString());
  } catch {
    window.location.reload();
  }
}

/**
 * Fullscreen branded loading splash.
 *
 * Shown while RequireAuth is still waiting on `/auth/me` to resolve — replaces
 * the old generic "Yuklanmoqda…" pulse so the user never sees a bare login
 * page flash on the first dashboard hit.
 *
 * The orange ring rotates 360° per ~1.6s; brand colours pulled from the
 * `--c-accent` and `--c-ink` design tokens (the app is light-only).
 */
export function LoadingSplash({
  label,
  className,
}: { label?: string; className?: string }) {
  // Watchdog: if boot/auth/chunk-load wedges (notably the Telegram WebView
  // serving a stale cached shell), surface a manual reload after 10s instead of
  // spinning forever. lazyWithRetry usually auto-recovers chunk failures before
  // this fires; this catches everything else.
  const [stuck, setStuck] = useState(false);
  useEffect(() => {
    const id = setTimeout(() => setStuck(true), 10_000);
    return () => clearTimeout(id);
  }, []);

  return (
    <main
      className={cn(
        'fixed inset-0 z-[100] grid place-items-center bg-cream',
        className,
      )}
      role="status"
      aria-live="polite"
    >
      <div className="flex flex-col items-center gap-6 px-6 text-center">
        <svg
          width={72}
          height={72}
          viewBox="0 0 72 72"
          aria-hidden
          className="muddato-spin"
        >
          {/* faint ring */}
          <circle cx="36" cy="36" r="26" fill="none" stroke="currentColor" strokeWidth="5" opacity="0.12" />
          {/* moving arc */}
          <path
            d="M 36 10 A 26 26 0 1 1 14 50"
            fill="none"
            stroke="var(--c-accent, #e07347)"
            strokeWidth="5"
            strokeLinecap="round"
          />
          {/* lead dot */}
            <circle cx="14" cy="50" r="5" fill="var(--c-accent, #e07347)" />
        </svg>
        <div>
          <div className="text-section font-semibold tracking-tight text-ink">muddato</div>
          {label && <div className="mt-1 text-small text-ink-soft">{label}</div>}
        </div>

        {stuck && (
          <div className="mt-2 flex flex-col items-center gap-2">
            <p className="text-small text-ink-soft">Biroz cho‘zilyapti…</p>
            <button
              type="button"
              onClick={hardReload}
              className="rounded-pill border border-line bg-surface px-4 py-1.5 text-small font-medium text-ink-soft transition hover:border-ink/30 hover:text-ink"
            >
              Qayta yuklash
            </button>
          </div>
        )}
      </div>

      {/* keyframes live with the component so we don't pollute the global stylesheet */}
      <style>{`
        @keyframes muddato_spin { from { transform: rotate(0deg) } to { transform: rotate(360deg) } }
        .muddato-spin { animation: muddato_spin 1.6s linear infinite; transform-origin: 50% 50%; }
        @media (prefers-reduced-motion: reduce) {
          .muddato-spin { animation-duration: 4s; }
        }
      `}</style>
    </main>
  );
}
