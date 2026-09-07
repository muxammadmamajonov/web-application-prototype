import { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Download, FileSpreadsheet, FileText, Percent, Send, TrendingUp, Wallet, BarChart3 } from 'lucide-react';
import { PageHeader } from '@/components/ui/PageHeader';
import { EmptyState } from '@/components/ui/EmptyState';
import { SkeletonList } from '@/components/ui/Skeleton';
import { MobileHeader, StatStrip, type StatStripItem } from '@/components/mobile/primitives';
import { KpiCard } from '@/components/ui/KpiCard';
import { ErrorCard } from '@/components/ui/ErrorCard';
import { Donut } from '@/components/ui/Donut';
import { Spark } from '@/components/ui/Spark';
import { Button } from '@/components/ui/Button';
import { Select } from '@/components/ui/Select';
import { DateRangePicker } from '@/components/ui/DateRangePicker';
import { money, pct, shortDate } from '@/lib/format';
import { useCurrency } from '@/lib/currency';
import { secondaryCurrencyHint } from '@/lib/secondaryCurrency';
import {
  downloadReport, sendReportToTelegram,
  useReportAging, useReportCashflow, useReportLtv,
  useReportMargin, useReportProducts, useReportStaff,
  type Granularity,
} from '@/lib/queries';
import { toast } from '@/lib/feedback';
import { apiErrorMessage } from '@/lib/errors';
import { V2_SURFACES_ENABLED } from '@/routes/_scope';

function ExportRow({ path, params }: { path: string; params?: Record<string, unknown> }) {
  const { t } = useTranslation();
  // (#442) Per-format loading: only the pressed button spins (Button's own
  // loading prop carries the spinner + aria-busy and disables just itself), so
  // exporting CSV doesn't block queueing the PDF of the same report.
  const [loading, setLoading] = useState<'csv' | 'xlsx' | 'pdf' | 'tg' | null>(null);
  const go = async (fmt: 'csv' | 'xlsx' | 'pdf') => {
    setLoading(fmt);
    try { await downloadReport(path, fmt, params); }
    catch (e) { toast.error(apiErrorMessage(e)); }
    finally { setLoading(null); }
  };
  // (#206) Deliver the report to the owner's Telegram via the bot (XLSX — the
  // richest "download everything" format).
  const toTelegram = async () => {
    setLoading('tg');
    try {
      await sendReportToTelegram(path, 'xlsx', params);
      toast.success(t('reports.sentToTelegram', 'Hisobot Telegramga yuborildi'));
    } catch (e) {
      toast.error(apiErrorMessage(e));
    } finally { setLoading(null); }
  };
  // CSV is the one retained export format; XLSX, PDF and Telegram delivery are
  // deferred, with handlers left wired behind the scope flag.
  return (
    <div className="flex flex-wrap items-center gap-2">
      <Button variant="ghost" size="sm" loading={loading === 'csv'} onClick={() => go('csv')}>
        <Download size={14} /> CSV
      </Button>
      {V2_SURFACES_ENABLED && (
        <Button variant="ghost" size="sm" loading={loading === 'xlsx'} onClick={() => go('xlsx')}>
          <FileSpreadsheet size={14} /> XLSX
        </Button>
      )}
      {V2_SURFACES_ENABLED && (
        <Button variant="ghost" size="sm" loading={loading === 'pdf'} onClick={() => go('pdf')}>
          <FileText size={14} /> PDF
        </Button>
      )}
      {V2_SURFACES_ENABLED && (
        <Button variant="ghost" size="sm" loading={loading === 'tg'} onClick={toTelegram}>
          <Send size={14} /> Telegram
        </Button>
      )}
    </div>
  );
}

