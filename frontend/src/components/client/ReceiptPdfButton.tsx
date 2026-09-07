import { useMemo } from 'react';
import { useTranslation } from 'react-i18next';
import { Check, Loader2, Receipt, RotateCcw } from 'lucide-react';
import { useDocumentSend } from '@/lib/useDocumentSend';
import { cn } from '@/lib/cn';

/**
 * One payment row's receipt-PDF action, with its own request state.
 *
 * WHY A COMPONENT AND NOT AN INLINE HANDLER. The payment history is a `.map()`,
 * and a hook cannot be called inside a loop — so the choice is one shared state
 * for the whole list or one component per row. Shared state is the wrong
 * answer twice over: requesting one receipt would disable every other receipt,
 * and a list that comes back from the server in a different order would hand
 * row A's "sent" to row B. State that belongs to a payment has to live in a
 * component keyed by that payment.
 *
 * WHY THE LABEL NEVER SAYS "TELEGRAM". `downloadGranted` returns `delegated`
 * for two different journeys: a server-confirmed delivery into the chat, and a
 * fall-through hand-off of a signed URL to the system browser when chat
 * delivery is unavailable. The button cannot tell those apart, and neither can
 * `isInsideTelegramClient()`. The layer that *does* know already says so
 * (`download.ts` toasts "Fayl Telegramga yuborildi" on the confirmed path), so
 * this control claims only what is true on every arriving path: it is ready.
 */
export function ReceiptPdfButton({ paymentId, amountLabel }: {
  /** The payment whose receipt this is. Also what the server is asked for. */
  readonly paymentId: string;
  /** Pre-formatted amount, so the accessible name says WHICH receipt. */
  readonly amountLabel: string;
}) {
  const { t } = useTranslation();

  // A fresh object literal every render would rebuild the hook's `send`
  // callback every render. The request is a function of the payment id alone.
  const req = useMemo(() => ({
    path: `/tenant/payments/${paymentId}/receipt.pdf`,
    filename: `receipt-${paymentId}.pdf`,
  }), [paymentId]);

  const doc = useDocumentSend('payment_receipt', paymentId, req);

  // Icon-only, so the accessible name is the ONLY thing carrying the state to a
  // screen reader — it has to change with it, and it has to keep naming the
  // payment, or "Chek tayyor" in a list of five receipts says nothing.
  const label = {
    idle:    t('client.receiptPdfFor',     { amount: amountLabel }),
    sending: t('client.receiptPdfSending', { amount: amountLabel }),
    sent:    t('client.receiptPdfReady',   { amount: amountLabel }),
    failed:  t('client.receiptPdfRetry',   { amount: amountLabel }),
  }[doc.state];

  const Icon = doc.state === 'sending' ? Loader2
    : doc.state === 'sent' ? Check
    : doc.state === 'failed' ? RotateCcw
    : Receipt;

  return (
    <>
      <button
        type="button"
        // From `failed`, this IS the retry: the hook's guard is the in-flight
        // ref, not the state, so a failed request needs no reset first. One
        // press to retry, not press-to-reset then press-to-send.
        onClick={() => { void doc.send(); }}
        disabled={doc.busy}
        aria-busy={doc.busy || undefined}
        aria-label={label}
        className={cn(
          // h-11/w-11 is unconditional and must survive every state: an icon
          // swap must not take the 44px box with it.
          'tap-target grid h-11 w-11 shrink-0 place-items-center rounded-icon border',
          'disabled:cursor-not-allowed',
          doc.state === 'sent' ? 'border-success/40 text-success'
            : doc.state === 'failed' ? 'border-danger/40 text-danger'
            : 'border-line text-ink-soft hover:bg-cream',
        )}
      >
        {/* The glyph changes with the state, so the outcome is legible without
            colour — the border tint is a second signal, never the only one. */}
        <Icon size={16} aria-hidden="true" className={cn(doc.state === 'sending' && 'animate-spin')} />
      </button>
      {/* Scoped to this one action. A region around the list would re-announce
          the whole payment history on every state change. Empty while idle, and
          present from first paint — a live region inserted at the moment of the
          change is not reliably announced. */}
      <span role="status" aria-live="polite" className="sr-only">
        {doc.state === 'idle' ? '' : label}
      </span>
    </>
  );
}
