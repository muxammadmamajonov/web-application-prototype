import { useTranslation } from 'react-i18next';
import { useNavigate, useParams } from 'react-router';
import {
  ArrowUpRight, CheckCircle2, ChevronRight, CreditCard, Inbox, LineChart,
  PieChart, ShieldAlert, ShieldCheck, Store, TrendingUp, Users, Wallet,
} from 'lucide-react';
import { PageHeader } from '@/components/ui/PageHeader';
import { KpiCard } from '@/components/ui/KpiCard';
import { SectionCard } from '@/components/ui/SectionCard';
import { ChartEmptyState } from '@/components/ui/ChartEmptyState';
import { Metric, MetricGrid } from '@/components/ui/Metric';
import { Donut } from '@/components/ui/Donut';
import { Spark } from '@/components/ui/Spark';
import { Button } from '@/components/ui/Button';
import { Badge, type BadgeTone } from '@/components/ui/Badge';
import { ErrorCard } from '@/components/ui/ErrorCard';
import { Skeleton } from '@/components/ui/Skeleton';
import { MobileHeader, StatStrip, type StatStripItem } from '@/components/mobile/primitives';
import { usePlatformOverview, usePlatformRiskStats, usePlatformPayments, type PlatformPaymentRow } from '@/lib/queries';
import { money, shortDate } from '@/lib/format';

const MONTH_KEYS = ['jan', 'feb', 'mar', 'apr', 'may', 'jun', 'jul', 'aug', 'sep', 'oct', 'nov', 'dec'];

/** How severe each published-risk bucket is — drives badge tone + sort order. */
const SEVERITY: Record<string, { tone: BadgeTone; weight: number }> = {
  fraud_reported: { tone: 'danger', weight: 4 },
  charged_off:    { tone: 'danger', weight: 3 },
  late_90:        { tone: 'danger', weight: 3 },
  late_60:        { tone: 'warn',   weight: 2 },
  late_30:        { tone: 'warn',   weight: 1 },
};

/** Platforma admin · Boshqaruv markazi — a single responsive command center
 *  (one card tree from 360px → wide desktop; only the header swaps for Telegram). */
