/**
 * Receipt review: the evidence, and the decision as a separate act.
 *
 * INVARIANT: opening this screen approves nothing. Approve/Reject are explicit
 * authenticated actions taken after the reviewer has seen the receipt.
 *
 * INVARIANT: every figure here is server-formatted, so this screen and the
 * receipt PDF cannot disagree.
 */
import { useTranslation } from 'react-i18next';
import { useNavigate, useParams } from 'react-router';
import { useQuery } from '@tanstack/react-query';
import { Check, FileQuestion, Loader2, X } from 'lucide-react';
import { api } from '@/lib/api';
import { queryKeys } from '@/lib/queryKeys';
import { apiErrorMessage } from '@/lib/errors';
import { toast } from '@/lib/feedback';
import { Button } from '@/components/ui/Button';
import { EmptyState } from '@/components/ui/EmptyState';
import { PageHeader } from '@/components/ui/PageHeader';
import { StatusBadge } from '@/components/ui/Badge';
import { Skeleton } from '@/components/ui/Skeleton';
import { ReceiptImageFrame } from '@/components/ui/ReceiptImageFrame';
import { useApprovePayment, useRejectPayment } from '@/lib/mutations';
import { useSubmitGuard } from '@/hooks/useSubmitGuard';

interface ReviewData {
  id: string;
  customer_name: string | null;
  contract_number: string | null;
  installment_id: string;
  amount_formatted: string;
  currency: string;
  method_label: string;
  status: string;
  status_label: string;
  receipt_image_url: string | null;
  submitted_at_label: string;
  actionable: boolean;
}

