import { useEffect, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { useNavigate, useParams, useSearchParams } from 'react-router';
import { useQueryClient } from '@tanstack/react-query';
import {
  AlertTriangle, ArrowLeft, ArrowRight, Banknote, CheckCircle2, CreditCard,
  Loader2, RefreshCw, ShoppingBag,
} from 'lucide-react';
import { BackChip } from '@/components/mobile/primitives';
import { useMyInstallment } from '@/lib/queries';
import { queryKeys } from '@/lib/queryKeys';
import { Skeleton } from '@/components/ui/Skeleton';
import { Button } from '@/components/ui/Button';
import { CardInstructions } from '@/components/ui/CardInstructions';
import { api } from '@/lib/api';
import { apiErrorMessage } from '@/lib/errors';
import { V2_SURFACES_ENABLED } from '@/routes/_scope';
import { money } from '@/lib/format';
import { confirmDialog, toast } from '@/lib/feedback';
import { openExternalLink } from '@/lib/telegram';
import { MoneyInput, MONEY_MAX } from '@/components/ui/FormFields';
import { useSubmitGuard } from '@/hooks/useSubmitGuard';
import { useKeyboardOpen } from '@/hooks/useKeyboardOpen';
import { useMainButton } from '@/lib/telegram/buttons';
import { useClosingConfirmation } from '@/lib/telegram/closing';
import { DEFAULT_CURRENCY } from '@/lib/currency';

/** /me/installments/:id/pay
 *
 *  Two phases:
 *   - "select" — show available methods (Click/Payme/Cash) and let the
 *     user pick + edit amount; default = installment.monthly_amount.
 *   - "wait"   — after method=click|payme: we've POSTed initiate, redirected
 *     the user to the provider; we poll /me/payments/{id}/status until
 *     approved or until the user comes back. Refresh button re-queries.
 *
 *  Cash path forwards to the receipt-upload page (Round 21.3, ?method=cash).
 */
interface MethodRow {
  /** (QA M-3) `card` was missing here, exactly as it was missing from the
   *  backend's accepted set — so the one method the server actually lists
   *  alongside cash was unknown to both ends. `payment_methods.OFFERED` is
   *  ("cash", "card"); click/payme are withdrawn but still wired. */
  method: 'click' | 'payme' | 'cash' | 'card';
  label: string;
  online: boolean;
}

/**
 * The screen title, owned outside the phase branches below.
 *
 * `PayFlow` renders seven mutually-exclusive phases; titling each of them
 * separately is seven chances to end up with none or two. The design leads with
 * the amount card rather than a written title, so the heading is exposed to
 * assistive tech only — the same words the tab title uses for this route.
 */
export default function ClientPayPage() {
  const { t } = useTranslation();
  return (
    <>
      <h1 className="sr-only">{t('routeTitle.pay')}</h1>
      <PayFlow />
    </>
  );
}

function PayFlow() {
  const { id } = useParams<{ id: string }>();
  const [params, setParams] = useSearchParams();
  const { t, i18n } = useTranslation();
  const locale = (i18n.language?.startsWith('ru') ? 'ru' : 'uz') as 'uz' | 'ru';
  const nav = useNavigate();

  const inst = useMyInstallment(id);
  const currency = inst.data?.currency ?? DEFAULT_CURRENCY;
  const qc = useQueryClient();
  const [methods, setMethods] = useState<MethodRow[] | null>(null);
  const [methodsErr, setMethodsErr] = useState<string | null>(null);
  // (#369) Tenant card/transfer details — previously fetched and dropped.
  const [cardInstructions, setCardInstructions] = useState<string | null>(null);
  const [chosen, setChosen] = useState<MethodRow['method'] | null>(null);
  const [amount, setAmount] = useState(0);
  const { pending: initiating, run: runInitiate } = useSubmitGuard();
  const keyboardOpen = useKeyboardOpen();
  const [paymentId, setPaymentId] = useState<string | null>(params.get('payment_id'));
  const [status, setStatus] = useState<string | null>(null);
  const [pollExhausted, setPollExhausted] = useState(false);
  const [pollNonce, setPollNonce] = useState(0);

  /**
   * Seed the monthly instalment ONCE per contract.
   *
   * A suggestion is an opening offer, not a floor. Re-seeding whenever the
   * field is empty makes it impossible to clear — the amount reappears under
   * the cursor and a smaller partial payment gets typed onto the end of it.
   * Only the person, or the explicit quick-pick chips below, change it after
   * the first offer.
   */
  const seededFor = useRef<string | null>(null);
  useEffect(() => {
    if (!inst.data || !id || seededFor.current === id) return;
    seededFor.current = id;
    setAmount(Number(inst.data.monthly_amount) || 0);
  }, [inst.data, id]);

  // Load available methods for THIS tenant/installment.

  // A customer must never be offered a method they cannot complete. The online
  // rails (Click, Payme) are deferred, leaving the manual ones — the shop's
  // transfer details plus receipt upload. Filtering on the provider-agnostic
  // `online` flag rather than on provider names defers a future rail too.
  useEffect(() => {
    if (!id) return;
    // (H15) Without this flag, switching contracts fast can let a stale
    // response for the PREVIOUS `id` land after the effect re-ran — showing
    // the wrong shop's card/transfer details. Mirrors the poll effect below.
    let cancelled = false;
    api.get<{ methods: MethodRow[]; card_instructions: string | null }>(
      `/me/installments/${id}/payment-methods`,
    )
      .then((r) => {
        if (cancelled) return;
        const offered = V2_SURFACES_ENABLED
          ? r.data.methods
          : r.data.methods.filter((m) => !m.online);
        setMethods(offered);
        setCardInstructions(r.data.card_instructions);
      })
      .catch((err) => { if (!cancelled) setMethodsErr(apiErrorMessage(err)); });
    return () => { cancelled = true; };
  }, [id]);

  // Poll payment status while waiting — bounded so it never spins forever.
  const MAX_POLLS = 24;   // ~3min of backoff before we hand control back to the user
  useEffect(() => {
    if (!paymentId) return;
    let cancelled = false;
    let attempt = 0;
    setPollExhausted(false);
    let timer: ReturnType<typeof setTimeout>;
    const tick = async () => {
      if (cancelled) return;
      try {
        const r = await api.get<{ status: string }>(`/me/payments/${paymentId}/status`);
        if (cancelled) return;
        setStatus(r.data.status);
        if (r.data.status === 'approved' || r.data.status === 'rejected' || r.data.status === 'refunded') {
          return;  // stop polling
        }
      } catch {
        // transient network bumps just retry; terminal failure surfaces via timeout below
      }
      attempt += 1;
      if (attempt >= MAX_POLLS) { if (!cancelled) setPollExhausted(true); return; }
      // backoff: 3s, 5s, 8s, 8s, …
      const delay = attempt <= 1 ? 3000 : attempt <= 2 ? 5000 : 8000;
      timer = setTimeout(tick, delay);
    };
    tick();
    return () => { cancelled = true; clearTimeout(timer); };
  }, [paymentId, pollNonce]);

  // Manual "check again" — restarts the bounded poll after a timeout.
  const recheck = () => { setPollExhausted(false); setPollNonce((n) => n + 1); };

  // On the 'approved' transition, drop stale balances so the detail/home
  // screens refetch. The ['me'] prefix covers ['me','installment',id],
  // ['me','summary'] and ['me','installments'].
  useEffect(() => {
    if (status === 'approved') qc.invalidateQueries({ queryKey: queryKeys.me.root() });
  }, [status, qc]);

  // ── handlers ────────────────────────────────────────────────────────
  const onInitiate = async () => {
    if (!id || !chosen) return;
    // Manual rail: hand off to receipt upload, where the customer states what
    // they transferred and attaches proof. No money moves in the app, so this
    // path carries no payment confirmation of its own.
    const chosenRow = methods?.find((x) => x.method === chosen);
    if (!chosenRow?.online) {
      // The method travels with the hand-off. Without it the receipt page
      // cannot tell cash (pay at the counter, nothing to photograph) from card
      // (paid somewhere we cannot see, where the image is the only evidence) —
      // and the backend recorded every manual payment as cash regardless.
      nav(`/${locale}/me/installments/${id}/receipt`
          + `?amount=${encodeURIComponent(String(amount))}`
          + `&method=${encodeURIComponent(chosen)}`);
      return;
    }
    // Online initiation is deferred — unreachable, because the selector above
    // never offers an online method.
    if (!V2_SURFACES_ENABLED) return;
    // (#363) Confirm before money moves online — mirror the friction the erasure
    // flow already applies. Cash/receipt stays frictionless (no money yet).
    const shopName = inst.data?.tenant?.name ?? '';
    const confirmed = await confirmDialog({
      title: t('client.confirmPayTitle', { defaultValue: "To'lovni tasdiqlang" }),
      message: `${money(amount)} ${currency} · ${chosenRow?.label ?? chosen}${shopName ? ` · ${shopName}` : ''} — ${t('client.confirmPayAsk', { defaultValue: "To'lovni boshlaymizmi?" })}`,
      confirmLabel: t('client.payNow', "Hozir to'lash"),
    });
    if (!confirmed) return;
    // Synchronous one-shot guard — a fast double-tap can't fire two /initiate
    // calls (two payments) before React disables the button.
    void runInitiate(async () => {
      try {
        const { data } = await api.post<{ payment_id: string; method: string; pay_url: string | null }>(
          '/me/payments/initiate',
          { installment_id: id, amount: String(amount), method: chosen },
        );
        setPaymentId(data.payment_id);
        setParams({ payment_id: data.payment_id, method: data.method }, { replace: true });
        if (data.pay_url) {
          // Open the provider checkout. Inside Telegram this uses WebApp.openLink
          // (in-app browser) so our polling tab survives; on web it opens a tab.
          openExternalLink(data.pay_url);
        }
      } catch (err) {
        toast.error(apiErrorMessage(err));
      }
    });
  };

  const refresh = async () => {
    if (!paymentId) return;
    try {
      const r = await api.get<{ status: string }>(`/me/payments/${paymentId}/status`);
      setStatus(r.data.status);
    } catch {/* */}
  };

  // ── Telegram-native pay button + closing protection ───────────────────
  // Computed before the phase early-returns below, because hooks must run
  // unconditionally on every render. Inside Telegram the MainButton becomes the
  // pay CTA; in a browser these are inert and the in-page button is shown.
  const payData = inst.data;
  const payRemaining = payData ? Math.max(0, Number(payData.remaining) || 0) : 0;
  const payAmount = amount;
  const inSelectPhase = !!payData && !paymentId;
  const inFlight = !!paymentId && status !== 'approved' && status !== 'rejected' && status !== 'refunded';
  const { isNative: nativePayButton } = useMainButton({
    text: payAmount > 0
      ? `${t('client.payNow', "Hozir to'lash")} · ${money(payAmount)} ${currency}`
      : t('client.payNow', "Hozir to'lash"),
    visible: inSelectPhase,
    enabled: !!chosen && payAmount > 0 && payAmount <= payRemaining,
    loading: initiating,
    onClick: onInitiate,
  });
  // Guard accidental swipe-close while an amount is entered or a payment is mid-flight.
  useClosingConfirmation((inSelectPhase && payAmount > 0) || inFlight);

  // ── render ──────────────────────────────────────────────────────────
  if (inst.isLoading) {
    // (#365) Page-shaped skeleton: back chip, amount card, three method rows.
    return (
      <div className="mx-auto w-full max-w-panel" role="status" aria-busy="true">
        <Skeleton className="h-9 w-24 rounded-icon" />
        <Skeleton className="mt-3 h-40 w-full rounded-card" />
        <div className="mt-5 space-y-2">
          <Skeleton className="h-14 w-full rounded-card" />
          <Skeleton className="h-14 w-full rounded-card" />
          <Skeleton className="h-14 w-full rounded-card" />
        </div>
      </div>
    );
  }
  // (#410 pattern, also applied in ReceiptUploadPage) A bad/stale :id or a
  // failed fetch must not fall through to a blank white trap — show an error
  // card with a way back, not a bare line of text the user can't act on.
  if (inst.isError || !inst.data) {
    return (
      <div className="mx-auto w-full max-w-panel py-12 text-center">
        <AlertTriangle className="mx-auto text-danger" size={28} />
        <div className="mt-2 text-body">{t('client.notFound', 'Topilmadi')}</div>
        <button
          onClick={() => nav(`/${locale}/me`)}
          className="mt-4 inline-flex items-center gap-1 text-small text-accent hover:underline"
        >
          <ArrowLeft size={14} /> {t('client.back', 'Orqaga')}
        </button>
      </div>
    );
  }

  const i = inst.data;
  const remaining = Math.max(0, Number(i.remaining) || 0);
  const amountNum = amount;
  const overRemaining = amountNum > remaining;

  // Phase: provider didn't confirm within the poll window
  if (paymentId && pollExhausted && status !== 'approved' && status !== 'rejected' && status !== 'refunded') {
    return (
      <div className="mx-auto w-full max-w-panel">
        <BackChip onClick={() => nav(-1)} label={t('client.back', 'Orqaga')} />
        <div role="status" aria-live="polite" className="rounded-card border border-warn/30 bg-[color:var(--c-warn-soft)] p-6 text-center">
          <div className="text-subsection font-semibold">{t('client.payPendingTitle', "To'lov hali tasdiqlanmadi")}</div>
          <p className="mt-1 text-caption text-ink-soft">
            {t('client.payPendingBody', "Agar pulni to'lagan bo'lsangiz, u tez orada tasdiqlanadi. Keyinroq tekshiring yoki do'kon bilan bog'laning.")}
          </p>
          <div className="mt-4 flex justify-center gap-2">
            <Button variant="inverse" size="sm" onClick={recheck}>
              <RefreshCw size={12} /> {t('client.checkAgain', 'Tekshirish')}
            </Button>
            <Button variant="outline" size="sm" onClick={() => nav(`/${locale}/me/installments/${id}`)}>
              {t('client.back', 'Orqaga')}
            </Button>
          </div>
        </div>
      </div>
    );
  }

  // Phase: waiting for provider
  if (paymentId && (status === null || status === 'pending')) {
    return (
      <div className="mx-auto w-full max-w-panel">
        <BackChip onClick={() => nav(-1)} label={t('client.back', 'Orqaga')} />
        <div role="status" aria-live="polite" className="rounded-card border border-line bg-surface p-6 text-center">
          <Loader2 className="mx-auto animate-spin text-accent" size={32} />
          <div className="mt-4 text-subsection font-semibold">{t('client.payingTitle', 'To\'lov amalga oshmoqda…')}</div>
          <p className="mt-1 text-caption text-muted">
            {t('client.payingBody', 'Wallet ilovasida to\'lovni tasdiqlang. So\'ng shu sahifa avtomatik yangilanadi.')}
          </p>
          <Button variant="outline" size="sm" className="mt-4" onClick={refresh}>
            <RefreshCw size={12} /> {t('client.refresh', 'Yangilash')}
          </Button>
        </div>
      </div>
    );
  }

  // Phase: terminal
  if (paymentId && status === 'approved') {
    return (
      <div className="mx-auto w-full max-w-panel">
        <div role="status" aria-live="polite" className="rounded-card border border-success/30 bg-success/10 p-6 text-center">
          <CheckCircle2 className="mx-auto text-success" size={40} />
          <div className="mt-3 text-subsection font-semibold">{t('client.paidTitle', 'To\'lov qabul qilindi')}</div>
          <p className="mt-1 text-caption text-ink-soft">
            {t('client.paidBody', 'Qarz qoldiq yangilandi. Chek yuklab olish uchun installment sahifasiga qayting.')}
          </p>
          <Button variant="inverse" size="sm" className="mt-4" onClick={() => nav(`/${locale}/me/installments/${id}`)}>
            {t('client.back', 'Orqaga')} <ArrowRight size={12} />
          </Button>
        </div>
      </div>
    );
  }
  if (paymentId && (status === 'rejected' || status === 'refunded')) {
    return (
      <div className="mx-auto w-full max-w-panel">
        <div role="alert" aria-live="assertive" className="rounded-card border border-danger/30 bg-danger/10 p-6 text-center">
          <div className="text-subsection font-semibold text-danger">{t('client.paymentFailed', 'To\'lov amalga oshmadi')}</div>
          {/* (#581) Map the raw provider status enum to friendly localized copy
              with a recovery hint — never show the raw enum to the customer. */}
          <p className="mt-1 text-caption text-ink-soft">
            {status === 'rejected' ? t('client.paymentRejected', "To'lov rad etildi — boshqa usul bilan urinib ko'ring yoki @MuddatoSupport bilan bog'laning")
              : status === 'refunded' ? t('client.paymentRefunded', "To'lov qaytarildi — boshqa usul bilan urinib ko'ring yoki @MuddatoSupport bilan bog'laning")
              : status}
          </p>
          <Button
            variant="outline"
            size="sm"
            className="mt-4"
            onClick={() => { setPaymentId(null); setStatus(null); setParams({}, { replace: true }); }}
          >
            {t('client.tryAgain', 'Qaytadan urinish')}
          </Button>
        </div>
      </div>
    );
  }

  // Phase: method selector
  return (
    <div className="mx-auto w-full max-w-panel">
      <BackChip onClick={() => nav(-1)} label={t('client.back', 'Orqaga')} />

      {/* Summary row */}
      <header className="flex items-start gap-3">
        {i.tenant.logo
          ? <img src={i.tenant.logo} alt="" className="h-10 w-10 rounded-control object-cover" />
          : <div className="grid h-10 w-10 place-items-center rounded-control bg-cream"><ShoppingBag size={16} className="text-ink-soft" /></div>}
        <div className="min-w-0">
          <div className="truncate text-body font-semibold">{i.tenant.name}</div>
          <div className="font-mono text-micro text-muted">{i.contract_number ?? i.id.slice(0, 8)}</div>
        </div>
      </header>

      {/* The surface is `--c-accent-fill` (5.10:1 against white), not
          `--c-accent` (3.63:1) — the small labels on this card need AA, which
          the brand orange cannot carry, and a translucent white foreground
          drops it further still. */}
      <div className="mt-5 rounded-card bg-[color:var(--c-accent-fill)] p-5 text-accent-ink">
        <label htmlFor="pay-amount" className="kicker-on-accent block">{t('client.amount', 'Summa')}</label>
        <div className="mt-1 flex items-baseline gap-2">
          {/* The shared money field: grouped thousands, zero-is-empty, a caret
              that survives regrouping, and paste that tolerates separators.
              Clamped to what the contract still owes, so the field cannot even
              hold an overpayment. `leading-none` with no padding would collapse
              the hit box to the 20 px text box; `min-h-11` restores a 44 px
              target on every pointer type (`tap-target` covers only coarse). */}
          <MoneyInput
            id="pay-amount"
            aria-label={t('client.amount', 'Summa')}
            value={amount}
            onChange={setAmount}
            max={remaining || MONEY_MAX}
            unstyled
            className="tap-target min-h-11 w-full bg-transparent text-display font-semibold leading-none outline-none placeholder:text-accent-ink/60"
          />
          <span className="text-subsection">{currency}</span>
        </div>
        <div className="mt-2 text-caption text-accent-ink">
          {t('client.monthlyAmountHint', 'Oylik to\'lov:')} <b>{money(i.monthly_amount)}</b>
          {' · '}
          {t('client.remaining', 'Qarz qoldiq')}: <b>{money(i.remaining)}</b>
        </div>
        {/* Quick-pick: monthly / full payoff — avoids hand-typing the amount,
            and is the ONLY way the suggestion comes back once edited. Outlined
            rather than tinted: a translucent white fill lifted the background
            and dropped the label below AA. */}
        <div className="mt-3 flex gap-2">
          <button type="button" onClick={() => setAmount(Number(i.monthly_amount) || 0)}
                  aria-pressed={amount === Number(i.monthly_amount)}
                  className={`inline-flex items-center justify-center tap-target min-h-11 rounded-pill border px-3 py-1.5 text-caption font-medium transition ${amount === Number(i.monthly_amount) ? 'border-white bg-white text-ink' : 'border-white/60 text-accent-ink hover:border-white'}`}>
            {t('client.amountMonthly', 'Oylik')}
          </button>
          <button type="button" onClick={() => setAmount(Number(i.remaining) || 0)}
                  aria-pressed={amount === Number(i.remaining)}
                  className={`inline-flex items-center justify-center tap-target min-h-11 rounded-pill border px-3 py-1.5 text-caption font-medium transition ${amount === Number(i.remaining) ? 'border-white bg-white text-ink' : 'border-white/60 text-accent-ink hover:border-white'}`}>
            {t('client.amountFull', 'To‘liq')}
          </button>
        </div>
      </div>

      {/* Methods */}
      <div className="mt-5 space-y-2">
        <div className="kicker mb-2">{t('client.method', 'To\'lov turini tanlang')}</div>
        {methodsErr && <div role="alert" aria-live="assertive" className="text-caption text-danger">{methodsErr}</div>}
        {!methods && !methodsErr && (
          <div role="status" aria-live="polite" aria-label={t('client.loadingMethods')} className="flex items-center gap-2">
            <Loader2 className="animate-spin text-accent" size={16} />
          </div>
        )}
        {methods?.map((m) => (
          <button
            key={m.method}
            onClick={() => setChosen(m.method)}
            className={`flex w-full items-center justify-between rounded-card border px-4 py-3 text-left transition ${
              chosen === m.method
                ? 'border-accent bg-[color:var(--c-accent-soft)]'
                : 'border-line bg-surface hover:bg-cream/40'
            }`}
          >
            <div className="flex items-center gap-3">
              {/* `card` is one of the two methods the product offers, and was
                  the only row rendering with no icon at all. */}
              {(m.method === 'click' || m.method === 'payme' || m.method === 'card')
                && <CreditCard size={20} className="text-accent" />}
              {m.method === 'cash' && <Banknote size={20} className="text-ink-soft" />}
              <div>
                <div className="font-semibold">{m.label}</div>
                <div className="text-micro text-muted">
                  {m.online
                    ? t('client.methodOnlineHint', 'Wallet ilovasida avtomatik')
                    : m.method === 'cash'
                      // Cash is handed over at the shop; there is often no chek
                      // to photograph, and the shop approving it took the money.
                      ? t('client.methodCashHint', "Do'konda naqd to'lang")
                      : t('client.methodProofHint', 'Chek foto yuborish kerak')}
                </div>
              </div>
            </div>
            {chosen === m.method && <CheckCircle2 size={18} className="text-accent" />}
          </button>
        ))}
        {/* (#369) Show where/how to pay as soon as a manual method is chosen —
            the shop's transfer details are what the customer actually needs on
            this screen. */}
        {chosen && !methods?.find((m) => m.method === chosen)?.online && cardInstructions && (
          <div className="mt-3">
            <CardInstructions text={cardInstructions} />
          </div>
        )}
      </div>

      {overRemaining && (
        <p className="mt-3 text-caption text-danger">
          {t('client.overRemaining', { amount: money(remaining), defaultValue: `Maksimal: ${money(remaining)} ${currency}` })}
        </p>
      )}

      {/* Sticky pay CTA. (#361) /pay is a subpage where MobileBottomNav renders
          null, so the old 64px nav offset left a phantom empty band — sit flush
          above the safe-area instead. When the soft keyboard is open the visual
          viewport shrinks; drop to bottom:0 so the CTA lifts above the keypad and
          stays tappable while the amount field is focused. */}
      {/* Inside Telegram the native MainButton (driven above) is the pay CTA, so
          this in-page button is only rendered in a browser / on old clients. */}
      {!nativePayButton && (
        <div
          className="sticky z-10 mt-6 -mx-4 border-t border-line bg-cream/95 px-4 py-3 backdrop-blur"
          style={{ bottom: keyboardOpen ? 0 : 'var(--tg-safe-bottom)' }}
        >
          <Button
            variant="inverse"
            size="lg"
            block
            disabled={!chosen || initiating || amountNum <= 0 || overRemaining}
            onClick={onInitiate}
          >
            {initiating && <Loader2 size={14} className="animate-spin" />}
            {t('client.payNow', 'Hozir to\'lash')}
          </Button>
        </div>
      )}
    </div>
  );
}
