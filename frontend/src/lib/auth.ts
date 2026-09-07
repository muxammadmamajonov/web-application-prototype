import { setSessionToken } from '@/lib/api';
import { queryClient } from '@/lib/queryClient';
import { useSession } from '@/store/session';

/**
 * Full sign-out. Clears every trace of the session so nothing leaks into the
 * next user on a shared device:
 *   - the HttpOnly session cookie (cleared by the backend /auth/logout endpoint)
 *   - the non-sensitive authed breadcrumb (`muddato.authed`) + any legacy
 *     `muddato.session` token evicted from older builds (via setSessionToken(null))
 *   - the persisted session store (`muddato.persist`: active tenant, impersonation)
 *   - the React Query cache (cached `me`, installments, payments, …)
 *
 * Does a hard navigation to `/auth/login` so all in-memory React state is
 * dropped too — the safest reset for a financial app.
 *
 * ORDER IS LOAD-BEARING (COR-02): `reset()` runs FIRST, `setSessionToken(null)`
 * second. The store is `persist`-backed, so `reset()` writes `muddato.persist`
 * as a side effect. Clearing storage first meant that write landed immediately
 * afterwards and re-created the key — cleared, then resurrected as an empty
 * blob. Resetting first lets the storage clear be the last word, so the key is
 * genuinely absent when the user leaves.
 */
export function logout(): void {
  try {
    useSession.getState().reset();
  } catch {
    /* store may not be initialised */
  }
  setSessionToken(null);
  try {
    queryClient.clear();
  } catch {
    /* query client may not be ready */
  }
  if (typeof window !== 'undefined') {
    window.location.assign('/auth/login');
  }
}