export default function PlatformOverviewPage() {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const { locale } = useParams();
  const lng = locale ?? 'uz';
  const goReview = () => navigate(`/${lng}/platform/payments`);
  const goTenants = () => navigate(`/${lng}/platform/tenants`);
  const currency = t('billing.currency');

  const overview = usePlatformOverview();
  const o = overview.data;
  const payments = usePlatformPayments();
  const pm = payments.data;
  const riskStats = usePlatformRiskStats();
  const rs = riskStats.data;

  // ── Derived view-model ─────────────────────────────────────────────────────
  const revenueSeries = (o?.revenue_series ?? []).map((p) => ({ x: t(`months.${MONTH_KEYS[p.month - 1]}`), y: p.amount }));
  const hasRevenue = revenueSeries.some((p) => p.y > 0);

  const statusSlices = [
    { key: 'active',    label: t('status.active'),    value: o?.tenants_by_status?.active    ?? 0, color: 'var(--c-success)' },
    { key: 'demo',      label: t('status.demo'),      value: o?.tenants_by_status?.demo      ?? 0, color: 'var(--c-accent)' },
    { key: 'suspended', label: t('status.suspended'), value: o?.tenants_by_status?.suspended ?? 0, color: 'var(--c-warn)' },
    { key: 'blocked',   label: t('status.blocked'),   value: o?.tenants_by_status?.blocked   ?? 0, color: 'var(--c-ink)' },
  ];
  const totalTenants = o?.tenants_total ?? 0;
  const activeTenants = o?.tenants_by_status?.active ?? 0;
  const customers = o?.customers_total ?? 0;
  const perStore = activeTenants > 0 ? Math.round(customers / activeTenants) : 0;

  const pending = o?.pending_receipts ?? 0;
  const pendingItems = (pm?.items ?? [])
    .filter((i) => i.status === 'pending')
    .sort((a, b) => b.created_at.localeCompare(a.created_at));
  const pendingTop = pendingItems.slice(0, 4);
  const payingStores = new Set((pm?.items ?? []).filter((i) => i.status === 'approved').map((i) => i.tenant_id)).size;

  const riskTotal = (rs?.total_signals ?? 0) + (rs?.flagged_customers ?? 0);
  const severityEntries = rs
    ? Object.entries(rs.published_by_severity).sort((a, b) => (SEVERITY[b[0]]?.weight ?? 0) - (SEVERITY[a[0]]?.weight ?? 0))
    : [];

  const reviewAction = pending > 0
    ? (
      <Button variant="primary" size="md" className="rounded-pill" onClick={goReview}>
        {t('platformOverview.reviewN', { count: pending })} <ArrowUpRight size={14} />
      </Button>
    )
    : undefined;

  // ── KPI summary — compact StatStrip on phones, full KpiCards from md up.
  //    Money values are preformatted (with the currency suffix) so a 7-digit UZS
  //    sum stays glanceable inside the narrow strip tile.
  const statItems: StatStripItem[] = [
    { key: 'mrr',          label: t('platformOverview.stripMrr', 'Daromad'),       value: `${money(o?.mrr_estimate_uzs ?? 0)} ${currency}`,        tone: 'success', icon: <TrendingUp size={14} />,  loading: overview.isLoading },
    { key: 'approved',     label: t('platformOverview.stripApproved', 'Tushum'),   value: `${money(o?.approved_this_month_uzs ?? 0)} ${currency}`, tone: 'success', icon: <CheckCircle2 size={14} />, loading: overview.isLoading },
    { key: 'installments', label: t('platformOverview.stripInstallments', 'Faol'), value: money(o?.active_installments ?? 0),                      tone: 'accent',  icon: <CreditCard size={14} />,  loading: overview.isLoading },
    { key: 'tenants',      label: t('platformOverview.stripTenants', "Do‘kon"),    value: money(activeTenants),                                    tone: 'default', icon: <Store size={14} />,       loading: overview.isLoading },
  ];

  return (
    <>
      {/* Header — desktop PageHeader / mobile MobileHeader (Telegram-safe). */}
      <div className="hidden md:block">
        <PageHeader title={t('nav.overview')} subtitle={t('platformOverview.controlSub')} actions={reviewAction} />
      </div>
      <div className="md:hidden">
        <MobileHeader title={t('nav.overview')} action={reviewAction} />
        <p className="mb-4 max-w-prose text-small leading-snug text-muted">{t('platformOverview.controlSub')}</p>
      </div>

      {/* A failed fetch with no cached data must never render as a confident
          "0 UZS / 0 do'kon" dashboard — that reads as real, reassuring numbers
          instead of a load failure to a Platform Owner. Show the shared
          ErrorCard in place of the whole KPI/analytics/operations body instead
          of layering a small banner over zeroed-out cards (same fix already
          applied to tenant/OverviewPage.tsx). A background refetch (isError but
          `o` still cached from a prior success) keeps showing last-known values. */}
      {overview.isError && !o ? (
        <ErrorCard error={overview.error} onRetry={() => overview.refetch()} />
      ) : (
      <div className="space-y-4 sm:space-y-5">
        {/* Row 1 — KPIs: compact StatStrip on phones, full KpiCards from md up
            (2-up at md, 4-up only at xl so a big UZS value never gets a clipped
            cell). md breakpoint matches the StatStrip's md:hidden — no overlap. */}
        <div className="md:hidden"><StatStrip items={statItems} /></div>
        <section className="hidden gap-4 md:grid md:grid-cols-2 xl:grid-cols-4">
          {/* `mrrHint` called these "paying businesses" while the figure is the
              count of ACTIVE tenants — MRR is SUM(plan.price_monthly) over
              tenants with status=active, whether or not any of them has ever
              paid. Read next to the cash-flow card's "To'lovchi do'konlar"
              (shops with an approved payment) the dashboard appeared to give two
              different answers for the same thing. The number was right; the
              word was not. The two captions below likewise promised a
              comparison — "vs last month", "vs last week" — that no field in
              this payload can supply; they now describe the figure instead. */}
          <KpiCard label={t('platformOverview.mrrEstimate')} value={o?.mrr_estimate_uzs ?? 0} unit={currency}
                   hint={t('platformOverview.mrrHint', { count: activeTenants })} tone="success"
                   icon={<TrendingUp size={16} />} loading={overview.isLoading} />
          <KpiCard label={t('platformOverview.approvedMonth')} value={o?.approved_this_month_uzs ?? 0} unit={currency}
                   hint={t('platformOverview.approvedHint')} tone="success"
                   icon={<CheckCircle2 size={16} />} loading={overview.isLoading} />
          <KpiCard label={t('platformOverview.activeInstallments')} value={o?.active_installments ?? 0}
                   hint={t('platformOverview.activeHint')} tone="accent"
                   icon={<CreditCard size={16} />} loading={overview.isLoading} />
          <KpiCard label={t('platformOverview.activeTenants')} value={activeTenants}
                   hint={t('platformOverview.ofTotalTenants', { total: totalTenants })} tone="default"
                   icon={<Store size={16} />} loading={overview.isLoading} />
        </section>

        {/* Row 2 — Analytics: revenue (2/3) + tenant status (1/3). */}
        <section className="grid grid-cols-1 gap-4 lg:grid-cols-3">
          <SectionCard
            className="lg:col-span-2"
            icon={<LineChart size={16} />}
            title={t('platformOverview.recurringRevenue')}
            meta={t('platformOverview.last6Months')}
          >
            {overview.isLoading
              ? <Skeleton className="h-[200px] w-full rounded-xl sm:h-[220px]" />
              : hasRevenue
                ? <Spark data={revenueSeries} />
                : <ChartEmptyState icon={<TrendingUp size={24} />}
                                   title={t('platformOverview.revenueEmptyTitle')}
                                   body={t('platformOverview.revenueEmptyBody')}
                                   hint={t('platformOverview.revenueEmptyHint')} />}
          </SectionCard>

          <SectionCard
            icon={<PieChart size={16} />}
            title={t('platformOverview.tenantsByStatus')}
            action={(
              <button onClick={goTenants}
                      className="inline-flex items-center gap-1 rounded-lg px-1.5 py-1 text-caption font-medium text-accent hover:underline">
                {t('platformOverview.viewAllTenants')} <ArrowUpRight size={14} />
              </button>
            )}
          >
            {overview.isLoading ? (
              <div className="flex flex-col items-center gap-4 py-2 sm:flex-row sm:gap-6">
                <Skeleton className="h-36 w-36 shrink-0 rounded-full sm:h-44 sm:w-44" />
                <div className="w-full space-y-2.5">
                  {statusSlices.map((s) => <Skeleton key={s.key} className="h-4 w-full" />)}
                </div>
              </div>
            ) : (
              // Donut renders its own legend — no second legend row beneath it.
              // Stacked (ring above full-width legend) so it fits the 1/3-width card.
              <Donut stacked slices={statusSlices} totalLabel={t('platformOverview.totalLabel')} total={totalTenants} />
            )}
          </SectionCard>
        </section>

        {/* Row 3 — Operations: the approvals worklist beside the stacked financial
            summary (cash flow + coverage). Pairing them this way keeps every money
            cell wide enough that a 7-digit UZS sum is never clipped. */}
        {/* No `lg:items-start`: with it the short approvals card kept its natural
            height beside the taller stacked column, and the leftover half of its
            grid cell showed as a bare block of page background — the widest
            single piece of dead space on the dashboard. Stretching turns that
            into breathing room INSIDE a card, which reads as deliberate. */}
        <section className="grid grid-cols-1 gap-4 lg:grid-cols-2">
          {/* Pending approvals — a real worklist, or a calm success state. */}
          <SectionCard
            icon={<Inbox size={16} />}
            title={t('platformOverview.awaitingApproval')}
            meta={pending > 0 ? t('platformOverview.acrossTenants', { count: o?.pending_tenant_count ?? 0 }) : undefined}
            action={pending > 0 ? <Badge tone="warn">{pending}</Badge> : undefined}
            footer={pending > 0 ? (
              <button onClick={goReview}
                      className="flex min-h-[40px] w-full items-center justify-between rounded-lg px-1 text-small font-medium text-accent transition hover:underline">
                {t('platformOverview.reviewAll')} <ChevronRight size={16} />
              </button>
            ) : undefined}
          >
            {pending === 0 ? (
              <CalmState icon={<CheckCircle2 size={24} />}
                         title={t('platformOverview.noPendingTitle')}
                         body={t('platformOverview.noPendingBody')} />
            ) : payments.isLoading ? (
              <div className="space-y-1">{[0, 1, 2].map((i) => <PendingSkeleton key={i} />)}</div>
            ) : pendingTop.length ? (
              <ul className="-mx-1">
                {pendingTop.map((row) => (
                  <li key={row.id}>
                    <PendingRow row={row} currency={currency} onClick={goReview} />
                  </li>
                ))}
              </ul>
            ) : (
              <p className="py-2 text-small text-ink-soft">{t('platformOverview.acrossTenants', { count: o?.pending_tenant_count ?? 0 })}</p>
            )}
          </SectionCard>

          {/* Financial summary — cash flow over coverage, stacked. */}
          <div className="space-y-4">
          {/* Cash flow — collected / pending money + paying-store counts. */}
          <SectionCard icon={<Wallet size={16} />} title={t('platformOverview.cashThisMonth')}>
            <MetricGrid>
              <Metric dot="bg-success" label={t('platformPayments.collected')} unit={currency}
                      value={payments.isError ? '—' : money(pm?.collected_this_month_uzs ?? 0)} loading={payments.isLoading} />
              <Metric dot="bg-warn" label={t('platformPayments.pending')} unit={currency}
                      value={payments.isError ? '—' : money(pm?.pending_amount_uzs ?? 0)} loading={payments.isLoading} />
              <Metric dot="bg-success" label={t('platformOverview.payingStores')}
                      value={payments.isError ? '—' : money(payingStores)} loading={payments.isLoading} />
              <Metric dot="bg-warn" label={t('platformOverview.pendingChecks')}
                      value={payments.isError ? '—' : money(pm?.pending_count ?? 0)} loading={payments.isLoading} />
            </MetricGrid>
          </SectionCard>

          {/* Coverage — stores + end clients, with a derived density helper. */}
          <SectionCard icon={<Users size={16} />} title={t('platformOverview.reachTitle')}
                       footer={!overview.isLoading && perStore > 0
                         ? <p className="text-caption text-muted">{t('platformOverview.perStoreClients', { count: perStore })}</p>
                         : undefined}>
            <MetricGrid>
              <Metric dot="bg-success" label={t('platformOverview.activeTenants')}
                      value={money(activeTenants)} loading={overview.isLoading} />
              <Metric dot="bg-accent" label={t('platformOverview.endClients')}
                      value={money(customers)} loading={overview.isLoading} />
            </MetricGrid>
          </SectionCard>
          </div>
        </section>

        {/* Row 4 — Risk network: a clean "all clear" state or a prioritized summary.
            riskStats is an independent query — gate its own isError so a failure
            here never renders as a reassuring "all clear" (riskTotal would
            otherwise read 0 on error and silently fall into the calm-state
            branch, which is worse than a zero KPI: it actively tells the admin
            risk monitoring is fine when it's actually unknown). */}
        <SectionCard
          icon={riskStats.isError ? <ShieldAlert size={16} /> : riskTotal > 0 ? <ShieldAlert size={16} /> : <ShieldCheck size={16} />}
          title={t('platformRisk.title')}
          meta={!riskStats.isError ? t('platformRisk.optInTenants', { count: rs?.opt_in_tenant_count ?? 0 }) : undefined}
        >
          {riskStats.isLoading ? (
            <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
              {[0, 1, 2, 3].map((i) => (
                <div key={i} className="space-y-2"><Skeleton className="h-3 w-2/3" /><Skeleton className="h-7 w-1/2" /></div>
              ))}
            </div>
          ) : riskStats.isError ? (
            <ErrorCard error={riskStats.error} onRetry={() => riskStats.refetch()} />
          ) : riskTotal === 0 ? (
            <div className="flex flex-col gap-4">
              <CalmState icon={<ShieldCheck size={24} />}
                         title={t('platformRisk.allClear')} body={t('platformRisk.allClearBody')} />
              <div className="flex flex-wrap gap-2">
                {[
                  t('platformRisk.watchLate'), t('platformRisk.watchSuspicious'),
                  t('platformRisk.watchLimit'), t('platformRisk.watchBlocked'),
                ].map((label) => (
                  <span key={label} className="inline-flex items-center rounded-pill border border-line bg-cream px-3 py-1 text-caption text-muted">
                    {label}
                  </span>
                ))}
              </div>
            </div>
          ) : (
            <div className="space-y-4">
              <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
                <RiskStat label={t('platformRisk.totalSignals')} value={rs?.total_signals ?? 0} />
                <RiskStat label={t('platformRisk.flaggedCustomers')} value={rs?.flagged_customers ?? 0} />
                <RiskStat label={t('platformRisk.published')} value={rs?.by_status?.published ?? 0} />
                <RiskStat label={t('platformRisk.pendingAppeal')} value={rs?.by_status?.pending_appeal ?? 0} />
              </div>
              {severityEntries.length > 0 && (
                <div className="flex flex-wrap gap-2 border-t border-line pt-4">
                  {severityEntries.map(([sev, n]) => (
                    <Badge key={sev} tone={SEVERITY[sev]?.tone ?? 'neutral'}>
                      {t(`platformRisk.severity.${sev}`, sev)}
                      <span className="ml-1.5 font-semibold tabular-nums">{n}</span>
                    </Badge>
                  ))}
                </div>
              )}
            </div>
          )}
        </SectionCard>
      </div>
      )}
    </>
  );
}

