import { useCallback, useEffect, useMemo, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Eye, Loader2, Megaphone, Send, Trash2 } from 'lucide-react';
import { PageHeader } from '@/components/ui/PageHeader';
import { FloatingCreateAction } from '@/components/ui/FloatingCreateAction';
import { Button } from '@/components/ui/Button';
import { Badge } from '@/components/ui/Badge';
import { Modal, Field, inputCls } from '@/components/ui/Modal';
import { Textarea } from '@/components/ui/FormFields';
import { Select } from '@/components/ui/Select';
import { RowCard } from '@/components/ui/RowCard';
import { DataTable, type Column } from '@/components/ui/DataTable';
import { SkeletonList } from '@/components/ui/Skeleton';
import { ErrorCard } from '@/components/ui/ErrorCard';
import { DetailModal, DefList, DefRow } from '@/components/ui/DetailModal';
import { MobileHeader } from '@/components/mobile/primitives';
import { SearchInput } from '@/components/ui/SearchInput';
import { RowActionMenu, type RowAction } from '@/components/ui/RowActionMenu';
import {
  FilterBar, FilterButton, FilterDrawer, RadioGroup, CheckGroup, DateRangeGroup, RangeGroup,
  matchesDatePreset, matchesRange, rangeActive, type DatePreset,
} from '@/components/ui/FilterDrawer';
import { toast, confirmDialog } from '@/lib/feedback';
import { useTenantBroadcasts, type BroadcastRow } from '@/lib/queries';
import { useCreateBroadcast, useDeleteBroadcast } from '@/lib/mutations';
import { shortDate } from '@/lib/format';
import { EmptyState } from '@/components/ui/EmptyState';

type TargetFilter = 'all' | 'active' | 'overdue' | 'custom';
const STATUS_KEYS = ['draft', 'scheduled', 'sending', 'sent', 'failed'] as const;

/** Owner / manager — compose a one-off message and push it to customers over
 *  the Telegram bot. Delivery is immediate; past sends are read-only (reach only). */
