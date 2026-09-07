import { useCallback, useRef, useState } from 'react';

/**
 * Synchronous one-shot guard for money-mutating actions (record payment,
 * payoff, approve/reject receipt, top-up, …).
 *
 * The instant the handler runs we flip a `useRef` flag — *before* any `await` —
 * so a fast double-tap that fires a second click before React has re-rendered
 * the disabled button is ignored. This mirrors the OIDC login flow's
 * `startingRef` one-shot (LoginPage.tsx) and closes the gap left by guarding on
 * post-async state (`busy`/`uploading`) alone, which only disables the button
 * after the request has already started.
 *
 * The returned `pending` is reactive state purely for UI (spinner / disabled
 * styling); the ref is the actual gate. `run` resolves whatever the wrapped
 * action returns, or `undefined` if it was a re-entrant call that got dropped.
 *
 * Usage:
 *   const { pending, run } = useSubmitGuard();
 *   const onPay = () => run(async () => { await api.post(...); });
 *   <button disabled={pending} onClick={onPay}>…</button>
 */
export function useSubmitGuard(): {
  /** Reactive flag for disabling/animating the button. */
  pending: boolean;
  /** Wrap the async action; re-entrant calls while in-flight are dropped. */
  run: <T>(action: () => Promise<T>) => Promise<T | undefined>;
} {
  const inFlight = useRef(false);
  const [pending, setPending] = useState(false);

  const run = useCallback(async <T,>(action: () => Promise<T>): Promise<T | undefined> => {
    // Synchronous gate — this check + set happen in the same tick as the click,
    // long before the awaited request resolves, so the second tap is a no-op.
    if (inFlight.current) return undefined;
    inFlight.current = true;
    setPending(true);
    try {
      return await action();
    } finally {
      inFlight.current = false;
      setPending(false);
    }
  }, []);

  return { pending, run };
}
