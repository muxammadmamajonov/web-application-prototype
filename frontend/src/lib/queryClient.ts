import { QueryClient } from '@tanstack/react-query';
import { isRetryable } from './errors';

/**
 * Shared QueryClient instance. Exported (rather than created inline in
 * main.tsx) so logout() can clear the cache on sign-out.
 *
 * The read/write asymmetry below is the safety property of this file:
 *
 *   READS  may recover. They are idempotent, so refetching after connectivity
 *          returns cannot double-anything. Retries stay narrow — only failures
 *          `isRetryable()` classifies as transport/server/rate-limit, never a
 *          validation, permission, not-found or conflict failure, which would
 *          fail identically however many times it is repeated.
 *
 *   WRITES never recover on their own. `retry: false`, and no reconnect
 *          handling for mutations anywhere: a contract, a payment, an approval,
 *          a reschedule, a balance credit or a top-up that may already have
 *          reached the server must never be replayed by the client. A failed
 *          money mutation surfaces to the person who triggered it, who decides
 *          whether to try again.
 */
export const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      // Bounded, kind-aware retry: at most two extra attempts, and only for
      // failures where repeating the identical request could plausibly succeed.
      retry: (failureCount, error) => failureCount < 2 && isRetryable(error),
      retryDelay: (attempt) => Math.min(1000 * 2 ** attempt, 5000),
      // Recover when the device comes back online — shop-floor phones on mobile
      // data drop requests routinely. Focus/mount refetching stays off: it would
      // multiply requests on a metered connection for no recovery benefit.
      refetchOnWindowFocus: false,
      refetchOnReconnect: true,
      refetchOnMount: false,
      staleTime: 60_000,
      // Cache for 10 minutes; after 5 min staleness kicks in, so returning users
      // see stale data while refetching rather than a cold skeleton (LX-11).
      gcTime: 10 * 60 * 1000,
    },
    // Never automatically replayed. See the note above.
    mutations: { retry: false },
  },
});