export default function PaymentReviewPage() {
  const { paymentId } = useParams<{ paymentId: string }>();
  const { t } = useTranslation();
  const navigate = useNavigate();
  const approve = useApprovePayment();
  const reject = useRejectPayment();
  // Guards a double tap on a MONEY decision. The server locks the row and
  // refuses a second transition, so this is courtesy rather than the guarantee
  // — but a button that stays live through a round trip invites the tap.
  //
  // (§20.11) `useSubmitGuard` must flip its ref synchronously, before any state
  // write and before the first `await`, so two activations dispatched in the
  // SAME frame cannot both pass the guard — a plain `useState` flag is not
  // readable until React commits, one render too late to stop the second tap.
  // `pending` stays the reactive half, for the disable and the spinner.
  //
  // ONE guard instance covers BOTH actions on purpose: Approve-then-Reject in a
  // single frame must also commit exactly one transition, which a per-button
  // lock would not prevent.
  const { pending: deciding, run: runDecision } = useSubmitGuard();

  const q = useQuery({
    queryKey: queryKeys.tenant.paymentReview(paymentId ?? ''),
    queryFn: async () => (await api.get(`/tenant/payments/${paymentId}/review`)).data as ReviewData,
    enabled: !!paymentId,
    retry: false,
  });

  if (q.isLoading) {
    return (
      <div className="space-y-3 p-4">
        <Skeleton className="h-8 w-1/2" />
        <Skeleton className="h-40 w-full" />
      </div>
    );
  }

  if (q.isError || !q.data) {
    // One state for "gone", "not yours" and "never existed" — the server
    // answers them identically and this screen must not undo that.
    return (
      <main className="grid min-h-[60vh] place-items-center px-4">
        <EmptyState
          icon={<FileQuestion size={28} />}
          title={t('review.unknownTitle', 'Havola ochilmadi')}
          body={t('review.notFoundBody', 'Bu toʻlov topilmadi yoki sizga tegishli emas.')}
          action={
            <Button variant="primary" size="md" onClick={() => navigate('..', { relative: 'path' })}>
              {t('common.back', 'Orqaga')}
            </Button>
          }
        />
      </main>
    );
  }

  const p = q.data;

  // `run` takes the lock and sets `pending` before this callback is entered, and
  // releases both in its own `finally` once the whole decision — including the
  // refetch — has settled. A re-entrant call is dropped, not queued.
  const decide = (action: 'approve' | 'reject') => runDecision(async () => {
    try {
      await (action === 'approve' ? approve : reject).mutateAsync(p.id);
      toast.success(
        action === 'approve'
          ? t('payments.approved', 'Toʻlov tasdiqlandi')
          : t('payments.rejected', 'Toʻlov rad etildi'),
      );
      await q.refetch();
    } catch (ex) {
      // Caught here, so a failure still releases the guard and leaves the
      // controls live for a deliberate retry.
      toast.error(apiErrorMessage(ex));
    }
  });

  return (
    <div className="mx-auto w-full max-w-panel space-y-4 p-4">
      <PageHeader
        title={t('review.title', 'Chekni tekshirish')}
        subtitle={p.contract_number ?? undefined}
      />

      <section className="card space-y-3 p-4">
        <dl className="grid grid-cols-[auto_minmax(0,1fr)] gap-x-4 gap-y-2 text-body">
          <dt className="text-muted">{t('review.customer', 'Mijoz')}</dt>
          <dd className="text-right font-medium">{p.customer_name ?? '—'}</dd>

          <dt className="text-muted">{t('review.contract', 'Shartnoma')}</dt>
          <dd className="text-right tabular-nums">{p.contract_number ?? '—'}</dd>

          <dt className="text-muted">{t('review.amount', 'Summa')}</dt>
          <dd className="text-right font-semibold tabular-nums">
            {p.amount_formatted} {p.currency}
          </dd>

          <dt className="text-muted">{t('review.method', 'Toʻlov turi')}</dt>
          <dd className="text-right">{p.method_label}</dd>

          <dt className="text-muted">{t('review.submitted', 'Yuborilgan')}</dt>
          <dd className="text-right">{p.submitted_at_label}</dd>

          <dt className="text-muted">{t('review.status', 'Holat')}</dt>
          <dd className="flex justify-end">
            <StatusBadge status={p.status} />
          </dd>
        </dl>
      </section>

      {p.receipt_image_url && (
        // (LX-02) The proof itself. Served through the authenticated proxy with
        // a download disposition and nosniff, so it is displayed, never
        // executed — see the backend's upload guard.
        //
        // `ReceiptImageFrame` must reserve its box up front (not size to the
        // decoded image), so the two buttons below occupy the same position from
        // first paint whether the image arrives, is slow, or cannot be rendered
        // at all — nothing below it may shift once the page has painted.
        <ReceiptImageFrame
          src={p.receipt_image_url}
          alt={t('review.receiptAlt', 'Yuborilgan chek')}
        />
      )}

      {p.actionable ? (
        // `tap-target` is the repo's coarse-pointer rule: it lifts the hit area
        // to 44px on a phone and leaves the compact desktop size alone. These
        // two buttons commit money on a screen reached from a chat
        // notification, so a mis-tap is the expensive kind.
        <div className="flex gap-3">
          <Button
            variant="outline" size="md" block disabled={deciding}
            className="tap-target"
            onClick={() => void decide('reject')}
            data-testid="reject-payment"
          >
            {deciding ? <Loader2 size={16} className="animate-spin" /> : <X size={16} />}
            {t('common.reject', 'Rad etish')}
          </Button>
          <Button
            variant="primary" size="md" block disabled={deciding}
            className="tap-target"
            onClick={() => void decide('approve')}
            data-testid="approve-payment"
          >
            {deciding ? <Loader2 size={16} className="animate-spin" /> : <Check size={16} />}
            {t('common.approve', 'Tasdiqlash')}
          </Button>
        </div>
      ) : (
        // A processed payment cannot be processed twice, so no control that
        // looks actionable survives the decision. Reopening the link — from a
        // chat message that is still sitting there — shows the final state.
        <p className="rounded-control border border-line bg-cream p-3 text-center text-caption text-muted">
          {t('review.alreadyProcessed', 'Bu toʻlov allaqachon koʻrib chiqilgan.')}
        </p>
      )}
    </div>
  );
}
