import { useEffect, useMemo, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { useQueryClient } from '@tanstack/react-query';
import { Loader2, Receipt } from 'lucide-react';
import { PageHeader } from '@/components/ui/PageHeader';
import { FloatingCreateAction } from '@/components/ui/FloatingCreateAction';
import { Button } from '@/components/ui/Button';
import { StatusBadge } from '@/components/ui/Badge';
import { RowCard } from '@/components/ui/RowCard';
import { DataTable, type Column } from '@/components/ui/DataTable';
import { SearchInput } from '@/components/ui/SearchInput';
import { SkeletonList } from '@/components/ui/Skeleton';
import { ErrorCard } from '@/components/ui/ErrorCard';
import { EmptyState } from '@/components/ui/EmptyState';
import { Modal, Field } from '@/components/ui/Modal';
import { ImageUploadField } from '@/components/ui/ImageUploadField';
import { MoneyInput } from '@/components/ui/FormFields';
import { Select } from '@/components/ui/Select';
import {
  FilterBar, FilterButton, FilterDrawer, RadioGroup, DateRangeGroup, RangeGroup,
  matchesDatePreset, matchesRange, rangeActive, type DatePreset,
} from '@/components/ui/FilterDrawer';
import { DetailModal, DefList, DefRow } from '@/components/ui/DetailModal';
import { ReceiptImageFrame } from '@/components/ui/ReceiptImageFrame';
import { MobileHeader } from '@/components/mobile/primitives';
import { StatusTabs } from '@/components/ui/StatusTabs';
import { useTenantPaymentsPage, useTenantInstallments, type PaymentRow } from '@/lib/queries';
import { queryKeys } from '@/lib/queryKeys';
import { api } from '@/lib/api';
import { apiErrorMessage } from '@/lib/errors';
import { confirmDialog, toast } from '@/lib/feedback';
import { money, paymentMethodLabel, shortDate } from '@/lib/format';
import { rowCurrency, useCurrency } from '@/lib/currency';
import { useSubmitGuard } from '@/hooks/useSubmitGuard';

const METHODS = ['cash', 'card', 'click', 'payme', 'transfer'] as const;
type StatusFilter = '' | 'pending' | 'approved' | 'rejected' | 'refunded';
type SortKey = 'new' | 'amount';
// See the tenant PaymentsPage's twin constant: the backend's hard per-page
// cap, large enough that a typical tenant's history for one status fits in a
// single page; "Yana yuklash" only appears past this (MUDD-84).
const PAYMENTS_PAGE_SIZE = 200;

/** Staff (sales) — record a payment at the counter (submitted as pending for
 *  owner approval) + see recent payments. Approve/reject is owner/manager-only,
 *  so the detail popup is view-only here. */
export default function StaffPaymentsPage() {
  const { t } = useTranslation();
  const currency = useCurrency();
  const [open, setOpen] = useState(false);
  const [statusFilter, setStatusFilter] = useState<StatusFilter>('');
  // (MUDD-84) Filtered on the server and paged — see the tenant PaymentsPage
  // for why a client-side status filter over a single capped fetch silently
  // hid a tenant's own older history.
  const query = useTenantPaymentsPage(PAYMENTS_PAGE_SIZE, statusFilter);
  const allRows = useMemo(() => query.data?.pages.flatMap((p) => p.items) ?? [], [query.data]);
  const total = query.data?.pages[0]?.total ?? 0;
  const statusCounts = query.data?.pages[0]?.counts;
  const [datePreset, setDatePreset] = useState<DatePreset>('all');
  const [amountRange, setAmountRange] = useState({ min: '', max: '' });
  const [sort, setSort] = useState<SortKey>('new');
  const [filterOpen, setFilterOpen] = useState(false);
  const [detail, setDetail] = useState<PaymentRow | null>(null);
  const [q, setQ] = useState('');
  const [debouncedQ, setDebouncedQ] = useState('');
  useEffect(() => { const id = setTimeout(() => setDebouncedQ(q.trim()), 250); return () => clearTimeout(id); }, [q]);

  // Real server-wide totals per status, independent of which tab is loaded.
  const allTotal = statusCounts ? Object.values(statusCounts).reduce((a, b) => a + b, 0) : undefined;
  const statusTabs: { value: StatusFilter; label: string; count?: number }[] = [
    { value: '', label: t('tenantPayments.filterAll', 'Hammasi'), count: allTotal },
    { value: 'pending', label: t('tenantPayments.filterPending', 'Tasdiqlash kutilmoqda'), count: statusCounts?.pending },
    { value: 'approved', label: t('tenantPayments.filterApproved', 'Tasdiqlangan'), count: statusCounts?.approved },
    { value: 'rejected', label: t('tenantPayments.filterRejected', 'Rad etilgan'), count: statusCounts?.rejected },
    { value: 'refunded', label: t('tenantPayments.filterRefunded', 'Qaytarilgan'), count: statusCounts?.refunded },
  ];
  const ql = debouncedQ.toLowerCase();
  // The status filter already ran on the server; only the remaining
  // client-side facets apply here.
  const rows = useMemo(() => allRows
    .filter((p) => matchesDatePreset(p.created_at, datePreset))
    .filter((p) => matchesRange(p.amount, amountRange))
    .filter((p) => !ql
      || (p.customer_name ?? '').toLowerCase().includes(ql)
      || p.method.toLowerCase().includes(ql))
    .slice()
    .sort((a, b) => (sort === 'amount' ? b.amount - a.amount : 0)),
    [allRows, datePreset, amountRange, ql, sort]);
  // Status lives in the visible StatusTabs row, not the drawer, so it is not
  // counted here — the drawer's badge reflects only the drawer's own filters.
  const activeFilters = (datePreset !== 'all' ? 1 : 0)
    + (rangeActive(amountRange) ? 1 : 0) + (sort !== 'new' ? 1 : 0);
  const resetFilters = () => { setStatusFilter(''); setDatePreset('all'); setAmountRange({ min: '', max: '' }); setSort('new'); setQ(''); };

  // Desktop (md+) table columns; the mobile RowCard list (below) is kept verbatim. (H17)
  const cols: Column<PaymentRow>[] = useMemo(() => [
    {
      key: 'customer', label: t('tenantPayments.colCustomer', 'Mijoz'), width: '1.6fr',
      render: (p) => <span className="block truncate font-medium">{p.customer_name ?? '—'}</span>,
      sortValue: (p) => (p.customer_name ?? '').toLowerCase(),
    },
    {
      key: 'method', label: t('tenantPayments.colMethod', 'Usul'), width: '110px',
      render: (p) => <span className="text-small text-muted">{paymentMethodLabel(t, p.method)}</span>,
      sortValue: (p) => p.method,
    },
    {
      key: 'amount', label: t('tenantPayments.colAmount', 'Summa'), width: '140px', align: 'right',
      render: (p) => <span className="font-semibold tabular-nums">{money(p.amount)}</span>,
      sortValue: (p) => p.amount,
    },
    {
      key: 'status', label: t('tenantPayments.colStatus', 'Holat'), width: '120px',
      render: (p) => <StatusBadge status={p.status} />, sortValue: (p) => p.status,
    },
    {
      key: 'date', label: t('tenantPayments.colDate', 'Sana'), width: '110px', align: 'right',
      render: (p) => <span className="tabular-nums text-small text-muted">{shortDate(p.created_at)}</span>,
      sortValue: (p) => p.created_at,
    },
  ], [t]);

  return (
    <>
      <div className="hidden md:block">
        <PageHeader
          title={t('nav.payments')}
          subtitle={t('staffPayments.subtitle', { count: total, defaultValue: "{{count}} ta to'lov" })}
        />
      </div>
      <div className="md:hidden">
        <MobileHeader title={t('nav.payments')} />
      </div>

      <FloatingCreateAction onClick={() => setOpen(true)} label={t('staffPayments.record', "To'lov qabul qilish")} />

      {/* First-class, always-visible status filter — the single source of
          truth for status; the drawer below no longer duplicates it (MUDD-84). */}
      <StatusTabs<StatusFilter> value={statusFilter} onChange={setStatusFilter} options={statusTabs} />

      <FilterBar>
        <SearchInput value={q} onChange={setQ} className="flex-1"
          placeholder={t('staffPayments.searchPh', { defaultValue: 'Mijoz yoki usul…' })} />
        <FilterButton count={activeFilters} onClick={() => setFilterOpen(true)} />
      </FilterBar>

      <FilterDrawer open={filterOpen} onClose={() => setFilterOpen(false)} onClear={resetFilters}>
        <DateRangeGroup value={datePreset} onChange={setDatePreset} />
        <RangeGroup
          label={t('tenantPayments.colAmount', 'Summa')}
          unit={t('common.sum', "so'm")}
          value={amountRange}
          onChange={setAmountRange}
        />
        <RadioGroup<SortKey>
          label={t('common.sort', 'Saralash')}
          value={sort}
          onChange={setSort}
          options={[
            { value: 'new', label: t('staffPayments.sortNew', 'Eng yangi') },
            { value: 'amount', label: t('staffPayments.sortAmount', 'Summa (katta)') },
          ]}
        />
      </FilterDrawer>

      {query.isLoading ? (
        <SkeletonList rows={6} />
      ) : query.isError ? (
        <ErrorCard error={query.error} onRetry={() => query.refetch()} />
      ) : rows.length === 0 ? (
        // A selected status tab narrows the result set exactly like a drawer
        // filter does — zero rows means "adjust what's selected", not "this
        // tenant has never taken a payment" (MUDD-84).
        activeFilters > 0 || debouncedQ || statusFilter ? (
          <EmptyState
            size="compact"
            icon={<Receipt size={20} />}
            title={t('common.noResults', 'Topilmadi')}
            body={t('staffPayments.emptyFilteredHint', "Filtrlarni tozalab, barcha to'lovlarni ko‘ring")}
            action={<Button variant="outline" size="md" onClick={resetFilters}>{t('common.clear', 'Tozalash')}</Button>}
          />
        ) : (
          <EmptyState size="compact" icon={<Receipt size={20} />} title={t('staffPayments.empty', "Hali to'lov yo'q.")} />
        )
      ) : (
        <>
          {/* Desktop (md+): dense sortable table that uses the wide screen. */}
          <div className="hidden md:block">
            <DataTable columns={cols} rows={rows} rowKey={(p) => p.id} onRowClick={(p) => setDetail(p)} />
          </div>

          {/* Mobile: richer avatar RowCards (verbatim). */}
          <ul className="space-y-2 md:hidden">
            {rows.map((p) => (
              <li key={p.id}>
                <RowCard
                  initial={p.customer_name ?? '#'}
                  title={p.customer_name ?? '—'}
                  subtitle={`${paymentMethodLabel(t, p.method)} · ${shortDate(p.created_at)}`}
                  leftBadge={<StatusBadge status={p.status} />}
                  trailing={<span className="text-body font-semibold tabular-nums">{money(p.amount)}</span>}
                  onClick={() => setDetail(p)}
                />
              </li>
            ))}
          </ul>
        </>
      )}

      {/* A status view can hold more than one page of history (MUDD-84). */}
      {query.hasNextPage && (
        <div className="mt-3 text-center">
          <Button variant="outline" size="md"
                  loading={query.isFetchingNextPage}
                  onClick={() => void query.fetchNextPage()}>
            {query.isFetchingNextPage
              ? t('common.loading')
              : t('common.loadMore', {
                  shown: allRows.length, total,
                  defaultValue: 'Yana yuklash ({{shown}}/{{total}})',
                })}
          </Button>
        </div>
      )}

      <DetailModal open={!!detail} onClose={() => setDetail(null)}
        title={detail?.customer_name ?? t('nav.payments')}
        subtitle={detail ? <StatusBadge status={detail.status} /> : undefined}>
        {detail && (
          <div className="space-y-4">
            <DefList>
              <DefRow label={t('tenantPayments.colAmount', 'Summa')}>{money(detail.amount)} {rowCurrency(detail, currency)}</DefRow>
              <DefRow label={t('tenantPayments.colMethod', 'Usul')}>{paymentMethodLabel(t, detail.method)}</DefRow>
              <DefRow label={t('tenantPayments.colDate', 'Sana')}>{shortDate(detail.created_at)}</DefRow>
            </DefList>
            {detail.receipt_image_url && (
              <div>
                {/* (LX-07) The tenant drawer's twin — same component, so the
                    two cannot drift apart. */}
                <ReceiptImageFrame
                  src={detail.receipt_image_url}
                  alt={t('tenantPayments.receipt', { defaultValue: 'Chek' })}
                />
                <a href={detail.receipt_image_url} target="_blank" rel="noopener" className="mt-2 inline-block text-caption font-medium text-accent underline">
                  {t('common.open', { defaultValue: 'Ochish' })}
                </a>
              </div>
            )}
          </div>
        )}
      </DetailModal>

      <RecordPaymentModal open={open} onClose={() => setOpen(false)} />
    </>
  );
}

function RecordPaymentModal({ open, onClose }: { open: boolean; onClose: () => void }) {
  const { t } = useTranslation();
  const currency = useCurrency();
  const qc = useQueryClient();
  const insQuery = useTenantInstallments();
  // Only open contracts can take a payment.
  const installments = (insQuery.data?.items ?? []).filter((i) => i.status === 'active' || i.status === 'overdue');
  const [insId, setInsId] = useState('');
  const [amount, setAmount] = useState('');
  const [method, setMethod] = useState<(typeof METHODS)[number]>('cash');
  const [receipt, setReceipt] = useState('');
  const { pending: busy, run: runSubmit } = useSubmitGuard();
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    if (!open) { setInsId(''); setAmount(''); setMethod('cash'); setReceipt(''); setErr(null); }
  }, [open]);

  // Default the amount to the selected contract's monthly payment.
  useEffect(() => {
    const sel = installments.find((i) => i.id === insId);
    if (sel && !amount) setAmount(String(sel.monthly_amount ?? ''));
  }, [insId]); // eslint-disable-line react-hooks/exhaustive-deps

  // Recording a payment commits money against a contract, so confirm the
  // amount, the customer and the contract first — a mis-keyed amount would
  // otherwise commit silently. On success, say plainly that an owner or manager
  // must approve it, so a sales user never believes the money is already
  // applied.
  const submit = async () => {
    if (!insId || !amount) { setErr(t('staffPayments.needFields', "Shartnoma va summani tanlang.")); return; }
    setErr(null);
    const sel = installments.find((i) => i.id === insId);
    if (!(await confirmDialog({
      title: t('staffPayments.confirmTitle', "To'lovni tasdiqlang"),
      message: t('staffPayments.confirmBody', {
        amount: `${money(Number(amount) || 0)} ${currency}`,
        name: sel?.customer_name ?? t('common.unknown', { defaultValue: 'Mijoz' }),
        contract: sel?.contract_number ?? '—',
        defaultValue: '{{amount}} · {{name}} · {{contract}} — qabul qilinadi.',
      }),
      confirmLabel: t('staffPayments.record', "To'lov qabul qilish"),
    }))) return;
    // Synchronous one-shot guard — a double-tap can't record two payments.
    void runSubmit(async () => {
      try {
        await api.post('/tenant/payments', {
          installment_id: insId, amount: Number(amount), method,
          receipt_image_url: receipt.trim() || null,
        });
        await qc.invalidateQueries({ queryKey: queryKeys.tenant.payments() });
        await qc.invalidateQueries({ queryKey: queryKeys.tenant.installmentsAll() });
        toast.success(t('staffPayments.recordedPendingApproval', {
          amount: `${money(Number(amount) || 0)} ${currency}`,
          defaultValue: "{{amount}} qabul qilindi. Do'kon egasi yoki menejer tasdiqlagach, qarz qoldig'i yangilanadi.",
        }));
        onClose();
      } catch (ex) {
        // The form keeps its values so a recoverable failure can be retried.
        setErr(apiErrorMessage(ex));
      }
    });
  };

  return (
    <Modal open={open} onClose={onClose} title={t('staffPayments.record', "To'lov qabul qilish")}
           subtitle={t('staffPayments.recordSub', "To'lov tasdiqlash uchun egaga yuboriladi.")}
           footer={
             <>
               <Button variant="outline" size="md" onClick={onClose}>{t('common.cancel')}</Button>
               <Button variant="primary" size="md" onClick={() => void submit()} disabled={busy || !insId || !amount}>
                 {busy && <Loader2 size={14} className="animate-spin" />} {t('staffPayments.record', "To'lov qabul qilish")}
               </Button>
             </>
           }>
      <div className="grid gap-3">
        <Field label={t('staffPayments.contract', 'Shartnoma')} required>
          <Select
            value={insId}
            onChange={(v) => setInsId(v)}
            placeholder="—"
            options={installments.map((i) => ({
              value: i.id,
              label: `${(i.contract_number ?? i.reference ?? i.id.slice(0, 8))} · ${money(Math.max(0, i.principal - i.paid_amount))} ${currency}`,
            }))}
          />
        </Field>
        <Field label={t('staffPayments.amount', 'Summa')} required hint={currency}>
          <MoneyInput value={Number(amount) || 0} onChange={(n) => setAmount(n ? String(n) : '')} />
        </Field>
        <Field label={t('staffPayments.method', "To'lov usuli")}>
          <Select
            value={method}
            onChange={(v) => setMethod(v as (typeof METHODS)[number])}
            options={METHODS.map((m) => ({ value: m, label: paymentMethodLabel(t, m) }))}
          />
        </Field>
        <Field label={t('staffPayments.receipt', 'Chek surati (ixtiyoriy)')}
               hint={t('staffPayments.receiptHint', 'Naqd/karta to‘lov uchun chek rasmini biriktiring — ega ko‘r-ko‘rona tasdiqlamasin')}>
          <ImageUploadField value={receipt} onChange={setReceipt} />
        </Field>
        {err && <div className="rounded-control border border-danger/30 bg-[color:var(--c-danger-soft)] px-3 py-2 text-body text-danger">{err}</div>}
      </div>
    </Modal>
  );
}