function Card({ title, hint, children, exportPath, exportParams }: {
  title: string; hint?: string; children: React.ReactNode;
  exportPath: string; exportParams?: Record<string, unknown>;
}) {
  return (
    <section className="card p-0 overflow-hidden">
      <div className="flex flex-wrap items-center justify-between gap-3 border-b border-line px-5 py-4">
        <div>
          <h3 className="text-subsection font-semibold">{title}</h3>
          {hint && <p className="mt-0.5 text-caption text-muted">{hint}</p>}
        </div>
        <ExportRow path={exportPath} params={exportParams} />
      </div>
      <div className="p-5">{children}</div>
    </section>
  );
}

/** One labelled metric line inside a mobile analytics card: small muted label
 *  on the left, the value (tabular) on the right. */
function MetricLine({ label, value }: { label: string; value: React.ReactNode }) {
  return (
    <div className="flex items-baseline justify-between gap-3">
      <span className="text-caption text-muted">{label}</span>
      <span className="tabular-nums text-small font-medium text-ink">{value}</span>
    </div>
  );
}

/** Mobile (<md) fallback for the analytics tables: each row becomes a compact
 *  stacked card — primary label on top, metric values as labelled lines below.
 *  Mirrors RowCard's look (.card surface, type tokens) so the table's columns
 *  fold into the card instead of scrolling off-screen on phones. */
function MobileCards<T>({ rows, rowKey, primary, lines }: {
  rows: T[];
  rowKey: (row: T) => string;
  primary: (row: T) => React.ReactNode;
  lines: (row: T) => { label: string; value: React.ReactNode }[];
}) {
  return (
    <ul className="space-y-2.5 md:hidden">
      {rows.map((r) => (
        <li key={rowKey(r)} className="rounded-card border border-line bg-surface px-4 py-3">
          <div className="mb-2 truncate text-body font-medium">{primary(r)}</div>
          <div className="space-y-1">
            {lines(r).map((l) => <MetricLine key={l.label} label={l.label} value={l.value} />)}
          </div>
        </li>
      ))}
    </ul>
  );
}

const fmtDate = (d: Date) => d.toISOString().slice(0, 10);

