import { useEffect, useRef, useState } from 'react';
import { greetingFor, CLIENT_GREETING } from '@/lib/greeting';
import { PhoneVerificationCard } from '@/components/ui/PhoneVerificationCard';
import { createPortal } from 'react-dom';
import { useTranslation } from 'react-i18next';
import { useNavigate } from 'react-router';
import { AlertTriangle, ArrowLeft, Bell, BellOff, ChevronRight, PackageOpen, RefreshCw } from 'lucide-react';
import { Skeleton, SkeletonList } from '@/components/ui/Skeleton';
import { useFocusTrap } from '@/hooks/useFocusTrap';
import { V2_SURFACES_ENABLED } from '@/routes/_scope';
import { StatusBadge } from '@/components/ui/Badge';
import { ErrorCard } from '@/components/ui/ErrorCard';
import { EmptyState } from '@/components/ui/EmptyState';
import { useSession } from '@/store/session';
import {
  useMyIdentity, useMyInstallments, useMySummary, useMyNotifications,
  type MyInstallmentRow, type NotificationRow,
} from '@/lib/queries';
import { useMarkNotificationsRead } from '@/lib/mutations';
import { money } from '@/lib/format';
import { cn } from '@/lib/cn';
import { ProgressRing, SegmentBar } from '@/components/mobile/primitives';
import { DEFAULT_CURRENCY } from '@/lib/currency';

/** Mijoz · Bosh sahifa — real /me/summary + /me/installments dan.
 *
 *  Cash-app style mobile layout (≤ 420px). The orange hero answers the one
 *  question a customer has — "what do I owe next?" — with the term ring and
 *  product context; the list below mirrors the design mockup with segmented
 *  progress bars and status pills.
 */
