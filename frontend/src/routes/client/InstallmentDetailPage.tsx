import { useMemo } from 'react';
import { useTranslation } from 'react-i18next';
import { useNavigate, useParams } from 'react-router';
import {
  AlertTriangle, ArrowLeft, Check, ChevronRight, Clock, CreditCard, FileText,
  MapPin, Phone, RotateCcw, Send, ShoppingBag, TrendingDown,
} from 'lucide-react';
import { useMyInstallment } from '@/lib/queries';
import { useDocumentSend } from '@/lib/useDocumentSend';
import { ReceiptPdfButton } from '@/components/client/ReceiptPdfButton';
import { StatusBadge } from '@/components/ui/Badge';
import { Skeleton, SkeletonList } from '@/components/ui/Skeleton';
import { ErrorCard } from '@/components/ui/ErrorCard';
import { Button } from '@/components/ui/Button';
import { apiErrorMessage, isNotFound } from '@/lib/errors';
import { money, paymentMethodLabel, shortDate } from '@/lib/format';
import { cn } from '@/lib/cn';
import { ProgressRing } from '@/components/mobile/primitives';
import { isInsideTelegramClient } from '@/lib/telegram';
import { displayPhone } from '@/lib/phone';

/** Mijoz · bitta kredit batafsil sahifa.
 *
 *  Dark summary hero (qoldiq + term ring + to'langan/oylik/tugaydi) over a
 *  status-dotted payment schedule — mirrors the design mockup.
 */
export default function ClientInstallmentDetailPage() {
  const { t } = useTranslation();
  // One structurally stable page heading ABOVE the four mutually exclusive
  // states below (loading / transient failure / missing contract / the detail),
  // so every one of them is a titled page and none of them can grow a second
  // h1. `RouteFocusManager` moves focus into <main> on every navigation, so
  // this is the first thing a screen-reader user meets here.
  //
  // sr-only because the visual header already carries the identity (shop name +
  // item), and the copy is the shared route title — the same string the
  // Telegram header and the document title use for this path — so the three
  // cannot drift apart.
  return (
    <>
      <h1 className="sr-only">{t('nav.installments')}</h1>
      <InstallmentDetail />
    </>
  );
}

function InstallmentDetail() {
  const { id } = useParams<{ id: string }>();
  const { t, i18n } = useTranslation();
  const locale = (i18n.language?.startsWith('ru') ? 'ru' : 'uz') as 'uz' | 'ru';
  const q = useMyInstallment(id);
  const nav = useNavigate();

  if (q.isLoading) {
    // (#365) Structural skeleton mirroring the page (back chip, dark hero,
    // schedule rows) — stable layout, no content jump when data lands.
    return (
      <div className="mx-auto w-full max-w-panel" role="status" aria-busy="true">
        <Skeleton className="h-9 w-24 rounded-icon" />
        <Skeleton className="mt-3 h-44 w-full rounded-card" />
        <SkeletonList rows={4} className="mt-5" />
      </div>
    );
  }
  // Genuinely missing (404) vs. a transient fetch failure are different
  // situations: a 404 can't be fixed by retrying, but a network/5xx blip can —
  // and silently treating both as "not found" denies the user a retry on a
  // page they were legitimately trying to reach.
  if (q.error && !isNotFound(q.error)) {
    return (
      <div className="mx-auto w-full max-w-panel py-12">
        <ErrorCard error={q.error} message={apiErrorMessage(q.error)} onRetry={() => q.refetch()} />
      </div>
    );
  }
  if (q.error || !q.data) {
    return (
      <div className="mx-auto w-full max-w-panel py-12 text-center">
        <AlertTriangle className="mx-auto text-danger" size={28} />
        <div className="mt-2 text-body">{t('client.notFound', 'Kredit topilmadi')}</div>
        <button
          onClick={() => nav(`/${locale}/me`)}
          className="mt-4 inline-flex items-center gap-1 text-small text-accent hover:underline"
        >
          <ArrowLeft size={14} /> {t('client.back', 'Orqaga')}
        </button>
      </div>
    );
  }

  return <LoadedInstallmentDetail inst={q.data} />;
}

