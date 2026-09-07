import { useMemo, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { useNavigate, useParams } from 'react-router';
import {
  ArrowUpRight, Check, ChevronRight, CreditCard, ExternalLink,
  Inbox, Store, X,
} from 'lucide-react';
import { PageHeader } from '@/components/ui/PageHeader';
import { MobileHeader } from '@/components/mobile/primitives';
import { SectionCard } from '@/components/ui/SectionCard';
import { SearchInput } from '@/components/ui/SearchInput';
import {
  FilterBar, FilterButton, FilterDrawer, ActiveFilters,
  RadioGroup, CheckGroup, DateRangeGroup, RangeGroup,
  matchesDatePreset, matchesRange, rangeActive, type DatePreset,
} from '@/components/ui/FilterDrawer';
import { StatusBadge } from '@/components/ui/Badge';
import { Button } from '@/components/ui/Button';
import { Avatar } from '@/components/ui/Avatar';
import { EmptyState } from '@/components/ui/EmptyState';
import { DataTable, type Column } from '@/components/ui/DataTable';
import { SkeletonList } from '@/components/ui/Skeleton';
import { ErrorCard } from '@/components/ui/ErrorCard';
import { DetailDrawer, DetailSection, DetailRow } from '@/components/ui/DetailDrawer';
import { usePlatformPayments, usePlatformTopups } from '@/lib/queries';
import { useReviewTopup } from '@/lib/mutations';
import { dateTime, money, shortDate } from '@/lib/format';
import { confirmDialog, toast } from '@/lib/feedback';
import { apiErrorMessage } from '@/lib/errors';
import { V2_SURFACES_ENABLED } from '@/routes/_scope';
import { useSubmitGuard } from '@/hooks/useSubmitGuard';

type PlatformPaymentItem = NonNullable<ReturnType<typeof usePlatformPayments>['data']>['items'][number];
type TopupItem = NonNullable<ReturnType<typeof usePlatformTopups>['data']>[number];

type PayStatus = 'all' | 'pending' | 'approved' | 'rejected' | 'refunded';
type PaySort = 'new' | 'amount' | 'tenant';
const PAY_METHODS = ['cash', 'card', 'transfer', 'click', 'payme'] as const;

/** Platform · cross-tenant payment oversight. Receipt approval stays with the
 *  tenant — this is HQ's monitoring view (and the Overview "review" CTA). */
export default function PlatformPaymentsPage() {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const { locale } = useParams();

  const [status, setStatus] = useState<PayStatus>('all');
  const [methods, setMethods] = useState<string[]>([]);
  const [datePreset, setDatePreset] = useState<DatePreset>('all');
  const [amountRange, setAmountRange] = useState({ min: '', max: '' });
  const [sort, setSort] = useState<PaySort>('new');
  const [search, setSearch] = useState('');
  const [filterOpen, setFilterOpen] = useState(false);
  const [detail, setDetail] = useState<PlatformPaymentItem | null>(null);

  // Status stays server-side (the backend's status_filter); the rest refine
  // client-side over the loaded page so no extra queries fire per tap.
  const q = usePlatformPayments(status === 'all' ? undefined : status);
  const d = q.data;

  // ── KPI aggregates from the same API response ──────────────────────────────

  // (#6) Summary strip and KpiCard grid removed — on a list page they are a
  // preamble the reader scrolls past. The figures live on Overview.

  // ── Filtered/sorted rows ──────────────────────────────────────────────────
  const term = search.trim().toLowerCase();
  const rows = useMemo(() => (d?.items ?? [])
    .filter((p) => methods.length === 0 || methods.includes(p.method))
    .filter((p) => matchesDatePreset(p.created_at, datePreset))
    .filter((p) => matchesRange(p.amount, amountRange))
    .filter((p) => !term || `${p.tenant_name} ${p.method}`.toLowerCase().includes(term))
    .slice()
    .sort((a, b) => {
      if (sort === 'amount') return b.amount - a.amount;
      if (sort === 'tenant') return a.tenant_name.localeCompare(b.tenant_name);
      return 0; // 'new' — keep server order (created desc)
    }), [d, methods, datePreset, amountRange, term, sort]);

  const activeFilters = (status !== 'all' ? 1 : 0) + (methods.length > 0 ? 1 : 0)
    + (datePreset !== 'all' ? 1 : 0) + (rangeActive(amountRange) ? 1 : 0) + (sort !== 'new' ? 1 : 0);
  const clearAll = () => {
    setStatus('all'); setMethods([]); setDatePreset('all');
    setAmountRange({ min: '', max: '' }); setSort('new');
  };

  const dateLabel: Record<DatePreset, string> = {
    all: '', today: t('filters.today', 'Bugun'), '7d': t('filters.last7', '7 kun'),
    '30d': t('filters.last30', '30 kun'), '90d': t('filters.last90', '90 kun'),
  };
  const sortLabel: Record<PaySort, string> = {
    new: '', amount: t('platformPayments.sortAmount', 'Summa (katta)'), tenant: t('platformPayments.sortTenant', "Do'kon (A–Z)"),
  };
  // Removable chips for each active facet (default values are omitted).
  const chips = [
    status !== 'all' && { key: 'status', label: t(`status.${status}`, status), onRemove: () => setStatus('all') },
    ...methods.map((m) => ({ key: `m-${m}`, label: t(`tenantSettings.pm_${m}`, m.toUpperCase()), onRemove: () => setMethods((xs) => xs.filter((x) => x !== m)) })),
    datePreset !== 'all' && { key: 'date', label: dateLabel[datePreset], onRemove: () => setDatePreset('all') },
    rangeActive(amountRange) && { key: 'amount', label: `${amountRange.min || '0'}–${amountRange.max || '∞'}`, onRemove: () => setAmountRange({ min: '', max: '' }) },
    sort !== 'new' && { key: 'sort', label: sortLabel[sort], onRemove: () => setSort('new') },
  ].filter(Boolean) as { key: string; label: string; onRemove: () => void }[];

  // Desktop (lg+) table columns — same breakpoint as TenantsPage since the
  // 280px sidebar leaves too little room for a comfortable table below lg.
  // (H17) memoized so an unrelated re-render doesn't rebuild this array.
  const cols: Column<PlatformPaymentItem>[] = useMemo(() => [
    {
      key: 'tenant', label: t('platformPayments.colTenant', "Do'kon"), width: '1.6fr',
      render: (p) => (
        <span className="flex min-w-0 items-center gap-3">
          <Avatar name={p.tenant_name} size="sm" />
          <span className="block truncate font-medium">{p.tenant_name}</span>
        </span>
      ),
      sortValue: (p) => p.tenant_name.toLowerCase(),
    },
    {
      key: 'method', label: t('tenantPayments.colMethod', 'Usul'), width: '1fr',
      render: (p) => <span className="block truncate text-small text-muted">{t(`tenantSettings.pm_${p.method}`, p.method)}</span>,
      sortValue: (p) => p.method,
    },
    {
      key: 'amount', label: t('tenantPayments.colAmount', 'Summa'), width: '140px', align: 'right',
      render: (p) => <span className="tabular-nums font-medium">{money(p.amount)}</span>,
      sortValue: (p) => p.amount,
    },
    {
      key: 'status', label: t('platformPayments.statusFilter', 'Holat'), width: '120px',
      render: (p) => <StatusBadge status={p.status} />, sortValue: (p) => p.status,
    },
    {
      key: 'date', label: t('tenantPayments.colDate', 'Sana'), width: '110px', align: 'right',
      render: (p) => <span className="tabular-nums text-small text-muted">{shortDate(p.created_at)}</span>,
      sortValue: (p) => p.created_at,
    },
    {
      key: 'chevron', label: '', width: '32px', align: 'right',
      render: () => <ChevronRight size={16} className="text-muted" />,
    },
  ], [t]);

  const searchOrFilter = !!term || activeFilters > 0;

  return (
    <>
      {/* ── Header — desktop PageHeader / mobile MobileHeader (Telegram-safe). ── */}
      <div className="hidden md:block">
        <PageHeader
          title={t('nav.payments')}
          subtitle={t('platformTopups.pageSubtitle', "Do'konlarning balans to'ldirish so'rovlarini ko'rib chiqing")}
        />
      </div>
      <div className="md:hidden">
        <MobileHeader title={t('nav.payments')} />
        <p className="mb-3 max-w-prose text-small leading-snug text-muted">
          {t('platformTopups.pageSubtitle', "Do'konlarning balans to'ldirish so'rovlarini ko'rib chiqing")}
        </p>
      </div>

      <div className="space-y-4 sm:space-y-5">
        {/* Cross-tenant payments oversight — the KPI strip and the all-payments
            list — is deferred, so the top-up review queue below (the operator's
            actual job here) stands alone rather than as its sub-section. */}

        {/* ── Topup review queue (pending card/transfer top-ups) — the retained
            operator money surface. ── */}
        <TopupReviewSection />

        {/* ── Payments list inside a SectionCard (deferred, see above) ── */}
        {V2_SURFACES_ENABLED && (
        <SectionCard
          icon={<CreditCard size={16} />}
          title={t('platformPayments.listTitle', "Barcha to'lovlar")}
          meta={!q.isLoading && rows.length > 0 ? t('platformPayments.listMeta', { count: rows.length }) : undefined}
        >
          <div className="space-y-3">
            <FilterBar>
              <SearchInput
                value={search}
                onChange={setSearch}
                className="flex-1"
                placeholder={t('platformPayments.searchPh', { defaultValue: "Do'kon yoki usul bo'yicha qidirish…" })}
              />
              <FilterButton count={activeFilters} onClick={() => setFilterOpen(true)} />
            </FilterBar>
            <ActiveFilters filters={chips} onClearAll={clearAll} />
          </div>

          <div className="mt-4">
            {q.isLoading ? (
              <SkeletonList rows={6} />
            ) : q.isError ? (
              <ErrorCard error={q.error} onRetry={() => q.refetch()} />
            ) : rows.length === 0 ? (
              searchOrFilter ? (
                <EmptyState
                  size="compact"
                  icon={<CreditCard size={24} />}
                  title={t('common.noResults', 'Topilmadi')}
                  body={t('platformPayments.emptyFilteredHint', "Filtrlarni tozalab, barcha to'lovlarni ko'ring")}
                  action={
                    <Button variant="outline" size="md" onClick={() => { clearAll(); setSearch(''); }}>
                      {t('common.clear', 'Tozalash')}
                    </Button>
                  }
                />
              ) : (
                <EmptyState
                  tone="accent"
                  size="compact"
                  icon={<CreditCard size={24} />}
                  title={t('platformPayments.emptyTitle', "Hali to'lovlar yo'q")}
                  body={t('platformPayments.emptyBody', "Birinchi do'kon tranzaksiyasidan so'ng to'lovlar shu yerda paydo bo'ladi.")}
                />
              )
            ) : (
              <>
                {/* Desktop (lg+): dense sortable table — lg breakpoint matches TenantsPage
                    because the 280px sidebar leaves < 740px below lg. */}
                <div className="hidden lg:block">
                  <DataTable columns={cols} rows={rows} rowKey={(p) => p.id} onRowClick={(p) => setDetail(p)} />
                </div>

                {/* Mobile + tablet: rich avatar cards (never the cramped table). */}
                <ul className="space-y-2 lg:hidden">
                  {rows.map((p) => (
                    <li key={p.id}>
                      <PaymentCard payment={p} onClick={() => setDetail(p)} />
                    </li>
                  ))}
                </ul>
              </>
            )}
          </div>
        </SectionCard>
        )}
      </div>

      {/* ── Filter drawer (filters the deferred all-payments list) ── */}
      {V2_SURFACES_ENABLED && (
      <FilterDrawer open={filterOpen} onClose={() => setFilterOpen(false)} onClear={clearAll}>
        <RadioGroup<PayStatus>
          label={t('platformPayments.statusFilter', 'Holat')}
          value={status}
          onChange={setStatus}
          options={[
            { value: 'all',      label: t('common.all', 'Hammasi') },
            { value: 'pending',  label: t('status.pending', 'Kutilmoqda') },
            { value: 'approved', label: t('status.approved', 'Tasdiqlangan') },
            { value: 'rejected', label: t('status.rejected', 'Rad etilgan') },
            { value: 'refunded', label: t('status.refunded', 'Qaytarilgan') },
          ]}
        />
        <CheckGroup
          label={t('platformPayments.methodFilter', "To'lov usuli")}
          value={methods}
          onChange={setMethods}
          options={PAY_METHODS.map((m) => ({ value: m, label: t(`tenantSettings.pm_${m}`, m.toUpperCase()) }))}
        />
        <DateRangeGroup value={datePreset} onChange={setDatePreset} />
        <RangeGroup
          label={t('tenantPayments.colAmount', 'Summa')}
          unit={t('billing.currency')}
          value={amountRange}
          onChange={setAmountRange}
          step={1000}
        />
        <RadioGroup<PaySort>
          label={t('common.sort', 'Saralash')}
          value={sort}
          onChange={setSort}
          options={[
            { value: 'new',    label: t('platformPayments.sortNew', 'Eng yangi') },
            { value: 'amount', label: t('platformPayments.sortAmount', 'Summa (katta)') },
            { value: 'tenant', label: t('platformPayments.sortTenant', "Do'kon (A–Z)") },
          ]}
        />
      </FilterDrawer>
      )}

      {/* ── Payment detail drawer ── */}
      <PaymentDetailDrawer
        payment={detail}
        onClose={() => setDetail(null)}
        onNavigateTenant={() => navigate(`/${locale ?? 'uz'}/platform/tenants`)}
      />
    </>
  );
}

// ─────────────────────────────────────────────────────────────────────────────
// Mobile payment card — avatar + identity + amount + status + date
// ─────────────────────────────────────────────────────────────────────────────

function PaymentCard({ payment: p, onClick }: { payment: PlatformPaymentItem; onClick: () => void }) {
  const { t } = useTranslation();
  return (
    <div
      role="button"
      tabIndex={0}
      onClick={onClick}
      onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); onClick(); } }}
      className="cursor-pointer rounded-card border border-line bg-surface p-4 transition hover:border-ink/15 hover:shadow-card focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ink/20"
    >
      <div className="flex items-start gap-3">
        <Avatar name={p.tenant_name} size="md" />
        <div className="min-w-0 flex-1">
          <div className="truncate font-medium">{p.tenant_name}</div>
          <div className="text-caption text-muted">{t(`tenantSettings.pm_${p.method}`, p.method)}</div>
        </div>
        <ChevronRight size={16} className="mt-1 shrink-0 text-muted" />
      </div>
      <div className="mt-3 flex items-center justify-between gap-2 border-t border-line pt-3">
        <StatusBadge status={p.status} />
        <div className="text-right">
          <div className="tabular-nums font-semibold">{money(p.amount)}</div>
          <div className="text-caption text-muted">{shortDate(p.created_at)}</div>
        </div>
      </div>
    </div>
  );
}

