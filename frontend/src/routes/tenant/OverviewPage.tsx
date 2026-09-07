import { useMemo, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { useNavigate, useParams } from 'react-router';
import { PageHeader } from '@/components/ui/PageHeader';
import { ChartEmptyState } from '@/components/ui/ChartEmptyState';
import { FloatingCreateAction } from '@/components/ui/FloatingCreateAction';
import { KpiCard } from '@/components/ui/KpiCard';
import { Metric } from '@/components/ui/Metric';
import { CheckCircle2, Wallet, TrendingUp } from 'lucide-react';
import { EmptyState } from '@/components/ui/EmptyState';
import { Skeleton, SkeletonKpis, SkeletonList } from '@/components/ui/Skeleton';
import { ErrorCard } from '@/components/ui/ErrorCard';
import { Donut } from '@/components/ui/Donut';
import { Spark } from '@/components/ui/Spark';
import { useTenantOverview, useTenantPayments, useTenantWorklist, type TenantOverview,
  useCatalogCategories, useReportCashflow,
} from '@/lib/queries';
import { localToday, money, paymentMethodLabel, shortDate, timeOfDay } from '@/lib/format';
import { useCurrency } from '@/lib/currency';
import { isRowOverdue, overdueDays } from '@/lib/overdue';
import { cn } from '@/lib/cn';
import { StatusBadge } from '@/components/ui/Badge';
import { InstallmentModal } from './_modals/InstallmentModal';
import { secondaryCurrencyHint } from '@/lib/secondaryCurrency';
import { InstallmentDetailModal } from './InstallmentsPage';

export default function TenantOverviewPage() {
  const { t, i18n } = useTranslation();
  const ambientCurrency = useCurrency();
  const navigate = useNavigate();
  const { locale } = useParams();
  const overview = useTenantOverview();
  const payments = useTenantPayments();
  const o = overview.data;
  // (MUDD-114) The server states which currency its scalar KPIs are in. Prefer
  // that over the ambient setting: the two agree for every single-currency shop,
  // and where they can disagree — a shop that switched `settings.currency` after
  // writing contracts — the server's answer is the one the numbers were computed
  // in, while the ambient one would relabel them.
  const currency = o?.primary_currency ?? ambientCurrency;
  const [open, setOpen] = useState(false);
  const [detailId, setDetailId] = useState<string | null>(null);
  // Set only by the post-creation hand-off, so a control labelled
  // "record a payment" lands the user on the payment form itself.
  const [payForId, setPayForId] = useState<string | null>(null);

  // "Needs attention" queue: contracts due today or overdue, one tap to act.
  //
  // Server-computed over the whole tenant, returning the true total and the
  // moment it was computed. Never re-derive this from a page of `/installments`
  // — that shows a slice of the overdue set and says nothing about it.
  const worklistQ = useTenantWorklist(8);
  const needsAttention = worklistQ.data?.items ?? [];
  const needsAttentionTotal = worklistQ.data?.total ?? 0;

  // (MUDD-140) The 14-day income chart used to sum the newest 200
  // `/tenant/payments` rows in the browser — pending/rejected/refunded
  // included, and silently incomplete past 200 payments in the window. The
  // tenant-wide, status-filtered, per-currency aggregate this needs already
  // exists (`/tenant/reports/cashflow`, the same one ReportsPage.tsx charts) —
  // never re-derive it from a page of raw rows again.
  //
  // No role gate: `/:locale/tenant/*` is `PersonaGuard allow={['tenant']}`
  // (router.tsx), and `personaFor` only ever resolves to `'tenant'` for
  // owner/manager or an impersonating platform admin — never for `sales`,
  // which maps to the `staff` persona and its own shell. So every account
  // that can actually mount this page is one `/tenant/reports/cashflow`
  // already serves: owner/manager directly, and an impersonating admin via
  // the owner-level membership `resolve_tenant` synthesizes for them
  // (`backend/app/api/tenant/_shared.py`). A role check here was gating
  // against a caller that cannot reach this component.
  //
  // `days` is the frozen 14-day window — computed once so the request range
  // and the bucket keys can never drift apart (a `staleTime` refetch across
  // midnight must not regenerate "today" against an unchanged request). Keys
  // are LOCAL calendar days (`localToday`, not `toISOString`): a UTC slice
  // reports the wrong day for roughly a third of the clock in UZT (UTC+5),
  // and the server buckets on the tenant's own calendar day too (MUDD-134).
  const days = useMemo(() => {
    const today = new Date();
    return Array.from({ length: 14 }, (_, i) => {
      const d = new Date(today);
      d.setDate(today.getDate() - (13 - i));
      return localToday(d);
    });
  }, []);
  const cashflow = useReportCashflow({ from: days[0], to: days[days.length - 1], granularity: 'day' });
  const incomeSeries = useMemo(() => {
    const rows = cashflow.data?.series ?? [];
    const byDay = new Map(rows.map((r) => [r.period.slice(0, 10), r.amount]));
    return days.map((key) => ({ x: shortDate(key), y: byDay.get(key) ?? 0 }));
  }, [cashflow.data, days]);
  const hasIncome = incomeSeries.some((p) => p.y > 0);
  const incomeSecondary = secondaryCurrencyHint(cashflow.data?.total_by_currency, currency);

  const statusSlices = [
    { key: 'active',    label: t('status.active'),    value: o?.installments_by_status.active ?? 0,    color: 'var(--c-success)' },
    { key: 'overdue',   label: t('status.overdue'),   value: o?.installments_by_status.overdue ?? 0,   color: 'var(--c-accent)' },
    { key: 'completed', label: t('status.completed'), value: o?.installments_by_status.completed ?? 0, color: 'var(--c-ink)' },
  ];
  const total = statusSlices.reduce((a, s) => a + s.value, 0);
  // (QA L-1) The subtitle string reads "{{count}} ta faol bo'lib to'lash" —
  // "{{count}} ACTIVE instalments" — but was being handed `total`, which sums
  // active + overdue + completed. It reported 224 active when 148 were. The
  // donut still shows `total` in its centre, correctly; this line claims to
  // count only the active ones, so it must.
  const activeCount = o?.installments_by_status.active ?? 0;

  // (MUDD-83) What the shop actually sells. The status donut says how contracts
  // are going; this says what they are FOR, which is the question behind "what
  // should I stock next month".
  //
  // Ordered by value, not by count, and the server orders it — three phones and
  // one car are four contracts either way, but the car is the business.
  const categoriesQ = useCatalogCategories();
  const categoryRows = useMemo(() => {
    const rows = o?.installments_by_category ?? [];
    const biggest = rows[0]?.amount_uzs ?? 0;
    return rows.map((row) => {
      const cat = categoriesQ.data?.find((c) => c.slug === row.category);
      return {
        ...row,
        // A retired catalog row still has history behind it, so fall back to
        // the slug rather than dropping the line or showing a blank label.
        label: cat
          ? (i18n.language.startsWith('ru') ? (cat.name_ru || cat.name_uz) : cat.name_uz)
          : row.category,
        // Share of the LARGEST category, not of the total: the bar is there to
        // compare categories with each other, and normalising to the total
        // leaves every bar short and hard to read once there are more than a few.
        share: biggest > 0 ? Math.round((row.amount_uzs / biggest) * 100) : 0,
        // (MUDD-141) `contract_count` is the real name for the legacy `count`
        // field; fall back to it so a stale cached response (no `contract_count`
        // yet) still shows a number instead of blank.
        contractCount: row.contract_count ?? row.count,
      };
    });
  }, [o?.installments_by_category, categoriesQ.data, i18n.language]);

  return (
    <>
      <div className="hidden md:block">
        <PageHeader
          title={t('nav.overview')}
          subtitle={t('tenantOverview.subtitle', { count: activeCount })}
        />
      </div>

      {/* This is a dashboard, not a list, so the create action never had a
          natural home in a header row. The floating `+` gives it the same home
          it has everywhere else, above the KPIs rather than in front of them. */}
      <FloatingCreateAction onClick={() => setOpen(true)} label={t('common.newInstallment')} />

      {/* ── Mobile hero (mockup-style) ──────────────────────────────── */}
      {/* A failed fetch with no cached data must never render as a "0 so'm
          debt" dashboard — that reads as real, reassuring data instead of a
          load failure for an owner checking their business's money. Show the
          shared ErrorCard in place of the KPIs instead of layering a banner
          over zeroed-out cards. A background refetch (isError but `o` still
          cached from a prior success) keeps showing the last-known values. */}
      {overview.isError && !o ? (
        <ErrorCard error={overview.error} onRetry={() => overview.refetch()} />
      ) : (
        <>
          <MobileOverview o={o} loading={overview.isLoading} />

          {overview.isLoading ? (
            // Three, matching the steady state: the fourth tile only appears
            // when receipts are pending, which is the exception. A fixed four
            // placeholders reflowed the row on EVERY dashboard load for every
            // shop with nothing awaiting review.
            <div className="hidden md:block"><SkeletonKpis count={3} /></div>
          ) : (
          <section className={cn('hidden grid-cols-1 gap-4 sm:grid-cols-2 md:grid',
                                 (o?.pending_receipts ?? 0) > 0 ? 'xl:grid-cols-4' : 'xl:grid-cols-3')}>
            {/* The hint must carry its NUMBER. It read as a bare "faol mijozlar"
                — a caption qualifying a count that was never passed, because the
                overview payload has no active-customer figure to pass. What the
                total is actually summed over is the active + overdue contracts
                (backend: sum of remaining_balance across those two statuses), and
                `installments_by_status` already reports them, so the hint now
                names the set the number really covers. */}
            <KpiCard
              label={t('tenantOverview.kpiDebt')}
              value={o?.debt_total_uzs ?? 0}
              unit={currency}
              hint={
                // A second currency is STATED, never folded into the figure
                // above it — there is no rate to fold it with. Absent for the
                // single-currency shops that are almost all of them, so their
                // dashboard is unchanged.
                secondaryCurrencyHint(o?.debt_total_by_currency, currency)
                ?? t('tenantOverview.kpiDebtHint', {
                  count: (o?.installments_by_status?.active ?? 0) + (o?.installments_by_status?.overdue ?? 0),
                })
              }
            />
            {/* h-full: these two tiles are wrapped in a button, which becomes the
                grid item — without it the card inside does not stretch to the row
                height and the unwrapped first tile stood visibly taller. */}
            <button type="button" onClick={() => navigate(`/${locale ?? 'uz'}/tenant/installments?status=overdue`)} className="h-full text-left transition hover:opacity-80">
              <KpiCard className="h-full" label={t('tenantOverview.kpiOverdue')} value={o?.overdue_uzs ?? 0}     unit={currency} tone="danger"
                        hint={secondaryCurrencyHint(o?.overdue_by_currency, currency)} />
            </button>
            {/* Money due today belongs to the collections workflow, so it drills
                into that view. `status=active` sent the owner to every open
                contract in the shop — a different, much larger set than the
                figure they tapped, and one the collections queue does not
                describe. */}
            <button type="button" onClick={() => navigate(`/${locale ?? 'uz'}/tenant/installments?status=due`)} className="h-full text-left transition hover:opacity-80">
              <KpiCard className="h-full" label={t('tenantOverview.kpiDueToday')} value={o?.due_today_uzs ?? 0}  unit={currency} tone="warn"
                        hint={secondaryCurrencyHint(o?.due_today_by_currency, currency)} />
            </button>
            {/* #26: hide the "waiting for confirmation" tile when nothing is pending. */}
            {(o?.pending_receipts ?? 0) > 0 && (
              <KpiCard label={t('tenantOverview.kpiPending')} value={o?.pending_receipts ?? 0} tone="accent" />
            )}
          </section>
          )}
        </>
      )}

      {/* Shown on phones too. These queries already run at every width — the
           data was fetched and then hidden, so a shop owner on a phone saw four
           tiles and nothing else while the same screen on a laptop carried the
           chart, the status split, the work queue and the recent payments. */}
      <section className="mt-6 grid grid-cols-1 gap-4 xl:grid-cols-2">
        <div className="card p-5">
          <h3 className="mb-4 text-subsection font-semibold">{t('tenantOverview.incomeChart')}</h3>
          {/* (H16) A failed cashflow fetch must not render as "no income
              today" — that's the fake-empty pattern noted at TodayPage.tsx:148. */}
          {cashflow.isLoading
            ? <div role="status" aria-busy="true"><Skeleton className="h-[200px] w-full" /></div>
            : cashflow.isError
              ? <ErrorCard error={cashflow.error} onRetry={() => cashflow.refetch()} />
              : hasIncome
                ? <>
                    <Spark data={incomeSeries} />
                    {incomeSecondary && <p className="mt-2 text-caption text-muted">{incomeSecondary}</p>}
                  </>
                : <ChartEmptyState icon={<TrendingUp size={24} />}
                                    title={t('tenantOverview.incomeChartEmptyTitle')}
                                    body={t('tenantOverview.incomeChartNote')} />}
        </div>
        <div className="card p-5">
          <h3 className="mb-4 text-subsection font-semibold">{t('tenantOverview.byStatus')}</h3>
          <Donut slices={statusSlices} total={total} totalLabel={t('tenantOverview.donutTotal', "bo'lib to'lash")} />
        </div>
      </section>

      {/* ── What the shop sells, by category (MUDD-83) ─────────────── */}
      <section className="mt-6 card p-5">
        <h3 className="mb-4 text-subsection font-semibold">{t('tenantOverview.byCategory')}</h3>
        {categoryRows.length === 0 ? (
          <p className="text-caption text-muted">{t('tenantOverview.byCategoryEmpty')}</p>
        ) : (
          <ul className="space-y-3">
            {categoryRows.map((row) => (
              <li key={row.category}>
                <div className="flex items-baseline justify-between gap-3">
                  <span className="min-w-0 truncate text-body font-medium text-ink">{row.label}</span>
                  <span className="shrink-0 text-body tabular-nums text-ink">
                    {money(row.amount_uzs)} {currency}
                    {/* Same rule as the KPI tiles: a category also sold in
                        another currency says so instead of being added up. */}
                    {secondaryCurrencyHint(row.amount_by_currency, currency) && (
                      <span className="ml-1 text-caption text-muted">
                        {secondaryCurrencyHint(row.amount_by_currency, currency)}
                      </span>
                    )}
                  </span>
                </div>
                <div className="mt-1 flex flex-wrap items-center gap-2">
                  {/* Purely decorative — the dona/shartnoma text next to it
                      carries the actual meaning for a screen reader. */}
                  <div aria-hidden="true" className="h-1.5 min-w-12 flex-1 overflow-hidden rounded-pill bg-surface">
                    <div className="h-full rounded-pill bg-accent"
                         style={{ width: `${row.share}%` }} />
                  </div>
                  {/* (MUDD-141) Units sold is the PRIMARY metric now — what the
                      shop should stock more of — so it leads and is styled like
                      the rest of the row's text. Contract count stays a
                      secondary, muted figure: it never disappears, but it no
                      longer reads as equal to the units figure. Each number
                      keeps its own word next to it (dona vs shartnoma) so two
                      digits side by side never read as an unlabeled "5 / 3". */}
                  <span className="flex flex-wrap items-baseline gap-x-1.5 gap-y-0.5 text-caption tabular-nums">
                    {row.units_sold != null && (
                      <span className="font-medium text-ink">
                        {t('tenantOverview.categoryUnits', { count: row.units_sold })}
                      </span>
                    )}
                    <span className="text-muted">
                      {t('tenantOverview.categoryContracts', { count: row.contractCount })}
                    </span>
                  </span>
                </div>
              </li>
            ))}
          </ul>
        )}
      </section>

      {/* ── Needs attention: due today + overdue — one tap to act ───── */}
      <section className="mt-6 card p-0 overflow-hidden">
        <div className="flex items-center justify-between px-5 py-4 border-b border-line">
          <div className="min-w-0">
            <h3 className="text-subsection font-semibold">{t('tenantOverview.needsAttention', { defaultValue: "E'tibor talab qiladi" })}</h3>
            {/* Say what this list covers and as of when, so a partial view is
                never mistaken for the whole book. */}
            {needsAttentionTotal > 0 && (
              <p className="mt-0.5 text-caption text-muted">
                {needsAttentionTotal > needsAttention.length
                  ? t('tenantOverview.queueScope', {
                      shown: needsAttention.length, total: needsAttentionTotal,
                      defaultValue: 'Jami {{total}} tadan {{shown}} tasi ko‘rsatilmoqda',
                    })
                  : t('tenantOverview.queueTotal', { total: needsAttentionTotal, defaultValue: 'Jami {{total}} ta' })}
                {worklistQ.data?.as_of && ` · ${t('common.asOf', { time: timeOfDay(worklistQ.data.as_of), defaultValue: '{{time}} holatiga ko‘ra' })}`}
              </p>
            )}
          </div>
          {/* This preview shows overdue AND due-today contracts, so its
              destination must too: `status=due`, not `status=overdue`, which
              would drop the ones the shop can still collect on time. */}
          <button type="button" onClick={() => navigate(`/${locale ?? 'uz'}/tenant/installments?status=due`)}
                  className="shrink-0 text-caption font-medium text-accent hover:underline">{t('common.viewAll', { defaultValue: 'Hammasi' })}</button>
        </div>
        {worklistQ.isLoading && <div className="px-5 py-4"><SkeletonList rows={3} /></div>}
        {worklistQ.isError && (
          <div className="px-6 py-8"><ErrorCard error={worklistQ.error} onRetry={() => worklistQ.refetch()} /></div>
        )}
        {!worklistQ.isLoading && !worklistQ.isError && needsAttention.length === 0 && (
          <EmptyState size="compact" icon={<CheckCircle2 size={20} />}
                      title={t('tenantOverview.allClear', { defaultValue: "Bugun muddati o'tgan to'lov yo'q" })} />
        )}
        <ul>
          {needsAttention.map((i) => {
            // The server classified this row against the tenant's today;
            // re-deriving it here from `status` would miss cron lag.
            const od = overdueDays(i, worklistQ.data?.as_of_date);
            return (
              <li key={i.id}>
                <button type="button" onClick={() => setDetailId(i.id)}
                        className="flex w-full items-center gap-4 border-b border-line px-5 py-3 text-left text-body transition last:border-0 hover:bg-cream">
                  <span className="min-w-0 flex-1 truncate font-medium">{i.customer_name ?? i.contract_number ?? i.id.slice(0, 8)}</span>
                  <span className="tabular-nums text-muted">{money(i.monthly_amount)} {currency}</span>
                  <StatusBadge status={isRowOverdue(i) ? 'overdue' : i.status} />
                  <span className="w-[110px] text-right tabular-nums text-caption text-muted">
                    {shortDate(i.next_due_date)}
                    {od > 0 && <span className="block font-medium text-[color:var(--c-danger-strong)]">{t('installments.overdueDays', { n: od, defaultValue: `${od} kun` })}</span>}
                  </span>
                </button>
              </li>
            );
          })}
        </ul>
      </section>

      <section className="mt-6 card p-0 overflow-hidden">
        <div className="flex items-center justify-between px-5 py-4 border-b border-line">
          <h3 className="text-subsection font-semibold">{t('tenantOverview.recentPayments')}</h3>
          <span className="text-micro uppercase tracking-wider text-muted">{t('tenantOverview.recentPaymentsHint')}</span>
        </div>
        {payments.isLoading && <div className="px-5 py-4"><SkeletonList rows={3} /></div>}
        {/* (H16) Distinguish a failed payments fetch from a genuinely quiet
            day — mirrors staff/TodayPage.tsx (#321). */}
        {!payments.isLoading && payments.isError && (
          <div className="px-6 py-8"><ErrorCard error={payments.error} onRetry={() => payments.refetch()} /></div>
        )}
        {!payments.isLoading && !payments.isError && (payments.data?.items.length ?? 0) === 0 && (
          <EmptyState size="compact" icon={<Wallet size={20} />} title={t('tenantOverview.noRecentPayments')} />
        )}
        <ul>
          {payments.data?.items.slice(0, 5).map((p) => (
            <li key={p.id}>
              {/* (#470) Clickable row matching the Needs-attention pattern above;
                  opens the payment's contract (no standalone tenant payment view).
                  Flex + truncate replaces the fixed-px grid so long names don't clip. */}
              <button type="button" onClick={() => setDetailId(p.installment_id)}
                      className="flex w-full items-center gap-4 border-b border-line px-5 py-3 text-left text-body transition last:border-0 hover:bg-cream">
                <span className="min-w-0 flex-1 truncate">
                  <span className="font-medium">{p.customer_name ?? '—'}</span> <span className="text-muted">· {paymentMethodLabel(t, p.method)}</span>
                </span>
                <span className="tabular-nums text-muted">{money(p.amount)} {currency}</span>
                <StatusBadge status={p.status} />
                <span className="w-[110px] text-right tabular-nums text-caption text-muted">{shortDate(p.created_at)}</span>
              </button>
            </li>
          ))}
        </ul>
      </section>

      <InstallmentModal open={open} onClose={() => setOpen(false)}
                        onRecordPayment={(id) => { setDetailId(id); setPayForId(id); }} />
      <InstallmentDetailModal id={detailId} openPayment={!!payForId && payForId === detailId}
                              onClose={() => { setDetailId(null); setPayForId(null); }} />
    </>
  );
}

/** Phone-width overview — dark outstanding hero + 2×2 stat grid (mockup-style). */
function MobileOverview({ o, loading }: { o?: TenantOverview; loading?: boolean }) {
  const { t } = useTranslation();
  const currency = useCurrency();
  const navigate = useNavigate();
  const { locale } = useParams();
  const byStatus = o?.installments_by_status ?? {};

  // (MUDD-121) The desktop KPI cards (`hidden md:grid` above) already state a
  // second currency instead of silently dropping it. The mobile hero — the
  // primary surface in this Telegram Mini App — carried the same three
  // figures with bare `money()` and no hint at all, so a mixed-currency
  // shop's USD debt disappeared entirely on phones.
  const debtSecondary = secondaryCurrencyHint(o?.debt_total_by_currency, currency);
  const dueTodaySecondary = secondaryCurrencyHint(o?.due_today_by_currency, currency);
  const overdueSecondary = secondaryCurrencyHint(o?.overdue_by_currency, currency);
  if (loading) {
    return (
      <div className="md:hidden">
        <div className="mb-4">
          <div className="text-caption text-muted">—</div>
          <div className="text-page-title font-semibold tracking-tight">{t('tenantOverview.today', 'Bugun')}</div>
        </div>
        <SkeletonKpis count={2} />
      </div>
    );
  }
  return (
    <div className="md:hidden">
      <div className="mb-4">
        <div className="text-caption text-muted">{o?.tenant.name ?? '—'}</div>
        <div className="text-page-title font-semibold tracking-tight">{t('tenantOverview.today', 'Bugun')}</div>
      </div>

      <div className="rounded-card bg-ink p-6 text-white">
        <div className="text-micro font-medium uppercase tracking-kicker text-white/55">
          {t('tenantOverview.kpiDebt')}
        </div>
        <div className="mt-1.5 flex items-baseline gap-1.5 font-mono leading-none">
          <span className="text-title font-semibold tabular-nums tracking-tight">{money(o?.debt_total_uzs ?? 0)}</span>
          <span className="text-small text-white/55">{currency}</span>
        </div>
        {debtSecondary && (
          <div className="mt-1 text-caption text-white/55">{debtSecondary}</div>
        )}
        <div className="mt-4 flex items-stretch gap-5 text-micro">
          <div>
            <div className="text-micro uppercase tracking-wider text-white/45">{t('tenantOverview.kpiDueToday')}</div>
            <div className="mt-1 font-mono text-body tabular-nums">{money(o?.due_today_uzs ?? 0)}</div>
            {dueTodaySecondary && (
              <div className="text-micro text-white/45">{dueTodaySecondary}</div>
            )}
          </div>
          <div className="w-px bg-white/12" />
          <div>
            <div className="text-micro uppercase tracking-wider text-white/45">{t('tenantOverview.kpiOverdue')}</div>
            <div className="mt-1 font-mono text-body tabular-nums text-[color:var(--c-accent)]">{money(o?.overdue_uzs ?? 0)}</div>
            {overdueSecondary && (
              <div className="text-micro text-white/45">{overdueSecondary}</div>
            )}
          </div>
        </div>
      </div>

      <div className="mt-3 grid grid-cols-2 gap-3">
        <button type="button" className="text-left" onClick={() => navigate(`/${locale ?? 'uz'}/tenant/installments?status=overdue`)}>
          <MStat dot="bg-danger"  label={t('tenantOverview.kpiOverdue')}  value={money(o?.overdue_uzs ?? 0)} sub={overdueSecondary} />
        </button>
        {(o?.pending_receipts ?? 0) > 0 && (
          <MStat dot="bg-warn"    label={t('tenantOverview.kpiPending')}  value={String(o?.pending_receipts ?? 0)} sub={t('tenantOverview.kpiPendingHint', 'tekshirilmoqda')} />
        )}
        <MStat dot="bg-ink"     label={t('status.active')}             value={String(byStatus.active ?? 0)} />
        <MStat dot="bg-success" label={t('status.completed')}          value={String(byStatus.completed ?? 0)} />
      </div>
    </div>
  );
}

/**
 * The card chrome around a shared {@link Metric}.
 *
 * The metric internals — dot, caption label, mono tabular value, hint line —
 * used to be re-implemented here, which is what `Metric`'s own docstring says
 * it exists to stop ("replacing the per-page MoneyBlock / inline div
 * duplicates"). Delegating also picks up what the copy had drifted away from:
 * truncation on long labels and values, the documented `text-caption` label
 * size, and a loading skeleton.
 */
function MStat({ dot, label, value, sub }: { dot: string; label: string; value: string; sub?: string }) {
  return (
    <div className="rounded-card border border-line bg-surface p-4">
      <Metric dot={dot} label={label} value={value} hint={sub} />
    </div>
  );
}