/** The detail itself, taken as a prop.
 *
 *  The four states above are mutually exclusive and three of them have no
 *  installment, so a hook needed by the loaded view cannot live beside them
 *  without either running before the early returns (and being handed an id that
 *  does not exist yet) or running after one (and changing hook order between
 *  renders). Splitting at the boundary where the data becomes non-optional
 *  makes `inst` a prop that is always there.
 */
function LoadedInstallmentDetail({ inst }: { inst: InstallmentDetailData }) {
  const { t, i18n } = useTranslation();
  const locale = (i18n.language?.startsWith('ru') ? 'ru' : 'uz') as 'uz' | 'ru';
  const nav = useNavigate();

  const currency = inst.currency;
  const endsOn = inst.schedule[inst.schedule.length - 1]?.due_date;
  // Payments this customer has submitted that the shop has not yet decided on.
  const pendingPayments = inst.payments.filter((p) => p.status === 'pending');
  const pendingSum = pendingPayments.reduce((n, p) => n + (Number(p.amount) || 0), 0);

  // (LX-01) The contract used to be `onClick={() => openContractPdf(id)}` — the
  // promise dropped on the floor, the button enabled and unmarked for the whole
  // grant-and-deliver round trip. `useDocumentSend` owns the honest state
  // machine (idle → sending → sent | failed, `sent` only on a confirmed
  // arrival) and the synchronous ref that makes a double tap one request.
  //
  // The request object is memoized because the hook's `send` closes over it;
  // a fresh literal each render would rebuild the callback each render.
  const contractReq = useMemo(() => ({
    path: `/tenant/installments/${inst.id}/contract.pdf`,
    filename: `contract-${inst.id}.pdf`,
  }), [inst.id]);
  const contract = useDocumentSend('contract', inst.id, contractReq);

  // Neutral on success on purpose: `delegated` covers both a confirmed Telegram
  // chat delivery AND the signed-URL fall-through, so this button cannot claim
  // the first without sometimes lying. `download.ts` toasts the Telegram-
  // specific confirmation on the path that actually knows.
  const contractLabel =
    contract.state === 'sending' ? t('file.sending', 'Yuborilmoqda…')
    : contract.state === 'sent' ? t('file.ready', 'Tayyor')
    : contract.state === 'failed' ? t('file.retry', 'Qayta urinish')
    : t('client.openContract', 'Shartnomani ko\'rish (PDF)');
  const ContractIcon =
    contract.state === 'sent' ? Check : contract.state === 'failed' ? RotateCcw : FileText;

  return (
    <div className="mx-auto w-full max-w-panel">
      {/* header */}
      <header className="flex items-center gap-3">
        {/* Inside Telegram the native BackButton is the back affordance, so we
            drop this in-page arrow there; the browser keeps it. */}
        {!isInsideTelegramClient() && (
          <button
            onClick={() => nav(-1)}
            className="tap-target grid h-11 w-11 shrink-0 place-items-center rounded-control text-ink-soft hover:bg-ink/5"
            aria-label={t('client.back', 'Orqaga')}
          >
            <ArrowLeft size={18} />
          </button>
        )}
        {inst.tenant.logo
          ? <img src={inst.tenant.logo} alt="" className="h-10 w-10 rounded-control object-cover" />
          : <div className="grid h-10 w-10 place-items-center rounded-control bg-cream"><ShoppingBag size={18} className="text-ink-soft" /></div>}
        <div className="min-w-0 flex-1">
          <div className="text-micro text-muted">{inst.tenant.name}</div>
          <div className="truncate text-subsection font-semibold tracking-tight">{inst.items[0]?.name ?? inst.reference ?? t('client.installment', 'Bo\'lib to\'lash')}</div>
        </div>
        <StatusBadge status={inst.status} />
      </header>

      {/* ── Dark summary hero ─────────────────────────────────────────── */}
      <section className="mt-4 rounded-card bg-ink p-6 text-white">
        <div className="flex items-start justify-between gap-4">
          <div>
            <div className="text-micro font-medium uppercase tracking-kicker text-white/55">
              {t('client.remaining', 'Qarz qoldiq')}
            </div>
            <div className="mt-1.5 flex items-baseline gap-1.5 font-mono leading-none">
              <span className="h-display tabular-nums">{money(inst.remaining)}</span>
              <span className="text-small text-white/55">{currency}</span>
            </div>
          </div>
          <ProgressRing paid={inst.paid_count} total={inst.months} size={64} stroke={5} light />
        </div>

        <div className="mt-5 h-px bg-white/10" />

        <div className="mt-4 grid grid-cols-3 gap-3">
          <HeroStat label={t('client.paid', 'To\'langan')} value={money(inst.paid_amount)} />
          <HeroStat label={t('client.monthly', 'Oylik')} value={money(inst.monthly_amount)} />
          <HeroStat label={t('client.endsOn', 'Tugaydi')} value={endsOn ?? '—'} mono={!!endsOn} />
        </div>
      </section>

      {/* ── Items ────────────────────────────────────────────────────── */}
      <section className="mt-5">
        <h2 className="mb-2 text-caption font-semibold uppercase tracking-kicker text-muted">{t('client.items', 'Mahsulotlar')}</h2>
        <ul className="divide-y divide-line rounded-card border border-line bg-surface">
          {inst.items.map((it, idx) => (
            <li key={idx} className="px-4 py-3">
              <div className="flex items-center justify-between gap-3">
                <div className="min-w-0">
                  <div className="truncate text-body font-medium">{it.name}</div>
                  <div className="text-micro text-muted">
                    {[it.variant, it.serial].filter(Boolean).join(' · ') || '—'}
                  </div>
                </div>
                <div className="text-right">
                  <div className="text-micro text-muted">{it.qty}× {money(it.unit_price)}</div>
                  <div className="font-semibold tabular-nums">{money(it.line_total)}</div>
                </div>
              </div>
            </li>
          ))}
        </ul>
      </section>

      {/* ── Shop contact (where to pay cash) ─────────────────────────── */}
      {(inst.tenant.address || inst.tenant.phone || inst.tenant.telegram || inst.tenant.hours) && (
        <section className="mt-5 rounded-card border border-line bg-surface p-4">
          <h2 className="mb-2 text-caption font-semibold uppercase tracking-kicker text-muted">{t('client.shopContact', 'Do\'kon ma\'lumotlari')}</h2>
          <div className="space-y-2 text-body">
            {inst.tenant.address && (
              <div className="flex items-start gap-2"><MapPin size={16} className="mt-0.5 shrink-0 text-muted" /><span>{inst.tenant.address}</span></div>
            )}
            {(inst.tenant.hours?.start || inst.tenant.hours?.end) && (
              <div className="flex items-center gap-2"><Clock size={16} className="shrink-0 text-muted" /><span className="tabular-nums">{inst.tenant.hours?.start ?? '—'} – {inst.tenant.hours?.end ?? '—'}</span></div>
            )}
            {inst.tenant.phone && (
              <a href={`tel:${inst.tenant.phone}`} className="flex min-w-0 items-center gap-2 text-accent"><Phone size={16} className="shrink-0" /><span className="truncate tabular-nums">{displayPhone(inst.tenant.phone)}</span></a>
            )}
            {inst.tenant.telegram && (
              <a href={`https://t.me/${inst.tenant.telegram}`} target="_blank" rel="noreferrer" className="flex min-w-0 items-center gap-2 text-accent"><Send size={16} className="shrink-0" /><span className="truncate">@{inst.tenant.telegram}</span></a>
            )}
          </div>
        </section>
      )}

      {/* ── Schedule ─────────────────────────────────────────────────── */}
      <section className="mt-5">
        <h2 className="mb-2 text-caption font-semibold uppercase tracking-kicker text-muted">{t('client.schedule', 'To\'lov jadvali')}</h2>
        <ul className="divide-y divide-line rounded-card border border-line bg-surface">
          {inst.schedule.map((row) => {
            const isPaid = row.status === 'paid';
            const isNext = row.status === 'next';
            // (#383) Overdue gets danger treatment distinct from the accent
            // "next", and the actionable row taps straight through to /pay
            // instead of forcing a scroll to the bottom buttons.
            const isOverdue = isNext && inst.headline?.kind === 'overdue';
            const inner = (
              <>
                <span className={cn(
                  'grid h-8 w-8 shrink-0 place-items-center rounded-pill font-mono text-caption font-bold',
                  isPaid ? 'bg-success text-white' : isOverdue ? 'bg-danger text-white' : isNext ? 'bg-accent text-white' : 'bg-cream text-muted',
                )}>
                  {isPaid ? <Check size={16} /> : row.n}
                </span>
                <div className="min-w-0 flex-1">
                  <div className="text-caption font-semibold">{row.due_date ?? '—'}</div>
                  {isNext && inst.headline && (
                    <div className={cn('mt-0.5 text-caption font-semibold', isOverdue ? 'text-danger' : 'text-accent')}>{headlineLine(inst.headline, t)}</div>
                  )}
                </div>
                <span className={cn('font-mono text-caption tabular-nums', isPaid ? 'text-muted' : isOverdue ? 'text-danger' : 'text-ink')}>
                  {money(row.amount)}<span className="ml-1 text-micro text-muted">{currency}</span>
                </span>
                {isNext && <ChevronRight size={16} className="shrink-0 text-muted" />}
              </>
            );
            return (
              <li key={row.n}>
                {isNext ? (
                  <button type="button"
                          onClick={() => nav(`/${locale}/me/installments/${inst.id}/pay`)}
                          className="flex w-full items-center gap-3 px-4 py-3 text-left transition hover:bg-ink/5">
                    {inner}
                  </button>
                ) : (
                  <div className="flex items-center gap-3 px-4 py-3">{inner}</div>
                )}
              </li>
            );
          })}
        </ul>
      </section>

      {/* The third state — sent, awaiting the shop's confirmation — must be as
          explicit as paid and due, or a customer who just sent a receipt cannot
          tell whether it counted. */}
      {pendingPayments.length > 0 && (
        <section role="status" aria-live="polite"
                 className="mt-5 rounded-card border border-warn/30 bg-[color:var(--c-warn-soft)] px-4 py-3">
          <div className="text-body font-semibold text-ink">
            {t('client.awaitingConfirmTitle', {
              count: pendingPayments.length,
              amount: `${money(pendingSum)} ${currency}`,
              defaultValue: '{{amount}} tasdiqlanishini kutmoqda',
            })}
          </div>
          <p className="mt-0.5 text-caption text-ink-soft">
            {t('client.awaitingConfirmBody',
               "Do'kon chekni tekshirmoqda. Tasdiqlangach, qarz qoldig'i yangilanadi — qayta to'lash shart emas.")}
          </p>
        </section>
      )}

      {/* ── Payment history ──────────────────────────────────────────── */}
      {inst.payments.length > 0 && (
        <section className="mt-5">
          <h2 className="mb-2 text-caption font-semibold uppercase tracking-kicker text-muted">{t('client.paymentsHistory', 'To\'lov tarixi')}</h2>
          <ul className="divide-y divide-line rounded-card border border-line bg-surface">
            {inst.payments.map((p) => (
              <li key={p.id} className="flex items-center justify-between gap-3 px-4 py-2.5">
                <div className="text-caption">
                  <div>{shortDate(p.paid_at)}</div>
                  <div className="text-micro text-muted">{paymentMethodLabel(t, p.method)}</div>
                </div>
                <div className="flex items-center gap-2">
                  <div className="flex flex-col items-end gap-1">
                    <div className="font-semibold tabular-nums">{money(p.amount)}</div>
                    <StatusBadge status={p.status} />
                  </div>
                  {p.status === 'approved' && (
                    <ReceiptPdfButton paymentId={p.id} amountLabel={money(p.amount)} />
                  )}
                </div>
              </li>
            ))}
          </ul>
        </section>
      )}

      {/* ── Actions ──────────────────────────────────────────────────── */}
      <div className="mt-6 grid gap-2">
        {inst.status !== 'completed' && (
          <>
            <Button variant="primary" size="lg" block onClick={() => nav(`/${locale}/me/installments/${inst.id}/pay`)}>
              <CreditCard size={16} /> {t('client.sendReceiptAmount', { amount: `${money(inst.monthly_amount)} ${currency}`, defaultValue: 'To\'lov chekni yuborish · {{amount}}' })}
            </Button>
            <Button
              variant="outline"
              size="lg"
              block
              className="border-success/40 bg-success/10 text-success hover:bg-success/15"
              onClick={() => nav(`/${locale}/me/installments/${inst.id}/payoff`)}
            >
              <TrendingDown size={16} /> {t('client.payoffShort', 'Hammasini yopish')}
            </Button>
          </>
        )}
        {/* One element across all four states — the label and glyph change, the
            node does not, so focus survives a state change. `block` already
            fixes the width, so no state can move anything below it. */}
        <Button
          variant="outline"
          size="lg"
          block
          loading={contract.busy}
          onClick={() => { void contract.send(); }}
        >
          {/* `Button loading` supplies its own spinner; a second glyph beside it
              would be two things spinning for one action. */}
          {!contract.busy && <ContractIcon size={14} aria-hidden="true" />}
          {contractLabel}
        </Button>
        {/* Scoped to this action, empty until there is something to say. */}
        <span role="status" aria-live="polite" className="sr-only">
          {contract.state === 'idle' ? '' : contractLabel}
        </span>
      </div>
    </div>
  );
}