// ─────────────────────────────────────────────────────────────────────────────
// Payment detail drawer — right-side panel (desktop) / full sheet (mobile)
// ─────────────────────────────────────────────────────────────────────────────

function PaymentDetailDrawer({ payment, onClose, onNavigateTenant }: { payment: PlatformPaymentItem | null; onClose: () => void; onNavigateTenant: () => void }) {
  const { t } = useTranslation();
  return (
    <DetailDrawer
      open={!!payment}
      onClose={onClose}
      title={payment?.tenant_name ?? ''}
      eyebrow={t('platformPayments.detailEyebrow', "To'lov")}
      badge={payment ? <StatusBadge status={payment.status} /> : undefined}
      leading={payment ? <Avatar name={payment.tenant_name} size="sm" /> : undefined}
      footer={
        payment ? (
          <Button variant="primary" size="md" onClick={() => { onClose(); onNavigateTenant(); }}>
            <Store size={16} /> {t('platformPayments.openTenant', "Do'kon")}
            <ArrowUpRight size={14} />
          </Button>
        ) : undefined
      }
    >
      {payment && (
        <>
          <DetailSection title={t('platformPayments.detailInfoSection', "To'lov ma'lumotlari")}>
            <div className="rounded-control border border-line">
              <DetailRow label={t('tenantPayments.colAmount', 'Summa')}>
                <span className="tabular-nums font-semibold">{money(payment.amount)} {t('billing.currency')}</span>
              </DetailRow>
              <DetailRow label={t('tenantPayments.colMethod', 'Usul')}>
                {t(`tenantSettings.pm_${payment.method}`, payment.method)}
              </DetailRow>
              {/* Status is NOT repeated here: the drawer header already carries
                  it in its `badge` slot, ~100px above and always visible even
                  when this list scrolls. Two badges for one field is the same
                  restatement the settings breadcrumb was. */}
              <DetailRow label={t('tenantPayments.colDate', 'Sana')}>
                {shortDate(payment.created_at)}
              </DetailRow>
              {payment.paid_at && (
                <DetailRow label={t('platformPayments.paidAt', "To'langan sana")}>
                  {shortDate(payment.paid_at)}
                </DetailRow>
              )}
            </div>
          </DetailSection>

          {payment.receipt_image_url && (
            <DetailSection title={t('platformPayments.receiptSection', 'Kvitansiya')}>
              <a
                href={payment.receipt_image_url}
                target="_blank"
                rel="noreferrer"
                className="inline-flex min-h-[40px] items-center gap-2 rounded-control border border-line bg-surface px-3 py-2 text-small font-medium text-accent transition hover:bg-ink/[0.03]"
              >
                <ExternalLink size={14} /> {t('platformPayments.viewReceipt', 'Chek')}
              </a>
            </DetailSection>
          )}
        </>
      )}
    </DetailDrawer>
  );
}