export default function TenantBroadcastsPage() {
  const { t } = useTranslation();
  const query = useTenantBroadcasts();
  const rows = useMemo(() => query.data?.items ?? [], [query.data]);
  const [open, setOpen] = useState(false);
  const [detail, setDetail] = useState<BroadcastRow | null>(null);
  const del = useDeleteBroadcast();
  const canDelete = detail != null && ['failed', 'draft', 'sent'].includes(detail.status);

  // Client-side search + advanced filters over the already-loaded broadcasts.
  const [search, setSearch] = useState('');
  const [statusFilter, setStatusFilter] = useState<string[]>([]);
  const [targetFilter, setTargetFilter] = useState<TargetFilter>('all');
  const [datePreset, setDatePreset] = useState<DatePreset>('all');
  const [sentRange, setSentRange] = useState({ min: '', max: '' });
  const [filterOpen, setFilterOpen] = useState(false);

  const filteredRows = useMemo(() => {
    const needle = search.trim().toLowerCase();
    return rows
      .filter((b) => statusFilter.length === 0 || statusFilter.includes(b.status))
      .filter((b) => targetFilter === 'all' || b.target === targetFilter)
      .filter((b) => matchesDatePreset(b.created_at, datePreset))
      .filter((b) => matchesRange(b.sent_count, sentRange))
      .filter((b) => !needle || `${b.title} ${b.body_uz}`.toLowerCase().includes(needle));
  }, [rows, search, statusFilter, targetFilter, datePreset, sentRange]);

  const activeFilters = (statusFilter.length > 0 ? 1 : 0) + (targetFilter !== 'all' ? 1 : 0)
    + (datePreset !== 'all' ? 1 : 0) + (rangeActive(sentRange) ? 1 : 0);
  const resetAll = () => { setStatusFilter([]); setTargetFilter('all'); setDatePreset('all'); setSentRange({ min: '', max: '' }); };

  // Shared confirm+delete flow, parameterized over the target row so it works
  // identically whether triggered from the open DetailModal (`detail`) or
  // directly from a row's action menu (no modal open yet).
  const deleteRow = useCallback(async (row: BroadcastRow) => {
    const ok = await confirmDialog({ danger: true, message: t('broadcasts.deleteConfirm', { defaultValue: "Bu xabarnomani o'chirasizmi?" }) });
    if (!ok) return;
    await del.mutateAsync(row.id);
    toast.success(t('broadcasts.deleted', { defaultValue: "Xabarnoma o'chirildi" }));
    setDetail(null);
  }, [t, del]);
  const onDelete = () => { if (detail) void deleteRow(detail); };

  const subtitle = t('broadcasts.subtitle', { count: query.data?.total ?? 0, defaultValue: '{{count}} ta xabarnoma' });

  // (#row-delete) Eligibility must be evaluated per-row (`b.status`), not off
  // the unrelated `detail` (currently-open modal) state — otherwise delete
  // either never appears (detail === null, the default) or leaks onto every
  // row once any deletable detail is open. Mirrors the `canDelete` whitelist.
  // (H17) stable across renders so the `cols` useMemo below doesn't rebuild
  // every render.
  const rowActions = useCallback((b: BroadcastRow): RowAction[] => [
    { key: 'view', label: t('common.viewDetails', 'Batafsil'), icon: Eye, onClick: () => setDetail(b) },
    ...(['failed', 'draft', 'sent'].includes(b.status) ? [
      { key: 'delete', label: t('common.delete', "O'chirish"), icon: Trash2, onClick: () => void deleteRow(b), destructive: true },
    ] : []),
  ], [t, deleteRow]);

  // Desktop (md+) table columns; the mobile RowCard list (below) is kept verbatim. (H17)
  const cols: Column<BroadcastRow>[] = useMemo(() => [
    {
      key: 'title', label: t('broadcasts.title', { defaultValue: 'Sarlavha' }), width: '1.8fr',
      render: (b) => <span className="block truncate font-medium">{b.title}</span>,
      sortValue: (b) => b.title.toLowerCase(),
    },
    {
      key: 'audience', label: t('broadcasts.audience', 'Kimga'), width: '1fr',
      render: (b) => (
        <span className="flex items-center gap-2">
          <StatusPill row={b} />
          <span className="truncate text-small text-muted">{t(`broadcasts.target.${b.target}`, b.target)}</span>
        </span>
      ),
      sortValue: (b) => b.status,
    },
    {
      key: 'sent', label: t('broadcasts.sentLabel', 'Yuborilgan soni'), width: '120px', align: 'right',
      render: (b) => (
        <span className="tabular-nums text-small">
          <span className="text-success">{b.sent_count}</span>
          {b.failed_count > 0 && <span className="text-danger"> / {b.failed_count}</span>}
        </span>
      ),
      sortValue: (b) => b.sent_count,
    },
    {
      key: 'created', label: t('customers.colJoined', 'Sana'), width: '110px', align: 'right',
      render: (b) => <span className="tabular-nums text-small text-muted">{shortDate(b.created_at ?? '')}</span>,
      sortValue: (b) => b.created_at ?? '',
    },
    {
      key: 'actions', label: '', width: '48px', align: 'right',
      render: (b) => <RowActionMenu actions={rowActions(b)} ariaLabel={t('common.actions', 'Amallar')} />,
    },
  ], [t, rowActions]);

  return (
    <>
      <div className="hidden md:block">
        <PageHeader title={t('nav.broadcasts')} subtitle={subtitle} />
      </div>
      <div className="md:hidden">
        <MobileHeader title={t('nav.broadcasts')} />
        <p className="mb-3 max-w-prose text-small leading-snug text-muted">{subtitle}</p>
      </div>

      {/* Keeps the Megaphone glyph — a generic + would say "add a row" when the
          action is "announce something to every customer". */}
      <FloatingCreateAction
        onClick={() => setOpen(true)}
        icon={Megaphone}
        label={t('broadcasts.compose', { defaultValue: 'Yangi xabarnoma' })}
      />

      <FilterBar>
        <SearchInput value={search} onChange={setSearch} className="flex-1"
          placeholder={t('broadcasts.searchPh', 'Sarlavha yoki matn bo‘yicha qidirish…')} />
        <FilterButton count={activeFilters} onClick={() => setFilterOpen(true)} />
      </FilterBar>

      <FilterDrawer open={filterOpen} onClose={() => setFilterOpen(false)} onClear={resetAll}>
        <CheckGroup
          label={t('broadcasts.statusLabel', 'Holat')}
          value={statusFilter}
          onChange={setStatusFilter}
          options={STATUS_KEYS.map((s) => ({ value: s, label: t(`broadcasts.status.${s}`, s) }))}
        />
        <RadioGroup<TargetFilter>
          label={t('broadcasts.audience', 'Kimga')}
          value={targetFilter}
          onChange={setTargetFilter}
          options={[
            { value: 'all', label: t('common.all', 'Hammasi') },
            { value: 'active', label: t('broadcasts.target.active', 'Faol mijozlar') },
            { value: 'overdue', label: t('broadcasts.target.overdue', 'Qarzdorlar') },
            { value: 'custom', label: t('broadcasts.target.custom', 'Tanlangan') },
          ]}
        />
        <DateRangeGroup label={t('customers.colJoined', 'Sana')} value={datePreset} onChange={setDatePreset} />
        <RangeGroup
          label={t('broadcasts.sentLabel', 'Yuborilgan soni')}
          value={sentRange}
          onChange={setSentRange}
        />
      </FilterDrawer>

      {query.isLoading ? (
        <SkeletonList rows={5} />
      ) : query.isError ? (
        <ErrorCard error={query.error} onRetry={() => query.refetch()} />
      ) : rows.length === 0 ? (
        <EmptyState size="compact" tone="accent"
                   icon={<Megaphone size={24} />}
                   title={t('broadcasts.emptyTitle', { defaultValue: 'Hali xabarnoma yuborilmagan' })}
                   body={t('broadcasts.emptyBody', { defaultValue: "Mijozlaringizga aksiya, eslatma yoki yangilik haqida bitta xabar yuboring — Telegram bot orqali yetkaziladi." })} />
      ) : filteredRows.length === 0 ? (
        <EmptyState size="compact"
                   icon={<Megaphone size={24} />}
                   title={t('common.noResults', 'Topilmadi')}
                   body={activeFilters > 0 ? t('broadcasts.emptyFilteredHint', 'Filtrlarni tozalab, barcha xabarnomalarni ko‘ring') : undefined}
                   action={activeFilters > 0
                     ? <Button variant="outline" size="md" onClick={resetAll}>{t('common.clear', 'Tozalash')}</Button>
                     : undefined} />
      ) : (
        <>
          {/* Desktop (md+): dense sortable table that uses the wide screen. */}
          <div className="hidden md:block">
            <DataTable columns={cols} rows={filteredRows} rowKey={(b) => b.id} onRowClick={(b) => setDetail(b)} />
          </div>

          {/* Mobile: richer RowCards (verbatim). */}
          <ul className="space-y-2 md:hidden">
            {filteredRows.map((b) => (
              <li key={b.id}>
                <RowCard
                  leading={<span className="grid h-10 w-10 shrink-0 place-items-center rounded-xl bg-accent/10 text-accent"><Megaphone size={18} /></span>}
                  title={b.title}
                  subtitle={b.body_uz}
                  leftBadge={<StatusPill row={b} />}
                  trailing={
                    <div className="flex items-center gap-2">
                      <span className="hidden text-caption tabular-nums text-muted sm:block">{shortDate(b.created_at ?? '')}</span>
                      <div className="hidden sm:block">
                        <RowActionMenu actions={rowActions(b)} ariaLabel={t('common.actions', 'Amallar')} />
                      </div>
                    </div>
                  }
                  onClick={() => setDetail(b)}
                />
              </li>
            ))}
          </ul>
        </>
      )}

      <DetailModal
        open={!!detail}
        onClose={() => setDetail(null)}
        size="lg"
        title={detail?.title ?? ''}
        subtitle={detail ? <StatusPill row={detail} /> : undefined}
        onDelete={canDelete ? onDelete : undefined}
        deleting={del.isPending}
      >
        {detail && (
          <div className="space-y-4">
            <p className="whitespace-pre-wrap text-sm text-ink">{detail.body_uz}</p>
            <DefList>
              <DefRow label={t('broadcasts.audience', 'Kimga')}>{t(`broadcasts.target.${detail.target}`, detail.target)}</DefRow>
              <DefRow label={t('broadcasts.sentN', { count: detail.sent_count, defaultValue: '{{count}} yuborildi' })}>
                <span className="text-success">{detail.sent_count}</span>
              </DefRow>
              <DefRow label={t('broadcasts.failedLabel', 'Yetmadi')}>{detail.failed_count > 0 ? <span className="text-danger">{detail.failed_count}</span> : null}</DefRow>
              <DefRow label={t('broadcasts.btnLabel', 'Tugma')}>
                {detail.button_label ? `${detail.button_label} → ${detail.button_url ?? ''}` : null}
              </DefRow>
              <DefRow label={t('customers.colJoined', 'Sana')}>{shortDate(detail.created_at ?? '')}</DefRow>
            </DefList>
          </div>
        )}
      </DetailModal>

      <ComposeModal open={open} onClose={() => setOpen(false)} />
    </>
  );
}