function headlineLine(
  headline: NonNullable<import('@/lib/queries').MyInstallmentHeadline>,
  t: (k: string, o?: Record<string, unknown>) => string,
): string {
  if (headline.kind === 'overdue') return t('client.overdueDays', { n: headline.days ?? 0 });
  if (headline.kind === 'due_today') return t('client.dueTodayMsg', { defaultValue: 'Bugun to\'lash kerak' });
  if (headline.kind === 'upcoming') return t('client.dueInDays', { n: headline.days ?? 0 });
  return '';
}

function HeroStat({ label, value, mono = true }: { label: string; value: string; mono?: boolean }) {
  return (
    <div className="min-w-0">
      <div className="text-micro uppercase tracking-wider text-white/50">{label}</div>
      <div className={cn('mt-1 truncate text-body font-semibold', mono && 'font-mono tabular-nums')}>{value}</div>
    </div>
  );
}

/**
 * (#10) Both PDF requests on this page used to fetch into a `blob:` URL and hand
 * it to `openExternalLink`, which forwards to `Telegram.WebApp.openLink`.
 * Telegram will not hand a `blob:` origin to the system browser, so for the
 * customers this screen exists for — who are in the Mini App — the button did
 * nothing at all, silently. The server now signs a short-lived https URL that
 * Telegram can actually open, and the blob path survives only as the desktop
 * fallback inside `downloadGranted`.
 *
 * (LX-01) Both requests go through `useDocumentSend` — the contract above, each
 * receipt inside `ReceiptPdfButton` — so the delivery architecture is unchanged
 * and only the feedback on the control is new.
 */
type InstallmentDetailData = NonNullable<ReturnType<typeof useMyInstallment>['data']>;