export default function TenantReportsPage() {
  const { t } = useTranslation();
  const currency = useCurrency();

  const today = new Date();
  const monthAgo = new Date(today.getTime() - 30 * 86400_000);
  const [from, setFrom] = useState(fmtDate(monthAgo));
  const [to, setTo] = useState(fmtDate(today));
  const [granularity, setGranularity] = useState<Granularity>('day');

  const aging = useReportAging();
  const cashflow = useReportCashflow({ from, to, granularity });
  const staff = useReportStaff();
  const products = useReportProducts();
  const ltv = useReportLtv(20);
  const margin = useReportMargin();

  const a = aging.data;
  // The guard has to cover EVERY hop. `a?.buckets.b0_30.count` only protects
  // the first: a 200 whose body lacks `buckets` throws before the `?? 0` can
  // apply, and the whole Reports page falls into the generic error boundary —
  // the screen the standard reserves for genuine crashes, never for a response
  // shape. Found by the browser pass, not by any type-level check: the response
  // is typed, so only real traffic disagrees with it.
  const agingSlices = [
    { key: 'b0_30',   label: '0–30',  value: a?.buckets?.b0_30?.count ?? 0,   color: 'var(--c-success)' },
    { key: 'b31_60',  label: '31–60', value: a?.buckets?.b31_60?.count ?? 0,  color: 'var(--c-warn)' },
    { key: 'b61_90',  label: '61–90', value: a?.buckets?.b61_90?.count ?? 0, color: 'var(--c-accent)' },
    { key: 'b90_plus', label: '90+',  value: a?.buckets?.b90_plus?.count ?? 0, color: 'var(--c-danger)' },
  ];

  const cf = cashflow.data;
  const series = (cf?.series ?? []).map((s) => ({ x: shortDate(s.period), y: s.amount }));

  // (MUDD-121) Each report's scalars are narrowed to ITS OWN primary currency
  // (MUDD-114) — the same currency the response is labelled with, which can
  // briefly disagree with the ambient tenant setting right after a shop
  // changes it. Preferring the report's own `currency` over the ambient one
  // is the same rule `OverviewPage`/`TodayPage` already follow for this exact
  // reason.
  const agingCurrency = a?.currency ?? currency;
  const cashflowCurrency = cf?.currency ?? currency;
  const staffCurrency = staff.data?.currency ?? currency;
  const productsCurrency = products.data?.currency ?? currency;
  const ltvCurrency = ltv.data?.currency ?? currency;
  const marginCurrency = margin.data?.currency ?? currency;

  return (
    <>
      <div className="hidden md:block">
        <PageHeader
          title={t('nav.reports', 'Hisobotlar')}
          subtitle={t('reports.subtitle', 'Biznesingizning to‘liq moliyaviy ko‘rinishi — eksport qiling, tahlil qiling.')}
        />
      </div>
      <div className="md:hidden">
        <MobileHeader title={t('nav.reports', 'Hisobotlar')} />
      </div>

      {/* Filter bar */}
      <section className="card mb-6 flex flex-wrap items-end gap-4 p-4">
        <label className="flex flex-col gap-1 text-caption text-ink-soft">
          {t('reports.period', 'Davr (dan – gacha)')}
          <DateRangePicker
            from={from} to={to} max={fmtDate(today)}
            onChange={({ from: f, to: tt }) => { setFrom(f); setTo(tt); }}
          />
        </label>
        <label className="flex flex-col gap-1 text-caption text-ink-soft">
          {t('reports.granularity', 'Davr')}
          <Select
            value={granularity}
            onChange={(v) => setGranularity(v as Granularity)}
            options={[
              { value: 'day', label: t('reports.gran.day', 'Kun') },
              { value: 'week', label: t('reports.gran.week', 'Hafta') },
              { value: 'month', label: t('reports.gran.month', 'Oy') },
            ]}
          />
        </label>
      </section>

      {/* Top KPIs — compact StatStrip on phones, KpiCards from md up.
          (#401) On a query error show a dash, not a hard 0 that reads as a real
          zero-revenue business. KpiCard / StatStrip render a string verbatim.
          Revenue / gross profit / margin come from the deferred margin report,
          so this shows only figures the product stands behind: outstanding debt
          from the retained aging report. */}
      <div className="md:hidden mb-6">
        <StatStrip items={[
          ...(V2_SURFACES_ENABLED ? [
            { key: 'rev',    label: t('reports.stripRevenue', 'Tushum'),    value: margin.isError ? '—' : `${money(margin.data?.revenue ?? 0)} ${currency}`,      tone: 'default', icon: <FileSpreadsheet size={14} />, loading: margin.isLoading } as StatStripItem,
            { key: 'gp',     label: t('reports.stripProfit', 'Foyda'),      value: margin.isError ? '—' : `${money(margin.data?.gross_profit ?? 0)} ${currency}`, tone: 'success', icon: <TrendingUp size={14} />,      loading: margin.isLoading } as StatStripItem,
            { key: 'margin', label: t('reports.kpiMargin', 'Marja'),        value: margin.isError ? '—' : pct(margin.data?.margin_pct ?? 0),                                  tone: 'accent',  icon: <Percent size={14} />,         loading: margin.isLoading } as StatStripItem,
          ] : []),
          { key: 'out',    label: t('reports.stripOutstanding', 'Qarz'),  value: aging.isError ? '—' : `${money(a?.total_outstanding ?? 0)} ${currency}`,        tone: 'warn',    icon: <Wallet size={14} />,          loading: aging.isLoading } as StatStripItem,
        ]} />
      </div>
      <section className="mb-6 hidden gap-4 md:grid md:grid-cols-2 xl:grid-cols-4">
        {V2_SURFACES_ENABLED && <KpiCard label={t('reports.kpiRevenue', 'Tushum')} value={margin.isError ? '—' : (margin.data?.revenue ?? 0)} unit={marginCurrency}
                 hint={secondaryCurrencyHint(margin.data?.revenue_by_currency, marginCurrency)} />}
        {V2_SURFACES_ENABLED && <KpiCard label={t('reports.kpiGrossProfit', 'Yalpi foyda')} value={margin.isError ? '—' : (margin.data?.gross_profit ?? 0)} unit={marginCurrency} tone="success"
                 hint={secondaryCurrencyHint(margin.data?.gross_profit_by_currency, marginCurrency)} />}
        {V2_SURFACES_ENABLED && <KpiCard label={t('reports.kpiMargin', 'Marja')} value={margin.isError ? '—' : pct(margin.data?.margin_pct ?? 0)} tone="accent" formatNumber={false} />}
        <KpiCard label={t('reports.kpiOutstanding', 'Qoldiq qarz')} value={aging.isError ? '—' : (a?.total_outstanding ?? 0)} unit={agingCurrency} tone="warn"
                 hint={secondaryCurrencyHint(a?.total_outstanding_by_currency, agingCurrency)} />
      </section>

      <div className="grid grid-cols-1 gap-6 xl:grid-cols-2">
        {/* Cashflow */}
        <Card title={t('reports.cashflow', 'Pul oqimi')} hint={t('reports.cashflowHint', 'Tasdiqlangan to‘lovlar')}
          exportPath="/tenant/reports/cashflow" exportParams={{ from, to, granularity }}>
          {cashflow.isLoading
            ? <SkeletonList rows={4} />
            : cashflow.isError
              ? <ErrorCard error={cashflow.error} onRetry={() => cashflow.refetch()} />
            : series.length === 0
              ? <EmptyState size="compact" icon={<BarChart3 size={20} />} title={t('reports.empty', 'Maʼlumot yo‘q')} />
              : (
                <>
                  <Spark data={series} />
                  <div className="mt-3 text-body text-ink-soft">
                    {t('reports.total', 'Jami')}: <span className="font-semibold tabular-nums">{money(cf?.total ?? 0)} {cashflowCurrency}</span>
                    {secondaryCurrencyHint(cf?.total_by_currency, cashflowCurrency) && (
                      <span className="ml-1 text-caption text-muted">
                        {secondaryCurrencyHint(cf?.total_by_currency, cashflowCurrency)}
                      </span>
                    )}
                  </div>
                </>
              )}
        </Card>

        {/* Aging */}
        <Card title={t('reports.aging', 'Qarz yoshi')} hint={t('reports.agingHint', 'Muddati o‘tgan kunlar bo‘yicha')}
          exportPath="/tenant/reports/aging">
          {aging.isLoading
            ? <SkeletonList rows={4} />
            : aging.isError
              ? <ErrorCard error={aging.error} onRetry={() => aging.refetch()} />
              : <Donut slices={agingSlices} total={a?.total_count ?? 0} totalLabel={t('reports.contracts', 'shartnoma')} />}
        </Card>

        {/* Deferred report — aging and cashflow are the retained ones. */}
        {/* Staff performance */}
        {V2_SURFACES_ENABLED && (
        <Card title={t('reports.staff', 'Xodimlar samaradorligi')} hint={t('reports.staffHint', 'Sotuv hajmi va komissiya')}
          exportPath="/tenant/reports/staff-performance">
          {staff.isLoading
            ? <SkeletonList rows={4} />
            : staff.isError
              ? <ErrorCard error={staff.error} onRetry={() => staff.refetch()} />
            : (staff.data?.staff.length ?? 0) === 0
              ? <EmptyState size="compact" icon={<BarChart3 size={20} />} title={t('reports.empty', 'Maʼlumot yo‘q')} />
              : (
                <>
                  <div className="hidden overflow-x-auto md:block">
                    <table className="w-full text-body">
                      <thead>
                        <tr className="border-b border-line text-left text-micro uppercase tracking-wider text-muted">
                          <th className="py-2 pr-3 font-medium">{t('reports.col.staff', 'Xodim')}</th>
                          <th className="py-2 pr-3 text-right font-medium">{t('reports.col.sales', 'Sotuv')}</th>
                          <th className="py-2 pr-3 text-right font-medium">{t('reports.col.volume', 'Hajm')}</th>
                          <th className="py-2 text-right font-medium">{t('reports.col.commission', 'Komissiya')}</th>
                        </tr>
                      </thead>
                      <tbody>
                        {staff.data!.staff.map((r) => (
                          <tr key={r.user_id ?? r.name} className="border-b border-line last:border-0">
                            <td className="py-2 pr-3 truncate">{r.name}</td>
                            <td className="py-2 pr-3 text-right tabular-nums">{r.sales_count}</td>
                            <td className="py-2 pr-3 text-right tabular-nums">
                              {money(r.volume)}
                              {secondaryCurrencyHint(r.volume_by_currency, staffCurrency) && (
                                <div className="text-caption text-muted">{secondaryCurrencyHint(r.volume_by_currency, staffCurrency)}</div>
                              )}
                            </td>
                            <td className="py-2 text-right tabular-nums">
                              {money(r.commission)}
                              {secondaryCurrencyHint(r.commission_by_currency, staffCurrency) && (
                                <div className="text-caption text-muted">{secondaryCurrencyHint(r.commission_by_currency, staffCurrency)}</div>
                              )}
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                  <MobileCards
                    rows={staff.data!.staff}
                    rowKey={(r) => r.user_id ?? r.name}
                    primary={(r) => r.name}
                    lines={(r) => [
                      { label: t('reports.col.sales', 'Sotuv'), value: r.sales_count },
                      { label: t('reports.col.volume', 'Hajm'), value: (
                        <>
                          {money(r.volume)}
                          {secondaryCurrencyHint(r.volume_by_currency, staffCurrency) && (
                            <span className="ml-1 text-muted">{secondaryCurrencyHint(r.volume_by_currency, staffCurrency)}</span>
                          )}
                        </>
                      ) },
                      { label: t('reports.col.commission', 'Komissiya'), value: (
                        <>
                          {money(r.commission)}
                          {secondaryCurrencyHint(r.commission_by_currency, staffCurrency) && (
                            <span className="ml-1 text-muted">{secondaryCurrencyHint(r.commission_by_currency, staffCurrency)}</span>
                          )}
                        </>
                      ) },
                    ]}
                  />
                </>
              )}
        </Card>
        )}

        {/* Deferred report — aging and cashflow are the retained ones. */}
        {/* Products */}
        {V2_SURFACES_ENABLED && (
        <Card title={t('reports.products', 'Mahsulotlar')} hint={t('reports.productsHint', 'Brend / kategoriya bo‘yicha')}
          exportPath="/tenant/reports/products">
          {products.isLoading
            ? <SkeletonList rows={4} />
            : products.isError
              ? <ErrorCard error={products.error} onRetry={() => products.refetch()} />
            : (products.data?.rows.length ?? 0) === 0
              ? <EmptyState size="compact" icon={<BarChart3 size={20} />} title={t('reports.empty', 'Maʼlumot yo‘q')} />
              : (
                <>
                  <div className="hidden overflow-x-auto md:block">
                    <table className="w-full text-body">
                      <thead>
                        <tr className="border-b border-line text-left text-micro uppercase tracking-wider text-muted">
                          <th className="py-2 pr-3 font-medium">{t('reports.col.brand', 'Brend')}</th>
                          <th className="py-2 pr-3 font-medium">{t('reports.col.category', 'Kategoriya')}</th>
                          <th className="py-2 pr-3 text-right font-medium">{t('reports.col.qty', 'Soni')}</th>
                          <th className="py-2 text-right font-medium">{t('reports.col.revenue', 'Tushum')}</th>
                        </tr>
                      </thead>
                      <tbody>
                        {products.data!.rows.map((r, i) => (
                          <tr key={`${r.brand}-${r.category}-${i}`} className="border-b border-line last:border-0">
                            <td className="py-2 pr-3 truncate">{r.brand || '—'}</td>
                            <td className="py-2 pr-3 truncate">{r.category || '—'}</td>
                            <td className="py-2 pr-3 text-right tabular-nums">{r.qty}</td>
                            <td className="py-2 text-right tabular-nums">
                              {money(r.revenue)}
                              {secondaryCurrencyHint(r.revenue_by_currency, productsCurrency) && (
                                <div className="text-caption text-muted">{secondaryCurrencyHint(r.revenue_by_currency, productsCurrency)}</div>
                              )}
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                  <MobileCards
                    rows={products.data!.rows.map((r, i) => ({ ...r, _i: i }))}
                    rowKey={(r) => `${r.brand}-${r.category}-${r._i}`}
                    primary={(r) => [r.brand || '—', r.category || '—'].join(' · ')}
                    lines={(r) => [
                      { label: t('reports.col.qty', 'Soni'), value: r.qty },
                      { label: t('reports.col.revenue', 'Tushum'), value: (
                        <>
                          {money(r.revenue)}
                          {secondaryCurrencyHint(r.revenue_by_currency, productsCurrency) && (
                            <span className="ml-1 text-muted">{secondaryCurrencyHint(r.revenue_by_currency, productsCurrency)}</span>
                          )}
                        </>
                      ) },
                    ]}
                  />
                </>
              )}
        </Card>
        )}

        {/* Deferred report — aging and cashflow are the retained ones. */}
        {/* LTV — top customers */}
        {V2_SURFACES_ENABLED && (
        <Card title={t('reports.ltv', 'Eng qimmatli mijozlar')} hint={t('reports.ltvHint', 'Umrbod qiymat bo‘yicha (top 20)')}
          exportPath="/tenant/reports/ltv" exportParams={{ limit: 20 }}>
          {ltv.isLoading
            ? <SkeletonList rows={4} />
            : ltv.isError
              ? <ErrorCard error={ltv.error} onRetry={() => ltv.refetch()} />
            : (ltv.data?.customers.length ?? 0) === 0
              ? <EmptyState size="compact" icon={<BarChart3 size={20} />} title={t('reports.empty', 'Maʼlumot yo‘q')} />
              : (
                <>
                  <div className="hidden overflow-x-auto md:block">
                    <table className="w-full text-body">
                      <thead>
                        <tr className="border-b border-line text-left text-micro uppercase tracking-wider text-muted">
                          <th className="py-2 pr-3 font-medium">{t('reports.col.customer', 'Mijoz')}</th>
                          <th className="py-2 pr-3 text-right font-medium">{t('reports.col.contracts', 'Shartnoma')}</th>
                          <th className="py-2 pr-3 text-right font-medium">{t('reports.col.ltv', 'LTV')}</th>
                          <th className="py-2 text-right font-medium">{t('reports.col.paid', 'To‘langan')}</th>
                        </tr>
                      </thead>
                      <tbody>
                        {ltv.data!.customers.map((c) => (
                          <tr key={c.customer_id} className="border-b border-line last:border-0">
                            <td className="py-2 pr-3 truncate">{c.name}</td>
                            <td className="py-2 pr-3 text-right tabular-nums">{c.contracts}</td>
                            <td className="py-2 pr-3 text-right tabular-nums">
                              {money(c.lifetime_value)}
                              {secondaryCurrencyHint(c.lifetime_value_by_currency, ltvCurrency) && (
                                <div className="text-caption text-muted">{secondaryCurrencyHint(c.lifetime_value_by_currency, ltvCurrency)}</div>
                              )}
                            </td>
                            <td className="py-2 text-right tabular-nums">
                              {money(c.paid)}
                              {secondaryCurrencyHint(c.paid_by_currency, ltvCurrency) && (
                                <div className="text-caption text-muted">{secondaryCurrencyHint(c.paid_by_currency, ltvCurrency)}</div>
                              )}
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                  <MobileCards
                    rows={ltv.data!.customers}
                    rowKey={(c) => c.customer_id}
                    primary={(c) => c.name}
                    lines={(c) => [
                      { label: t('reports.col.contracts', 'Shartnoma'), value: c.contracts },
                      { label: t('reports.col.ltv', 'LTV'), value: (
                        <>
                          {money(c.lifetime_value)}
                          {secondaryCurrencyHint(c.lifetime_value_by_currency, ltvCurrency) && (
                            <span className="ml-1 text-muted">{secondaryCurrencyHint(c.lifetime_value_by_currency, ltvCurrency)}</span>
                          )}
                        </>
                      ) },
                      { label: t('reports.col.paid', 'To‘langan'), value: (
                        <>
                          {money(c.paid)}
                          {secondaryCurrencyHint(c.paid_by_currency, ltvCurrency) && (
                            <span className="ml-1 text-muted">{secondaryCurrencyHint(c.paid_by_currency, ltvCurrency)}</span>
                          )}
                        </>
                      ) },
                    ]}
                  />
                </>
              )}
        </Card>
        )}

        {/* Deferred report — aging and cashflow are the retained ones. */}
        {/* Margin */}
        {V2_SURFACES_ENABLED && (
        <Card title={t('reports.margin', 'Marja tahlili')} hint={t('reports.marginHint', 'Tannarx maʼlum bo‘lgan tovarlar bo‘yicha')}
          exportPath="/tenant/reports/margin">
          {margin.isLoading
            ? <SkeletonList rows={4} />
            : margin.isError
              ? <ErrorCard error={margin.error} onRetry={() => margin.refetch()} />
            : (
              <dl className="grid grid-cols-2 gap-y-3 text-body">
                <dt className="text-ink-soft">{t('reports.kpiRevenue', 'Tushum')}</dt>
                <dd className="text-right font-medium tabular-nums">
                  {money(margin.data?.revenue ?? 0)}
                  {secondaryCurrencyHint(margin.data?.revenue_by_currency, marginCurrency) && (
                    <div className="text-caption font-normal text-muted">{secondaryCurrencyHint(margin.data?.revenue_by_currency, marginCurrency)}</div>
                  )}
                </dd>
                <dt className="text-ink-soft">{t('reports.costKnown', 'Tannarx (maʼlum)')}</dt>
                <dd className="text-right font-medium tabular-nums">
                  {money(margin.data?.cost_known ?? 0)}
                  {secondaryCurrencyHint(margin.data?.cost_known_by_currency, marginCurrency) && (
                    <div className="text-caption font-normal text-muted">{secondaryCurrencyHint(margin.data?.cost_known_by_currency, marginCurrency)}</div>
                  )}
                </dd>
                <dt className="text-ink-soft">{t('reports.kpiGrossProfit', 'Yalpi foyda')}</dt>
                <dd className="text-right font-medium tabular-nums">
                  {money(margin.data?.gross_profit ?? 0)}
                  {secondaryCurrencyHint(margin.data?.gross_profit_by_currency, marginCurrency) && (
                    <div className="text-caption font-normal text-muted">{secondaryCurrencyHint(margin.data?.gross_profit_by_currency, marginCurrency)}</div>
                  )}
                </dd>
                <dt className="text-ink-soft">{t('reports.kpiMargin', 'Marja')}</dt>
                <dd className="text-right font-medium tabular-nums">{pct(margin.data?.margin_pct ?? 0)}</dd>
                <dt className="text-ink-soft">{t('reports.coverage', 'Tannarx qamrovi')}</dt>
                <dd className="text-right font-medium tabular-nums">{margin.data?.qty_with_cost ?? 0} / {margin.data?.qty_total ?? 0}</dd>
              </dl>
            )}
        </Card>
        )}
      </div>
    </>
  );
}