/** One pending-payment row inside the approvals worklist.
 *
 *  (MUDD-114) `currency` is the FALLBACK, not the answer: this list mixes every
 *  tenant on the platform, and the row's own currency is what the amount is in.
 *  The ambient `t('billing.currency')` used to be stamped on every row, so a
 *  shop trading in USD had its receipts reviewed as though they were UZS. */
function PendingRow({ row, currency, onClick }: { row: PlatformPaymentRow; currency: string; onClick: () => void }) {
  return (
    <button
      onClick={onClick}
      className="flex min-h-[44px] w-full items-center gap-3 rounded-xl px-2 py-2 text-left transition hover:bg-ink/[0.03]"
    >
      <span className="grid h-9 w-9 shrink-0 place-items-center rounded-xl bg-warn-soft text-[color:var(--c-warn-strong)]">
        <Inbox size={16} />
      </span>
      <div className="min-w-0 flex-1">
        <div className="truncate text-small font-medium">{row.tenant_name}</div>
        <div className="text-micro text-muted">{shortDate(row.created_at)}</div>
      </div>
      <div className="shrink-0 text-right">
        <div className="font-mono text-small font-semibold tabular-nums">{money(row.amount)}</div>
        <div className="text-micro text-muted">{row.currency ?? currency}</div>
      </div>
      <ChevronRight size={16} className="shrink-0 text-muted" />
    </button>
  );
}

