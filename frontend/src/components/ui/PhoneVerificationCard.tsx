import { useCallback, useEffect, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { CheckCircle2, Loader2, Phone, ShieldAlert } from 'lucide-react';
import { Button } from '@/components/ui/Button';
import { requestContact } from '@/lib/telegram/permissions';
import {
  POLL_INTERVAL_MS, WAIT_TIMEOUT_MS, canRetry, identityMessageKey,
  isSettled, needsSupport,
  type VerificationState,
} from '@/lib/identityStates';

export interface IdentitySnapshot {
  state: string;
  verified: boolean;
  phoneMasked: string | null;
}

/**
 * Prove a phone number through Telegram, and say honestly where the flow is.
 *
 * THE CLIENT NEVER DECIDES. Tapping the button opens Telegram's own consent
 * sheet; its callback reports whether the person tapped "Share" and nothing
 * else. The number goes to the bot as an authenticated update, which is a
 * webhook round-trip away, so afterwards this asks the server — repeatedly,
 * with a bounded wait — what the server now believes.
 *
 * Every branch is a labelled state with its own sentence: already verified,
 * asking, waiting, cancelled, this Telegram is too old, the wait ran out, wrong
 * region, wrong number for this invitation, and the two cases only a human can
 * untangle. None of them is an icon on its own.
 */
export function PhoneVerificationCard({
  snapshot, onRefetch, onVerified,
}: {
  /** Server-owned state. `null` while it is still being fetched. */
  snapshot: IdentitySnapshot | null;
  /** Re-read the server's state. Must return the fresh snapshot. */
  onRefetch: () => Promise<IdentitySnapshot | null>;
  onVerified?: (snapshot: IdentitySnapshot) => void;
}) {
  const { t } = useTranslation();
  const [flow, setFlow] = useState<VerificationState | null>(null);
  const stopRef = useRef<(() => void) | null>(null);
  const onRefetchRef = useRef(onRefetch);
  onRefetchRef.current = onRefetch;
  const onVerifiedRef = useRef(onVerified);
  onVerifiedRef.current = onVerified;

  // Any in-flight wait is abandoned when this leaves the screen; a timer that
  // outlives its card would keep polling a surface nobody is looking at.
  useEffect(() => () => stopRef.current?.(), []);

  const waitForServer = useCallback(() => {
    stopRef.current?.();
    setFlow('waiting');
    const startedAt = Date.now();
    let cancelled = false;
    let timer: number | undefined;

    const tick = async () => {
      if (cancelled) return;
      let fresh: IdentitySnapshot | null = null;
      try {
        fresh = await onRefetchRef.current();
      } catch {
        // A dropped connection mid-wait is not a failed verification — the
        // update may already have landed. Keep asking until the deadline.
      }
      if (cancelled) return;
      if (fresh?.verified) {
        setFlow(null);
        onVerifiedRef.current?.(fresh);
        return;
      }
      if (Date.now() - startedAt >= WAIT_TIMEOUT_MS) { setFlow('timeout'); return; }
      timer = window.setTimeout(() => void tick(), POLL_INTERVAL_MS);
    };

    stopRef.current = () => { cancelled = true; if (timer) window.clearTimeout(timer); };
    timer = window.setTimeout(() => void tick(), POLL_INTERVAL_MS);
  }, []);

  const start = useCallback(async () => {
    setFlow('prompting');
    const result = await requestContact();       // called straight from the tap
    if (result === 'unsupported') { setFlow('unsupported'); return; }
    if (result === 'cancelled') { setFlow('cancelled'); return; }
    waitForServer();
  }, [waitForServer]);

  const serverState = (snapshot?.state ?? 'VERIFICATION_REQUIRED') as VerificationState;
  const state: VerificationState = flow ?? serverState;
  // Only the server's `VERIFIED` settles this. `verified: true` on its own is
  // not enough: a proved number can still be the wrong one for this invitation,
  // or belong to a region that cannot trade — both arrive with `verified: true`
  // and a state that must be shown, not congratulated.
  const settled = isSettled(state);

  if (settled) {
    return (
      <div data-testid="identity-card" data-state="VERIFIED"
           className="flex items-center gap-3 rounded-card border border-line bg-cream px-4 py-3">
        <CheckCircle2 size={20} className="shrink-0 text-success" aria-hidden="true" />
        <div className="min-w-0">
          <p className="text-body font-medium">{t(identityMessageKey('VERIFIED'))}</p>
          {snapshot?.phoneMasked && (
            // Masked: enough to recognise your own number, useless to anyone
            // reading over a shoulder.
            <p className="truncate text-caption tabular-nums text-muted">{snapshot.phoneMasked}</p>
          )}
        </div>
      </div>
    );
  }

  const busy = state === 'prompting' || state === 'waiting';
  const support = needsSupport(state);

  return (
    <div data-testid="identity-card" data-state={state}
         className="rounded-card border border-line bg-surface p-4">
      <div className="flex items-start gap-3">
        {support
          ? <ShieldAlert size={20} className="mt-0.5 shrink-0 text-danger" aria-hidden="true" />
          : <Phone size={20} className="mt-0.5 shrink-0 text-muted" aria-hidden="true" />}
        <div className="min-w-0 flex-1">
          {/* A status region, so the state change is announced rather than just
              repainted. `alert` for the ones that stop the flow. */}
          <p role={support ? 'alert' : 'status'} aria-live="polite"
             className="text-body text-ink-soft">
            {t(identityMessageKey(state))}
          </p>
          {busy && (
            <p className="mt-1 flex items-center gap-1.5 text-caption text-muted">
              <Loader2 size={14} className="animate-spin" aria-hidden="true" />
              {t('identity.waitingHint', 'Telegram javobini kutmoqda…')}
            </p>
          )}
        </div>
      </div>

      {!support && state !== 'unsupported' && (
        <div className="mt-3">
          <Button variant="primary" size="md" block type="button"
                  className="tap-target" loading={busy} onClick={() => void start()}>
            {t(canRetry(state) && state !== 'VERIFICATION_REQUIRED'
              ? 'identity.retry' : 'identity.verifyAction')}
          </Button>
        </div>
      )}
    </div>
  );
}