function StatusPill({ row }: { row: BroadcastRow }) {
  const { t } = useTranslation();
  const tone = row.status === 'failed' ? 'danger' : row.status === 'sent' ? 'success' : 'warn';
  return <Badge tone={tone}>{t(`broadcasts.status.${row.status}`, row.status)}</Badge>;
}

function ComposeModal({ open, onClose }: { open: boolean; onClose: () => void }) {
  const { t } = useTranslation();
  const create = useCreateBroadcast();
  const [title, setTitle] = useState('');
  const [body, setBody] = useState('');
  const [target, setTarget] = useState<'all' | 'active'>('all');
  const [btnLabel, setBtnLabel] = useState('');
  const [btnUrl, setBtnUrl] = useState('');
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    if (!open) { setTitle(''); setBody(''); setTarget('all'); setBtnLabel(''); setBtnUrl(''); setErr(null); }
  }, [open]);

  const submit = async () => {
    if (!title.trim() || !body.trim()) { setErr(t('broadcasts.needFields', { defaultValue: 'Sarlavha va matnni kiriting.' })); return; }
    if (btnUrl.trim() && !/^https?:\/\/.+/i.test(btnUrl.trim())) {
      setErr(t('broadcasts.badUrl', { defaultValue: "Tugma havolasi http(s):// bilan boshlanishi kerak." }));
      return;
    }
    setErr(null);
    // Irreversible mass-send — confirm first.
    const ok = await confirmDialog({
      title: t('broadcasts.confirmTitle', { defaultValue: 'Xabarnomani yuborish?' }),
      message: target === 'all'
        ? t('broadcasts.confirmBodyAll')
        : t('broadcasts.confirmBodyActive'),
      confirmLabel: t('broadcasts.send', { defaultValue: 'Yuborish' }),
    });
    if (!ok) return;
    try {
      const res = await create.mutateAsync({
        title: title.trim(),
        body_uz: body.trim(),
        target,
        button_label: btnLabel.trim() || null,
        button_url: btnUrl.trim() || null,
      });
      toast.success(t('broadcasts.sentToast', { count: (res as BroadcastRow).sent_count, defaultValue: '{{count}} ta mijozga yuborildi' }));
      onClose();
    } catch (ex) {
      const e = ex as { response?: { data?: { detail?: string } } };
      setErr(e.response?.data?.detail ?? t('common.errorLoading'));
    }
  };

  return (
    <Modal open={open} onClose={onClose} title={t('broadcasts.compose', { defaultValue: 'Yangi xabarnoma' })}
           subtitle={t('broadcasts.composeSub', { defaultValue: 'Xabar Telegram bot orqali darhol yuboriladi.' })} size="lg"
           footer={
             <>
               <Button variant="outline" size="md" onClick={onClose}>{t('common.cancel')}</Button>
               <Button variant="primary" size="md" onClick={submit} disabled={create.isPending || !title.trim() || !body.trim()}>
                 {create.isPending ? <Loader2 size={14} className="animate-spin" /> : <Send size={14} />}
                 {t('broadcasts.send', { defaultValue: 'Yuborish' })}
               </Button>
             </>
           }>
      <div className="grid gap-3">
        <Field label={t('broadcasts.title', { defaultValue: 'Sarlavha' })} required>
          <input className={inputCls} value={title} maxLength={160}
                 onChange={(e) => setTitle(e.target.value)} placeholder={t('broadcasts.titlePh', { defaultValue: 'Aksiya: 0% ustama!' })} />
        </Field>
        <Field label={t('broadcasts.body', { defaultValue: 'Matn' })} required>
          <Textarea value={body} maxLength={4000} rows={5}
                    onChange={setBody}
                    placeholder={t('broadcasts.bodyPh', { defaultValue: 'Hurmatli mijoz! Shu hafta...' })} />
        </Field>
        <Field label={t('broadcasts.audience', { defaultValue: 'Kimga' })}>
          <Select
            value={target}
            onChange={(v) => setTarget(v as 'all' | 'active')}
            options={[
              { value: 'all', label: t('broadcasts.audienceAll', { defaultValue: 'Barcha mijozlar' }) },
              { value: 'active', label: t('broadcasts.audienceActive', { defaultValue: 'Faqat faol mijozlar' }) },
            ]}
          />
        </Field>
        <div className="grid gap-3 sm:grid-cols-2">
          <Field label={t('broadcasts.btnLabel', { defaultValue: 'Tugma matni (ixtiyoriy)' })}>
            <input className={inputCls} value={btnLabel} maxLength={64}
                   onChange={(e) => setBtnLabel(e.target.value)} placeholder={t('broadcasts.btnLabelPh', { defaultValue: 'Batafsil' })} />
          </Field>
          <Field label={t('broadcasts.btnUrl', { defaultValue: 'Tugma havolasi (ixtiyoriy)' })}>
            <input className={inputCls} value={btnUrl} maxLength={500} inputMode="url"
                   onChange={(e) => setBtnUrl(e.target.value)} placeholder="https://" />
          </Field>
        </div>
        {err && <div className="rounded-xl border border-danger/30 bg-[color:var(--c-danger-soft)] px-3 py-2 text-sm text-danger">{err}</div>}
      </div>
    </Modal>
  );
}
