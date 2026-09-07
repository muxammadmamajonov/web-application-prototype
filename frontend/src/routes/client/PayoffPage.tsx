import { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { useNavigate, useParams } from 'react-router';
import {
  AlertTriangle, ArrowLeft, Banknote, CheckCircle2, CreditCard,
  Loader2, TrendingDown,
} from 'lucide-react';
import { BackChip } from '@/components/mobile/primitives';
import { Button } from '@/components/ui/Button';
import { useMainButton } from '@/lib/telegram/buttons';
import { useClosingConfirmation } from '@/lib/telegram/closing';
import { useMyInstallment } from '@/lib/queries';
import { Skeleton } from '@/components/ui/Skeleton';
import { api } from '@/lib/api';
import { apiErrorMessage } from '@/lib/errors';
import { money } from '@/lib/format';
import { toast } from '@/lib/feedback';
import { openExternalLink } from '@/lib/telegram';
import { useSubmitGuard } from '@/hooks/useSubmitGuard';
import { DEFAULT_CURRENCY } from '@/lib/currency';

interface PayoffQuote {
  installment_id: string;
  remaining_principal: string;
  discount_pct: string;
  discount_amount: string;
  payoff_amount: string;
  scheduled_total: string;
  savings_vs_schedule: string;
  months_left: number;
}

interface MethodRow { method: 'click' | 'payme' | 'cash'; label: string; online: boolean }

/** /me/installments/:id/payoff
 *
 *  Customer hits "Hammasini yopish" → we fetch the quote (with tenant's
 *  discount applied), show savings prominently, let them pick a method,
 *  then forward into the same initiate flow as a regular payment.
 */
export default function ClientPayoffPage() {
  const { id } = useParams<{ id: string }>();
  const { t, i18n } = useTranslation();
  const locale = (i18n.language?.startsWith('ru') ? 'ru' : 'uz') as 'uz' | 'ru';
  const nav = useNavigate();

  const inst = useMyInstallment(id);
  const currency = inst.data?.currency ?? DEFAULT_CURRENCY;
  const [quote, setQuote] = useState<PayoffQuote | null>(null);
  const [quoteErr, setQuoteErr] = useState<string | null>(null);
  const [methods, setMethods] = useState<MethodRow[]>([]);
  const [chosen, setChosen] = useState<MethodRow['method'] | null>(null);
  const { pending: paying, run: runPay } = useSubmitGuard();

  useEffect(() => {
    if (!id) return;
    // (H15) Without this flag, switching contracts fast can let a stale
    // response for the PREVIOUS `id` land after the effect re-ran — showing
    // the wrong shop's payoff quote/methods. Same pattern as PayPage.tsx.
    let cancelled = false;
    api.get<PayoffQuote>(`/me/installments/${id}/payoff-quote`)
      .then((r) => { if (!cancelled) setQuote(r.data); })
      .catch((err) => { if (!cancelled) setQuoteErr(apiErrorMessage(err)); });
    api.get<{ methods: MethodRow[] }>(`/me/installments/${id}/payment-methods`)
      .then((r) => { if (!cancelled) setMethods(r.data.methods); })
      .catch(() => {/* silently fall back to cash */});
    return () => { cancelled = true; };
  }, [id]);

  const onPay = () => {
    if (!id || !chosen) return;
    if (chosen === 'cash') {
      nav(`/${locale}/me/installments/${id}/receipt?amount=${encodeURIComponent(quote?.payoff_amount ?? '0')}`);
      return;
    }
    // Synchronous one-shot guard — block a double-tap from firing two payoffs.
    void runPay(async () => {
      try {
        const { data } = await api.post<{ payment_id: string; pay_url: string | null }>(
          `/me/installments/${id}/payoff`,
          { method: chosen },
        );
        if (data.pay_url) openExternalLink(data.pay_url);
        // Forward to /pay with payment_id so the polling UI takes over.
        nav(`/${locale}/me/installments/${id}/pay?payment_id=${data.payment_id}&method=${chosen}`);
      } catch (err) {
        toast.error(apiErrorMessage(err));
      }
    });
  };

  // Telegram-native payoff CTA + closing protection (hooks must run before the
  // early returns below). Inside Telegram the MainButton is the CTA.
  const { isNative: nativePayoffBtn } = useMainButton({
    text: quote
      ? `${t('client.payoffNow', "Hammasini hozir to'lash")} · ${money(quote.payoff_amount)} ${currency}`
      : t('client.payoffNow', "Hammasini hozir to'lash"),
    visible: !!quote,
    enabled: !!chosen && !paying,
    loading: paying,
    onClick: onPay,
  });
  useClosingConfirmation(!!chosen);

  if (inst.isLoading) {
    // (#365) Page-shaped skeleton: back chip, header, quote card.
    return (
      <div className="mx-auto w-full max-w-md" role="status" aria-busy="true">
        <Skeleton className="h-9 w-24 rounded-lg" />
        <Skeleton className="mt-3 h-12 w-2/3" />
        <Skeleton className="mt-4 h-48 w-full rounded-card" />
      </div>
    );
  }
  // (#410 pattern, also applied in PayPage/ReceiptUploadPage) A bad/stale :id
  // or a failed fetch must not fall through to a blank white trap — show an
  // error card with a way back, not a bare line of text the user can't act on.
  if (inst.isError || !inst.data) {
    return (
      <div className="mx-auto w-full max-w-md py-12 text-center">
        <AlertTriangle className="mx-auto text-danger" size={28} />
        <div className="mt-2 text-sm">{t('client.notFound', 'Topilmadi')}</div>
        <button
          onClick={() => nav(`/${locale}/me`)}
          className="mt-4 inline-flex items-center gap-1 text-small text-accent hover:underline"
        >
          <ArrowLeft size={14} /> {t('client.back', 'Orqaga')}
        </button>
      </div>
    );
  }

  return (
    <div className="mx-auto w-full max-w-md">
      <BackChip onClick={() => nav(-1)} label={t('client.back', 'Orqaga')} />

      <header className="mb-4">
        <div className="text-micro uppercase tracking-wider text-muted">{inst.data.tenant.name}</div>
        <h1 className="text-lg font-semibold">{t('client.payoffTitle', 'Hammasini hozir yopish')}</h1>
      </header>

      {quoteErr && (
        <div className="rounded-xl border border-danger/30 bg-[color:var(--c-danger-soft)] p-3 text-caption text-danger">
          <AlertTriangle size={14} className="mr-1 inline" /> {quoteErr}
        </div>
      )}

      {!quote && !quoteErr && (
        <div className="space-y-3" role="status" aria-busy="true">
          <Skeleton className="h-32 w-full rounded-card" />
          <Skeleton className="h-24 w-full rounded-card" />
        </div>
      )}

      {quote && (
        <>
          {/* Savings hero — visible only when there IS a discount */}
          {Number(quote.savings_vs_schedule) > 0 && (
            <div className="rounded-card border border-success/30 bg-success/10 p-4 text-center">
              <div className="inline-flex items-center gap-2 text-success font-semibold">
                <TrendingDown size={16} /> {t('client.youSave', 'Siz tejaysiz')}
              </div>
              <div className="mt-1 text-title font-bold tabular-nums">
                {money(quote.savings_vs_schedule)} {currency}
              </div>
              <div className="text-micro text-ink-soft">
                {t('client.vsSchedule', 'jadval bo\'yicha to\'lashga nisbatan')}
              </div>
            </div>
          )}

          {/* Breakdown */}
          <div className="mt-4 rounded-card border border-line bg-surface p-4">
            <KV label={t('client.remainingPrincipal', 'Qarz qoldiq')}
                value={`${money(quote.remaining_principal)} ${currency}`} />
            {Number(quote.discount_pct) > 0 && (
              <KV label={t('client.discount', 'Chegirma') + ` (${quote.discount_pct}%)`}
                  value={`− ${money(quote.discount_amount)} ${currency}`}
                  tone="success" />
            )}
            <div className="my-3 h-px bg-line" />
            <KV label={t('client.totalToPay', 'Hozir to\'lash')}
                value={`${money(quote.payoff_amount)} ${currency}`}
                big />
            <div className="mt-2 text-micro text-muted">
              {t('client.monthsLeft', '{{n}} oy qoldi', { n: quote.months_left })}
            </div>
          </div>

          {/* Methods */}
          <div className="mt-4 space-y-2">
            <div className="kicker">{t('client.method', 'To\'lov turi')}</div>
            {methods.map((m) => (
              <button
                key={m.method}
                onClick={() => setChosen(m.method)}
                className={`flex w-full items-center justify-between rounded-card border px-4 py-3 text-left transition ${
                  chosen === m.method ? 'border-accent bg-[color:var(--c-accent-soft)]' : 'border-line bg-surface hover:bg-cream/40'
                }`}
              >
                <div className="flex items-center gap-3">
                  {m.method === 'cash' ? <Banknote size={20} className="text-ink-soft" /> : <CreditCard size={20} className="text-accent" />}
                  <div>
                    <div className="font-semibold">{m.label}</div>
                    <div className="text-micro text-muted">
                      {m.online ? t('client.methodOnlineHint') : t('client.methodCashHint')}
                    </div>
                  </div>
                </div>
                {chosen === m.method && <CheckCircle2 size={18} className="text-accent" />}
              </button>
            ))}
          </div>

          {!nativePayoffBtn && (
            <Button variant="primary" size="lg" block className="mt-6" disabled={!chosen || paying} onClick={onPay}>
              {paying && <Loader2 size={14} className="animate-spin" />}
              {t('client.payoffNow', 'Hammasini hozir to\'lash')}
            </Button>
          )}
        </>
      )}
    </div>
  );
}

function KV({ label, value, tone, big }: { label: string; value: string; tone?: 'success'; big?: boolean }) {
  return (
    <div className="flex items-center justify-between gap-3 py-1.5">
      <div className="text-caption text-ink-soft">{label}</div>
      <div className={`${big ? 'text-lg font-bold' : 'text-sm font-semibold'} tabular-nums ${tone === 'success' ? 'text-success' : ''}`}>
        {value}
      </div>
    </div>
  );
}