export default function ClientHomePage() {
  const { t, i18n } = useTranslation();
  const me = useSession((s) => s.me);
  const summary = useMySummary();
  const identity = useMyIdentity();
  const open = useMyInstallments('open');
  const completed = useMyInstallments('completed');
  const locale = (i18n.language?.startsWith('ru') ? 'ru' : 'uz') as 'uz' | 'ru';
  const nav = useNavigate();

  const s = summary.data;
  const firstOpen = (open.data ?? [])[0];
  const hasOpen = !!(open.data?.length);
  const heroCurrency = s?.next_due_currency ?? firstOpen?.currency ?? DEFAULT_CURRENCY;
  const notifs = useMyNotifications();
  const [notifOpen, setNotifOpen] = useState(false);

  // Hero state: overdue vs. due today vs. upcoming vs. all clear.
  //
  // (QA H-2) "All clear" additionally requires nothing left outstanding. It
  // used to be `due_today === '0' && !next_due_date`, and the server returned a
  // null date for anyone whose due date had already PASSED — so the one group
  // guaranteed to see "all your payments are done" was the customers who were
  // late. The balance check makes the celebratory state impossible to reach
  // while money is owed, whatever the dates say.
  const isOverdue = !!s && (s.next_due_overdue ?? false);
  // Due-today in ANY currency, not just the dominant one `s.due_today` is
  // denominated in — a customer owing today only at a USD shop must still see
  // this, even if most of their contracts (and so the dominant currency) are
  // UZS. Falls back to the old scalar check for a cached response that
  // predates the per-currency map.
  const isDueToday = !!s && (
    s.due_today_by_currency
      ? Object.keys(s.due_today_by_currency).length > 0
      : s.due_today !== '0'
  );
  // Same currency-blind check as `isDueToday`: the dominant currency is now
  // picked by contract count, not by amount, so a currency with a real
  // balance can sit behind a dominant currency that itself owes nothing.
  const owesNothing = !s || (
    s.total_outstanding_by_currency
      ? Object.keys(s.total_outstanding_by_currency).length === 0
      : Number(s.total_outstanding) <= 0
  );
  const isAllClear = !!s && !isDueToday && !s.next_due_date && owesNothing;

  // This is one actionable payment, not a sum across shops whose contracts may
  // use different currencies.
  const heroAmount = s ? money(s.next_due_amount) : '0';
  const heroSubtitle = s
    ? (isOverdue
        ? t('client.overdueSince', { date: s.next_due_date })
        : isDueToday
          ? t('client.dueTodayMsg', 'Bugun to\'lash kerak')
          : s.next_due_date
            ? t('client.nextDueDateMsg', { date: s.next_due_date })
            : t('client.noPaymentNow'))
    : '';

  return (
    <div className="mx-auto w-full max-w-panel">
      <header className="flex items-center justify-between gap-3">
        <div className="min-w-0">
          <div className="text-caption text-muted">{firstOpen?.tenant_name ?? 'Muddato'}</div>
          {/* The greeting is what this screen is titled, visually and to AT. */}
          <h1 className="mt-0.5 truncate text-title font-semibold tracking-tight">
            {greetingFor(t, me, CLIENT_GREETING)}
          </h1>
        </div>
        {/* Deferred: an in-app notifications sheet duplicates the Telegram chat
            the customer already reads — two inboxes for the same messages. */}
        {V2_SURFACES_ENABLED && (
        <button onClick={() => setNotifOpen(true)}
                className="tap-target relative grid h-11 w-11 shrink-0 place-items-center rounded-control border border-line text-ink-soft hover:bg-ink/5"
                aria-label={t('client.notifications', 'Bildirishnomalar')}>
          <Bell size={18} />
          {(notifs.data?.unread ?? 0) > 0 && (
            <span className="absolute -right-1 -top-1 grid h-4 min-w-4 place-items-center rounded-pill bg-danger px-1 text-micro font-bold text-white">
              {notifs.data!.unread > 9 ? '9+' : notifs.data!.unread}
            </span>
          )}
        </button>
        )}
      </header>

      {/* Proving the phone comes before anything a shop can sell to: an
          unverified person cannot be activated as a customer. Hidden entirely
          once the server says it is settled. */}
      {identity.data && !identity.data.verified && (
        <div className="mt-4">
          <PhoneVerificationCard
            snapshot={identity.data}
            onRefetch={async () => (await identity.refetch()).data ?? null}
          />
        </div>
      )}

      {V2_SURFACES_ENABLED && notifOpen && <NotificationsSheet onClose={() => setNotifOpen(false)} />}

      {/* ── Next-payment hero ─────────────────────────────────────────── */}
      <section className="mt-5 overflow-hidden rounded-card bg-accent p-6 text-white shadow-pop">
        <div className="text-micro font-medium uppercase tracking-kicker text-white/70">
          {isOverdue ? t('client.overduePayment') : t('client.nextPayment', 'Keyingi to\'lov')}
        </div>

        {/* (#365) Hero-shaped skeleton instead of a spinner — the amount and
            subtitle land without a layout jump. White-tinted to read on the
            accent background. */}
        {summary.isLoading && (
          <div className="mt-4 space-y-3" role="status" aria-busy="true">
            <Skeleton className="h-10 w-2/3 bg-white/25" />
            <Skeleton className="h-3.5 w-1/2 bg-white/20" />
          </div>
        )}

        {summary.isError && !summary.isLoading && (
          <div className="mt-4">
            <p className="text-body text-white/90">{t('common.loadFailed')}</p>
            <button
              onClick={() => { summary.refetch(); open.refetch(); }}
              className="mt-3 inline-flex items-center gap-1.5 rounded-pill bg-white/20 px-4 py-2 text-small font-medium text-white hover:bg-white/30"
            >
              <RefreshCw size={14} /> {t('common.retry')}
            </button>
          </div>
        )}

        {s && !summary.isError && (
          <>
            {/* All-clear empty state: nothing due now, no future payments */}
            {isAllClear ? (
              <div className="mt-4 text-center">
                <div className="text-small font-medium text-white">{heroSubtitle}</div>
                <div className="mt-3 text-micro text-white/70">
                  {t('client.allPaidUp', 'Barcha to\'lovlaringiz to\'langan')}
                </div>
              </div>
            ) : (
              <>
                {/* Normal/future payment state: show amount + details */}
                <div className="mt-2 flex items-baseline gap-1.5 font-mono leading-none">
                  <span className="h-display tabular-nums">{heroAmount}</span>
                  <span className="text-body text-white/70">{heroCurrency}</span>
                </div>
                <div className="mt-2 text-small text-white/85">{heroSubtitle}</div>

                {s.overdue_count > 0 && (
                  <div className="mt-3 inline-flex items-center gap-1.5 rounded-pill bg-white/15 px-3 py-1 text-caption font-medium">
                    <AlertTriangle size={12} /> {t('client.overdueCount', { n: s.overdue_count })}
                  </div>
                )}

                {firstOpen && (
                  <div className="mt-5 flex items-center gap-3.5">
                    <ProgressRing paid={firstOpen.paid_count} total={firstOpen.months} size={56} light />
                    <div className="min-w-0 flex-1 text-micro leading-snug text-white/85">
                      <div className="truncate">
                        {firstOpen.item_summary} · {t('client.monthsTerm', { n: firstOpen.months, defaultValue: '{{n}} oylik' })}
                      </div>
                      <div className="text-white/65">
                        {t('client.remainingShort', 'Qolgan')}: {money(firstOpen.remaining)} {firstOpen.currency}
                      </div>
                    </div>
                  </div>
                )}

                {hasOpen && (
                  <button
                    onClick={() => firstOpen && nav(`/${locale}/me/installments/${firstOpen.id}/pay`)}
                    className="mt-5 w-full rounded-pill bg-white py-3 text-body font-semibold text-accent"
                  >
                    {t('client.payNow', 'To\'lash')}
                  </button>
                )}
              </>
            )}
          </>
        )}
      </section>

      {/* ── Open installments ────────────────────────────────────────── */}
      <section className="mt-7">
        <div className="mb-3 flex items-center justify-between">
          <h2 className="text-caption font-semibold uppercase tracking-kicker text-muted">
            {t('client.myInstallments', 'Bo\'lib to\'lashlarim')}
          </h2>
          {/* Never show a count on a failed fetch — "0" would read as "no debt",
              which is exactly the wrong impression to give on a fetch error. */}
          {!open.isError && (
            <span className="text-micro text-muted">
              {t('common.countItems', { n: open.data?.length ?? 0 })}
            </span>
          )}
        </div>

        {open.isLoading && <SkeletonList rows={2} />}
        {/* A failed fetch must never be mistaken for "nothing owed" — show the
            error explicitly instead of silently falling through to an empty list. */}
        {open.isError && <ErrorCard error={open.error} onRetry={() => open.refetch()} />}
        {!open.isLoading && !open.isError && open.data && open.data.length === 0 && (
          <EmptyState
            size="compact"
            icon={<PackageOpen size={20} />}
            title={t('client.noInstallmentsYet', 'Hozircha aktiv kreditingiz yo\'q.')}
          />
        )}

        {!open.isError && (
          <ul className="space-y-2.5">
            {(open.data ?? []).map((inst) => (
              <InstallmentCard key={inst.id} inst={inst} locale={locale} />
            ))}
          </ul>
        )}
      </section>

      {/* ── Completed ────────────────────────────────────────────────── */}
      {completed.data && completed.data.length > 0 && (
        <section className="mt-7">
          <h2 className="mb-3 text-caption font-semibold uppercase tracking-kicker text-muted">
            {t('client.completed', 'Yopilgan kreditlar')}
          </h2>
          <ul className="space-y-2.5">
            {completed.data.slice(0, 5).map((inst) => (
              <InstallmentCard key={inst.id} inst={inst} locale={locale} />
            ))}
          </ul>
        </section>
      )}
    </div>
  );
}