// ─────────────────────────────────────────────────────────────────────────────
// Topup review queue — pending card/transfer platform subscription top-ups
// ─────────────────────────────────────────────────────────────────────────────

/** Tenant → platform subscription top-up review queue. Approving a card-transfer
 *  top-up credits the tenant's prepaid balance. Row → detail with Approve/Reject. */
function TopupReviewSection() {
  const { t } = useTranslation();
  const q = usePlatformTopups('pending');
  const review = useReviewTopup();
  const items = q.data ?? [];
  const [detail, setDetail] = useState<TopupItem | null>(null);
  // (H14) Synchronous one-shot guard. This was `const [acting, setActing] =
  // useState(false)` with the check placed AFTER the `confirmDialog` await —
  // doubly late: `useState` is not readable until React commits, and the
  // check itself only ran once a whole async gap had already passed. A fast
  // double-tap (or two approvals dispatched in the same frame) could both
  // clear the check and both call `review.mutateAsync`, crediting a tenant's
  // balance twice. `useSubmitGuard` flips its ref synchronously before the
  // first `await` — including before `confirmDialog` — so a re-entrant call
  // is dropped before it can even open a second dialog. Mirrors
  // PaymentReviewPage's approve/reject guard: this approves real money too.
  const { pending: acting, run: runAct } = useSubmitGuard();

  // Distinguish a failed fetch from a genuinely-empty queue: hiding both
  // identically would let a transient error read as "nothing to review". (#512)
  if (q.isError) {
    return (
      <div
        role="alert"
        className="flex items-center justify-between gap-2 rounded-control border border-danger/30 bg-[color:var(--c-danger-soft)] px-3 py-2 text-small text-danger"
      >
        {t('common.errorLoading', 'Yuklashda xatolik')}
        <Button variant="outline" size="sm" onClick={() => q.refetch()}>{t('common.retry', 'Qayta urinish')}</Button>
      </div>
    );
  }
  // This queue is the ONLY retained content on the page (the all-payments list
  // below it is behind V2_SURFACES_ENABLED). Returning null for both the loading
  // and the empty case therefore did not hide a section — it left the operator
  // on a page that was nothing but a heading and a subtitle, with no way to tell
  // "still loading" from "nothing to do" from "this page is broken".
  if (q.isLoading) {
    return (
      <SectionCard icon={<Inbox size={16} />} title={t('platformTopups.title', "Balans to'ldirish so'rovlari")}>
        <SkeletonList rows={3} />
      </SectionCard>
    );
  }
  // An empty queue is the GOOD state here, so it reads as "you're clear", not as
  // an absence — and it says what will appear here, because an operator who has
  // never seen a request otherwise cannot tell what this page is for.
  if (items.length === 0) {
    return (
      <EmptyState
        tone="accent"
        icon={<Inbox size={24} />}
        title={t('platformTopups.emptyTitle', "Ko'rib chiqiladigan so'rov yo'q")}
        body={t('platformTopups.emptyBody', "Do'kon balansini to'ldirish so'rovi yuborganda u shu yerda paydo bo'ladi — chekni tekshirib, tasdiqlaysiz yoki rad etasiz.")}
      />
    );
  }

  // Approving a top-up credits a tenant's real balance: restate tenant and
  // amount first, keep the control visibly busy for the whole round trip (a
  // slow request that looks like nothing happened invites a second click), and
  // state the outcome only after the server confirms it.
  const act = (tp: TopupItem, action: 'approve' | 'reject') => runAct(async () => {
    const amount = `${money(tp.amount)} ${t('billing.currency')}`;
    const confirmed = await confirmDialog({
      title: action === 'approve'
        ? t('platformTopups.approveConfirmTitle', "To'ldirishni tasdiqlang")
        : t('platformTopups.rejectConfirmTitle', "So'rovni rad etasizmi?"),
      message: action === 'approve'
        ? t('platformTopups.approveConfirmBody', {
            tenant: tp.tenant_name, amount,
            defaultValue: '{{tenant}} balansiga {{amount}} qo‘shiladi va hisob-fakturalar yopiladi.',
          })
        : t('platformTopups.rejectConfirmBody', {
            tenant: tp.tenant_name, amount,
            defaultValue: '{{tenant}} uchun {{amount}} so‘rovi rad etiladi. Balans o‘zgarmaydi.',
          }),
      danger: action === 'reject',
      confirmLabel: action === 'approve' ? t('common.approve', 'Tasdiqlash') : t('common.reject', 'Rad etish'),
    });
    if (!confirmed) return;
    try {
      await review.mutateAsync({ id: tp.id, action });
      setDetail(null);
      toast.success(action === 'approve'
        ? t('platformTopups.approved', { tenant: tp.tenant_name, amount, defaultValue: '{{tenant}} balansiga {{amount}} qo‘shildi.' })
        : t('platformTopups.rejected', { tenant: tp.tenant_name, defaultValue: '{{tenant}} so‘rovi rad etildi.' }));
    }
    catch (ex) { toast.error(apiErrorMessage(ex)); }
  });

  // Audit rows render only for actors the server could actually resolve, so an
  // unknown one is absent rather than an invented name. With none resolvable the
  // whole block is skipped — an empty bordered box reads as a rendering bug.
  const hasAudit = !!detail
    && !!(detail.created_by_name || detail.reviewed_by_name || detail.reviewed_at);

  return (
    <SectionCard
      icon={<Inbox size={16} />}
      title={t('platformTopups.title', "Balans to'ldirish so'rovlari")}
      action={
        <span className="inline-flex items-center rounded-pill bg-warn-soft px-2.5 py-0.5 text-caption font-semibold text-[color:var(--c-warn-strong)]">
          {items.length}
        </span>
      }
    >
      <ul className="space-y-2">
        {items.map((tp) => (
          <li key={tp.id}>
            <TopupRow topup={tp} onClick={() => setDetail(tp)} />
          </li>
        ))}
      </ul>

      {/* Topup detail drawer — approve / reject actions */}
      <DetailDrawer
        open={!!detail}
        onClose={() => setDetail(null)}
        title={detail?.tenant_name ?? ''}
        eyebrow={t('platformTopups.detailEyebrow', "Balans to'ldirish")}
        leading={detail ? <Avatar name={detail.tenant_name} size="sm" /> : undefined}
        footer={
          detail ? (
            <>
              <Button
                variant="outline"
                size="md"
                loading={acting}
                disabled={acting}
                onClick={() => void act(detail, 'reject')}
                className="border-danger/40 text-danger hover:bg-[color:var(--c-danger-soft)]"
              >
                <X size={16} /> {t('common.reject', 'Rad etish')}
              </Button>
              <Button
                variant="primary"
                size="md"
                loading={acting}
                onClick={() => void act(detail, 'approve')}
              >
                {!acting && <Check size={16} />} {t('common.approve', 'Tasdiqlash')}
              </Button>
            </>
          ) : undefined
        }
      >
        {detail && (
          <>
            <DetailSection title={t('platformTopups.detailInfoSection', "So'rov ma'lumotlari")}>
              <div className="rounded-control border border-line">
                <DetailRow label={t('tenantPayments.colAmount', 'Summa')}>
                  <span className="tabular-nums font-semibold">{money(detail.amount)} {t('billing.currency')}</span>
                </DetailRow>
                <DetailRow label={t('tenantPayments.colMethod', 'Usul')}>
                  {t(`tenantSettings.pm_${detail.method}`, detail.method)}
                </DetailRow>
                <DetailRow label={t('platformTopups.destCard', 'Qabul qiluvchi karta')}>
                  {detail.dest_card_label ?? detail.dest_card_id ?? '—'}
                </DetailRow>
                <DetailRow label={t('platformPayments.statusFilter', 'Holat')}>
                  <StatusBadge status={detail.status} />
                </DetailRow>
                {/* Date AND time: two transfers from one business on the same day
                    are indistinguishable in a bank statement without the clock. */}
                <DetailRow label={t('tenantPayments.colDate', 'Sana')}>
                  {dateTime(detail.created_at)}
                </DetailRow>
                {detail.note && (
                  <DetailRow label={t('platformTopups.note', 'Izoh')}>{detail.note}</DetailRow>
                )}
              </div>
            </DetailSection>

            <DetailSection title={t('platformPayments.receiptSection', 'Kvitansiya')}>
              {detail.receipt_url ? (
                <a
                  href={detail.receipt_url}
                  target="_blank"
                  rel="noreferrer"
                  className="inline-flex min-h-[40px] items-center gap-2 rounded-control border border-line bg-surface px-3 py-2 text-small font-medium text-accent transition hover:bg-ink/[0.03]"
                >
                  <ExternalLink size={14} /> {t('platformPayments.viewReceipt', 'Chek')}
                </a>
              ) : (
                // Stated, not hidden: "no receipt" is a reason to reject, and a
                // missing section is indistinguishable from one that failed to load.
                <p className="text-small text-muted">
                  {t('platformTopups.noReceipt', 'Chek biriktirilmagan')}
                </p>
              )}
            </DetailSection>

            {hasAudit && (
              <DetailSection title={t('platformTopups.auditSection', 'Audit')}>
                <div className="rounded-control border border-line">
                  {detail.created_by_name && (
                    <DetailRow label={t('platformTopups.createdBy', "So'rov yuborgan")}>
                      {detail.created_by_name}
                    </DetailRow>
                  )}
                  {detail.reviewed_by_name && (
                    <DetailRow label={t('platformTopups.reviewedBy', "Ko'rib chiqqan")}>
                      {detail.reviewed_by_name}
                    </DetailRow>
                  )}
                  {detail.reviewed_at && (
                    <DetailRow label={t('platformTopups.reviewedAt', "Ko'rib chiqilgan vaqt")}>
                      {dateTime(detail.reviewed_at)}
                    </DetailRow>
                  )}
                </div>
              </DetailSection>
            )}
          </>
        )}
      </DetailDrawer>
    </SectionCard>
  );
}

/** Single row inside the topup queue — avatar, tenant name, amount + date. */
function TopupRow({ topup: tp, onClick }: { topup: TopupItem; onClick: () => void }) {
  const { t } = useTranslation();
  return (
    <button
      onClick={onClick}
      className="flex min-h-[52px] w-full items-center gap-3 rounded-control px-2 py-2.5 text-left transition hover:bg-ink/[0.03] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ink/20"
    >
      <Avatar name={tp.tenant_name} size="sm" />
      <div className="min-w-0 flex-1">
        <div className="truncate text-small font-medium">{tp.tenant_name}</div>
        <div className="text-micro text-muted">{t(`tenantSettings.pm_${tp.method}`, tp.method)} · {dateTime(tp.created_at)}</div>
      </div>
      <div className="shrink-0 text-right">
        <div className="font-mono text-small font-semibold tabular-nums">{money(tp.amount)}</div>
        <div className="text-micro text-muted">{t('billing.currency')}</div>
      </div>
      <ChevronRight size={16} className="shrink-0 text-muted" />
    </button>
  );
}
