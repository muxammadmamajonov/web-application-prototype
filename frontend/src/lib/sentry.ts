import * as Sentry from '@sentry/react';

// (#159) Build-time DSN. When unset (dev / preview without a DSN) Sentry stays
// fully inert — no init, no events — so nothing changes until you configure it.
const DSN = (import.meta.env as Record<string, string | undefined>).VITE_SENTRY_DSN;

/** Initialize Sentry only when a DSN is configured. No-op otherwise. */
export function initSentry(): void {
  if (!DSN) return;
  Sentry.init({
    dsn: DSN,
    environment: import.meta.env.MODE,
    tracesSampleRate: 0.1,
    sendDefaultPii: false, // never ship PII to Sentry
  });
}

/** Report a caught render error (no-op until initSentry runs with a DSN). */
export function reportError(error: unknown, componentStack?: string | null): void {
  if (!DSN) return;
  Sentry.captureException(
    error,
    componentStack ? { contexts: { react: { componentStack } } } : undefined,
  );
}