function InstallmentCard({
  inst, locale,
}: { inst: MyInstallmentRow; locale: 'uz' | 'ru' }) {
  const { t } = useTranslation();
  const nav = useNavigate();
  const overdue = inst.status === 'overdue';

  const isOpen = inst.status === 'active' || inst.status === 'overdue';
  return (
    <li className="rounded-card border border-line bg-surface p-4">
      <button
        onClick={() => nav(`/${locale}/me/installments/${inst.id}`)}
        className="block w-full text-left"
      >
        <div className="flex items-center justify-between gap-3">
          <div className="truncate text-body font-semibold tracking-tight">{inst.item_summary}</div>
          <StatusBadge status={inst.status} />
        </div>

        <div className="mt-3">
          <SegmentBar paid={inst.paid_count} total={inst.months} tone={overdue ? 'danger' : 'ink'} />
        </div>

        <div className="mt-2.5 flex items-center justify-between text-micro text-muted">
          <span className="tabular-nums">
            {inst.paid_count}/{inst.months} · {t('client.remainingShort', 'Qolgan')} {money(inst.remaining)} {inst.currency}
          </span>
          <ChevronRight size={14} className="text-muted/70" />
        </div>
      </button>

      {isOpen && (
        <button
          onClick={() => nav(`/${locale}/me/installments/${inst.id}/pay`)}
          className={cn('mt-3 w-full rounded-pill py-2.5 text-small font-semibold text-white',
            overdue ? 'bg-danger' : 'bg-accent')}
        >
          {t('client.payNow', 'To\'lash')}
        </button>
      )}
    </li>
  );
}