function PendingSkeleton() {
  return (
    <div className="flex items-center gap-3 px-2 py-2">
      <Skeleton className="h-9 w-9 shrink-0 rounded-xl" />
      <div className="flex-1 space-y-1.5"><Skeleton className="h-3.5 w-1/2" /><Skeleton className="h-3 w-1/4" /></div>
      <Skeleton className="h-3.5 w-16" />
    </div>
  );
}

/** Compact, calm positive state used inside operation/risk panels (not the
 *  full-height EmptyState card — that would leave a large blank inside a panel). */
function CalmState({ icon, title, body }: { icon: React.ReactNode; title: string; body: string }) {
  return (
    <div className="flex items-center gap-3 py-2">
      <span className="grid h-11 w-11 shrink-0 place-items-center rounded-2xl bg-success-soft text-[color:var(--c-success-strong)]">
        {icon}
      </span>
      <div className="min-w-0">
        <div className="text-small font-semibold">{title}</div>
        <p className="text-caption text-muted">{body}</p>
      </div>
    </div>
  );
}

function RiskStat({ label, value }: { label: string; value: number }) {
  return (
    <div className="min-w-0">
      <div className="truncate text-caption text-muted">{label}</div>
      <div className="mt-1 text-title font-semibold tabular-nums">{money(value)}</div>
    </div>
  );
}
