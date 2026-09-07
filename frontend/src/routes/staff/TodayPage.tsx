import { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { PageHeader } from '@/components/ui/PageHeader';
import { CheckCircle2, Wallet } from 'lucide-react';
import { EmptyState } from '@/components/ui/EmptyState';
import { FloatingCreateAction } from '@/components/ui/FloatingCreateAction';
import { KpiCard } from '@/components/ui/KpiCard';
import { SkeletonKpis, SkeletonList } from '@/components/ui/Skeleton';
import { ErrorCard } from '@/components/ui/ErrorCard';
import { Button } from '@/components/ui/Button';
import { StatusBadge } from '@/components/ui/Badge';
import { MobileHeader, StatTile } from '@/components/mobile/primitives';
import { useStaffToday, useTenantPayments, useTenantWorklistPages } from '@/lib/queries';
import { money, paymentMethodLabel, shortDate } from '@/lib/format';
import { rowCurrency, useCurrency } from '@/lib/currency';
import { secondaryCurrencyHint } from '@/lib/secondaryCurrency';
import { isRowOverdue, overdueDays } from '@/lib/overdue';
import { InstallmentModal } from '../tenant/_modals/InstallmentModal';
import { InstallmentDetailModal } from '../tenant/InstallmentsPage';

export default function StaffTodayPage() {
  const { t } = useTranslation();
  const currency = useCurrency();
  const [saleOpen, setSaleOpen] = useState(false);
  const todayQ = useStaffToday();
  const s = todayQ.data;
  const payments = useTenantPayments();
  const recent = payments.data?.items.slice(0, 5) ?? [];

  // The clerk's actual job: contracts due today or already overdue. Tapping a
  // row opens the contract (with record-payment) — an action queue, not a feed.
  const [detailId, setDetailId] = useState<string | null>(null);
  // Set only by the post-creation hand-off, so a control labelled
  // "record a payment" lands the user on the payment form itself.
  const [payForId, setPayForId] = useState<string | null>(null);
  // Server-computed over the whole tenant, and PAGED rather than truncated: the
  // heading counts the true total, so a first-page-only list would silently end
  // while insisting the rest existed. This is a person's whole day of work.
  const worklistQ = useTenantWorklistPages(20);
  const worklist = worklistQ.data?.pages.flatMap((p) => p.items) ?? [];
  const worklistTotal = worklistQ.data?.pages[0]?.total ?? 0;

  // (MUDD-114) `received_today` is only the PRIMARY-currency share of what
  // this salesperson collected today — a shift that also took a second
  // currency must say so, the same rule the tenant Overview KPIs already
  // follow, or a clerk reconciling a cash drawer would silently miss it.
  const receivedSecondary = secondaryCurrencyHint(s?.received_today_by_currency, s?.currency ?? currency);

  return (
    <>
      <div className="hidden md:block">
        <PageHeader title={t('common.today')} />
      </div>

      <FloatingCreateAction onClick={() => setSaleOpen(true)} label={t('common.newSale')} />

      {!todayQ.isLoading && todayQ.isError && (
        <div className="mb-6">
          <ErrorCard error={todayQ.error} onRetry={() => todayQ.refetch()} />
        </div>
      )}

      {/* ── Mobile: dark received hero + stat tiles (mockup-style) ───── */}
      {!todayQ.isError && <div className="md:hidden">
        <MobileHeader title={t('common.today')} />
        <p className="mb-3 text-small leading-snug text-muted">
          {t('staffToday.worklistSub', { count: worklistTotal, defaultValue: "{{count}} ta yig'ish kerak" })}
        </p>
        <div className="rounded-card bg-ink p-6 text-white">
          <div className="text-micro font-medium uppercase tracking-kicker text-white/55">{t('staffToday.received')}</div>
          <div className="mt-1.5 flex items-baseline gap-1.5 font-mono leading-none">
            <span className="h-display tabular-nums">{money(s?.received_today ?? 0)}</span>
            <span className="text-small text-white/55">{s?.currency ?? currency}</span>
          </div>
          {receivedSecondary && (
            <div className="mt-1 text-caption text-white/55">{receivedSecondary}</div>
          )}
        </div>
        <div className="mt-3 grid grid-cols-2 gap-3">
          <StatTile dot="bg-warn"    label={t('staffToday.toApprove')} value={String(s?.pending_count ?? 0)} />
          <StatTile dot="bg-success" label={t('staffToday.customersLabel')} value={String(s?.new_customers_today ?? 0)} />
        </div>
      </div>}

      {todayQ.isLoading ? (
        <div className="hidden md:block"><SkeletonKpis count={3} /></div>
      ) : !todayQ.isError ? (
      <section className="hidden grid-cols-1 gap-4 sm:grid-cols-3 md:grid">
        <KpiCard label={t('staffToday.receivedLabel')}  value={s?.received_today ?? 0} unit={s?.currency ?? currency}
                 hint={receivedSecondary ?? t('staffToday.receivedHint')}  tone="success" />
        <KpiCard label={t('staffToday.toApprove')}      value={s?.pending_count ?? 0}                               hint={t('staffToday.pendingHint')}   tone="warn" />
        <KpiCard label={t('staffToday.customersLabel')} value={s?.new_customers_today ?? 0}                         hint={t('staffToday.customersHint')} tone="accent" />
      </section>
      ) : null}

      {/* ── Worklist: due today + overdue (the clerk's actual job) ───── */}
      <section className="mt-6 card p-0 overflow-hidden">
        <div className="flex items-center justify-between border-b border-line px-5 py-4">
          <h3 className="text-subsection font-semibold">{t('staffToday.worklistTitle', { defaultValue: "Bugun yig'ish kerak" })}</h3>
          <span className="text-micro uppercase tracking-wider text-muted">{t('staffToday.worklistHint', { defaultValue: 'Muddati bugun yoki kechikkan' })}</span>
        </div>
        {worklistQ.isLoading && <div className="px-5 py-4"><SkeletonList rows={3} /></div>}
        {/* (#406) A failed worklist fetch must show an explicit error+retry, not
            the "nothing to collect today" empty copy (error-as-empty). */}
        {!worklistQ.isLoading && worklistQ.isError && (
          <div className="px-6 py-8"><ErrorCard error={worklistQ.error} onRetry={() => worklistQ.refetch()} /></div>
        )}
        {!worklistQ.isLoading && !worklistQ.isError && worklist.length === 0 && (
          <EmptyState size="compact" icon={<CheckCircle2 size={20} />}
                      title={t('staffToday.worklistEmpty', { defaultValue: "Bugun yig'ish kerak shartnoma yo'q" })} />
        )}
        <ul>
          {worklist.map((i) => {
            const remaining = Math.max(0, i.principal - i.paid_amount);
            // Server-derived, against the tenant's today.
            const od = overdueDays(i, worklistQ.data?.pages[0]?.as_of_date);
            return (
              <li key={i.id}>
                <button type="button" onClick={() => setDetailId(i.id)}
                        className="flex w-full items-center gap-3 border-b border-line px-5 py-3 text-left transition last:border-0 hover:bg-cream">
                  <div className="min-w-0 flex-1">
                    <div className="truncate text-body font-medium">{i.customer_name ?? i.contract_number ?? i.id.slice(0, 8)}</div>
                    <div className="truncate text-caption text-muted">
                      {money(i.monthly_amount)} {rowCurrency(i, currency)} · {shortDate(i.next_due_date)}
                      {od > 0 && <span className="font-medium text-[color:var(--c-danger-strong)]"> · {t('installments.overdueDays', { n: od, defaultValue: `${od} kun kechikkan` })}</span>}
                    </div>
                  </div>
                  <StatusBadge status={isRowOverdue(i) ? 'overdue' : i.status} />
                  <span className="hidden tabular-nums text-caption text-muted sm:block">{money(remaining)}</span>
                </button>
              </li>
            );
          })}
        </ul>
        {/* Every remaining row is reachable — no silent truncation. */}
        {worklistQ.hasNextPage && (
          <div className="border-t border-line px-5 py-3 text-center">
            <Button variant="outline" size="md"
                    disabled={worklistQ.isFetchingNextPage}
                    onClick={() => void worklistQ.fetchNextPage()}>
              {worklistQ.isFetchingNextPage
                ? t('common.loading')
                : t('common.loadMore', {
                    shown: worklist.length, total: worklistTotal,
                    defaultValue: 'Yana yuklash ({{shown}}/{{total}})',
                  })}
            </Button>
          </div>
        )}
      </section>

      {/* Recent confirmed payments (read-only context) */}
      <section className="mt-6 card p-0 overflow-hidden">
        <div className="flex items-center justify-between border-b border-line px-5 py-4">
          <h3 className="text-subsection font-semibold">{t('staffToday.recent')}</h3>
          <span className="text-micro uppercase tracking-wider text-muted">{t('staffToday.recentHint')}</span>
        </div>
        {payments.isLoading && <div className="px-5 py-4"><SkeletonList rows={3} /></div>}
        {/* (#321) Distinguish a failed payments fetch from a genuinely quiet day. */}
        {!payments.isLoading && payments.isError && (
          <div className="px-6 py-8"><ErrorCard error={payments.error} onRetry={() => payments.refetch()} /></div>
        )}
        {!payments.isLoading && !payments.isError && recent.length === 0 && (
          <EmptyState size="compact" icon={<Wallet size={20} />} title={t('staffToday.empty')} />
        )}
        <ul>
          {recent.map((p) => (
            <li key={p.id}>
              {/* Clickable row → opens the payment's contract, matching the worklist
                  above and the tenant Overview "recent payments" pattern. */}
              <button type="button" onClick={() => setDetailId(p.installment_id)}
                      className="flex w-full items-center gap-3 border-b border-line px-5 py-3 text-left transition last:border-0 hover:bg-cream">
                <div className="min-w-0 flex-1">
                  <div className="truncate text-body font-medium">{p.customer_name ?? '—'}</div>
                  <div className="truncate text-caption text-muted">{paymentMethodLabel(t, p.method)} · {shortDate(p.created_at)}</div>
                </div>
                <span className="tabular-nums text-body">{money(p.amount)} {rowCurrency(p, currency)}</span>
                <StatusBadge status={p.status} />
              </button>
            </li>
          ))}
        </ul>
      </section>

      <InstallmentModal open={saleOpen} onClose={() => setSaleOpen(false)}
                        onRecordPayment={(id) => { setDetailId(id); setPayForId(id); }} />
      <InstallmentDetailModal id={detailId} openPayment={!!payForId && payForId === detailId}
                              onClose={() => { setDetailId(null); setPayForId(null); }} />
    </>
  );
}