/** Full-screen notifications list. Marks everything read on open so the bell
 *  badge clears, but keeps the items visible. */
function NotificationsSheet({ onClose }: { onClose: () => void }) {
  const { t } = useTranslation();
  const nav = useNavigate();
  // (§8) This is a full-screen overlay and was the one in the product that
  // opted out of the rules the others follow: it never portalled, never
  // trapped focus, and could not be dismissed with Escape — so a keyboard or
  // screen-reader user could tab straight out of it into the page underneath
  // while it still covered the screen. `useFocusTrap` is the same hook Modal,
  // DetailDrawer and RowActionMenu use, and it wires Escape too.
  const panelRef = useRef<HTMLDivElement>(null);
  useFocusTrap(panelRef, true, onClose);
  const q = useMyNotifications();
  const markRead = useMarkNotificationsRead();

  useEffect(() => {
    const prev = document.body.style.overflow;
    document.body.style.overflow = 'hidden';
    if ((q.data?.unread ?? 0) > 0) markRead.mutate();
    return () => { document.body.style.overflow = prev; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [q.data?.unread]);

  const items = q.data?.items ?? [];
  // Portalled to <body> for the same reason as every other overlay: rendered in
  // place, an ancestor's transform or overflow can clip a `fixed` child.
  return createPortal((
    <div ref={panelRef} role="dialog" aria-modal="true"
         aria-label={t('client.notifications', 'Bildirishnomalar')}
         className="fixed inset-0 z-[60] flex flex-col bg-cream"
         style={{ paddingTop: 'max(0.75rem, env(safe-area-inset-top))' }}>
      <div className="flex items-center gap-2 border-b border-line bg-surface px-4 pb-3 pt-1">
        <button onClick={onClose} className="tap-target grid h-11 w-11 place-items-center rounded-control text-ink-soft hover:bg-ink/5" aria-label={t('client.back', 'Orqaga')}>
          <ArrowLeft size={18} />
        </button>
        <span className="text-subsection font-semibold">{t('client.notifications', 'Bildirishnomalar')}</span>
      </div>
      <div className="flex-1 overflow-y-auto p-4 pb-[max(16px,env(safe-area-inset-bottom))]">
        {q.isLoading && <SkeletonList rows={3} />}
        {q.isError && <ErrorCard error={q.error} onRetry={() => q.refetch()} />}
        {!q.isLoading && !q.isError && items.length === 0 && (
          <EmptyState
            size="compact"
            icon={<BellOff size={20} />}
            title={t('client.noNotifications', 'Hozircha bildirishnoma yo‘q')}
          />
        )}
        {!q.isError && (
          <ul className="space-y-2">
            {items.map((n) => <NotifItem key={n.id} n={n} onNav={(link) => { onClose(); nav(link); }} />)}
          </ul>
        )}
      </div>
    </div>
  ), document.body);
}

function NotifItem({ n, onNav }: { n: NotificationRow; onNav: (link: string) => void }) {
  const tappable = !!n.link && n.link.startsWith('/');
  const body = (
    <>
      <div className="flex items-start justify-between gap-2">
        <span className="text-body font-semibold">{n.title}</span>
        {!n.read && <span className="mt-1 h-2 w-2 shrink-0 rounded-pill bg-accent" />}
      </div>
      {n.body && <p className="mt-1 whitespace-pre-line text-small text-ink-soft">{n.body.replace(/<[^>]+>/g, '')}</p>}
      <div className="mt-1 text-micro text-muted">{n.created_at.slice(0, 16).replace('T', ' ')}</div>
    </>
  );
  return (
    <li className="rounded-card border border-line bg-surface p-4">
      {tappable
        ? <button onClick={() => onNav(n.link!)} className="block w-full text-left">{body}</button>
        : body}
    </li>
  );
}
