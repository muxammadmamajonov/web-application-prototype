import { useCallback, useEffect, useMemo, useState } from 'react';
import { useTranslation } from 'react-i18next';
import {
  ChevronRight, Loader2, Megaphone, Send, Users,
} from 'lucide-react';
import { PageHeader } from '@/components/ui/PageHeader';
import { FloatingCreateAction } from '@/components/ui/FloatingCreateAction';
import { MobileHeader } from '@/components/mobile/primitives';
import { Button } from '@/components/ui/Button';
import { Badge } from '@/components/ui/Badge';
import { EmptyState } from '@/components/ui/EmptyState';
import { DataTable, type Column } from '@/components/ui/DataTable';
import { SkeletonList } from '@/components/ui/Skeleton';
import { Modal, Field, inputCls } from '@/components/ui/Modal';
import { Select } from '@/components/ui/Select';
import { DetailModal, DefList, DefRow } from '@/components/ui/DetailModal';
import { Textarea } from '@/components/ui/FormFields';
import { ErrorCard } from '@/components/ui/ErrorCard';
import { SearchInput } from '@/components/ui/SearchInput';
import {
  FilterBar, FilterButton, FilterDrawer, ActiveFilters, CheckGroup, DateRangeGroup,
  matchesDatePreset, type DatePreset,
} from '@/components/ui/FilterDrawer';
import {
  usePlatformBroadcasts,
  usePlatformSegments,
  type PlatformBroadcastRow,
  type SegmentRow,
} from '@/lib/queries';
import { useSendPlatformBroadcast } from '@/lib/mutations';
import { confirmDialog, toast } from '@/lib/feedback';
import { apiErrorMessage } from '@/lib/errors';
import { shortDate } from '@/lib/format';

function segLabel(s: SegmentRow, lng: string): string {
  return lng.startsWith('ru') ? s.label_ru : s.label_uz;
}

/** http(s) URL validation for the optional image / button-URL fields. Returns
 *  true for empty (the fields are optional) so only a malformed value blocks. */
function isValidHttpUrl(v: string): boolean {
  const s = v.trim();
  if (!s) return true;
  try {
    const u = new URL(s);
    return u.protocol === 'http:' || u.protocol === 'https:';
  } catch {
    return false;
  }
}

const STATUS_TONE: Record<string, 'success' | 'warn' | 'danger' | 'neutral'> = {
  sent: 'success', done: 'success', queued: 'warn', sending: 'warn', failed: 'danger',
};

/** Platform → segmented HQ broadcasts. Read-first: the recent/sent broadcasts
 *  list is the primary content; composing a new one (audience segment, UZ/RU
 *  bodies, optional image + inline button) happens in a header-launched modal. */
export default function PlatformBroadcastsPage() {
  const { t, i18n } = useTranslation();
  const lng = i18n.language;
  const list = usePlatformBroadcasts();
  const segs = usePlatformSegments();

  const [composeOpen, setComposeOpen] = useState(false);
  const [detail, setDetail] = useState<PlatformBroadcastRow | null>(null);

  // Recent-broadcasts list: client-side search + filter over the loaded items.
  const [search, setSearch] = useState('');
  const [statusFilter, setStatusFilter] = useState<string[]>([]);
  const [segmentFilter, setSegmentFilter] = useState<string[]>([]);
  const [datePreset, setDatePreset] = useState<DatePreset>('all');
  const [filterOpen, setFilterOpen] = useState(false);

  const subtitle = t('platformBroadcasts.subtitle', 'Tanlangan auditoriyaga eʼlon');
  const allItems = useMemo(() => list.data?.items ?? [], [list.data]);

  // (H17) stable across renders so the `cols` useMemo below doesn't rebuild
  // every render.
  const segName = useCallback((key?: string) => {
    const k = key ?? 'owners';
    const seg = (segs.data ?? []).find((s) => s.key === k);
    return seg ? segLabel(seg, lng) : k;
  }, [segs.data, lng]);

  // Aggregates for KPI cards — derived client-side from the loaded list.

  // (#6) Summary strip and KpiCard grid removed — on a list page they are a
  // preamble the reader scrolls past. The figures live on Overview.

  // Distinct status / segment values present in the loaded broadcasts — drives
  // the filter option lists (with per-option counts) so we only offer what exists.
  const statusOptions = useMemo(() => {
    const m = new Map<string, number>();
    for (const b of allItems) m.set(b.status, (m.get(b.status) ?? 0) + 1);
    return Array.from(m, ([value, n]) => ({ value, label: `${t(`status.${value}`, value)} · ${n}` }));
  }, [allItems, t]);

  const segmentOptions = useMemo(() => {
    const m = new Map<string, number>();
    for (const b of allItems) {
      const k = b.segment ?? 'owners';
      m.set(k, (m.get(k) ?? 0) + 1);
    }
    return Array.from(m, ([value, n]) => {
      const seg = (segs.data ?? []).find((s) => s.key === value);
      return { value, label: `${seg ? segLabel(seg, lng) : value} · ${n}` };
    });
  }, [allItems, segs.data, lng]);

  const items = useMemo(() => {
    const needle = search.trim().toLowerCase();
    return allItems
      .filter((b) => statusFilter.length === 0 || statusFilter.includes(b.status))
      .filter((b) => segmentFilter.length === 0 || segmentFilter.includes(b.segment ?? 'owners'))
      .filter((b) => matchesDatePreset(b.created_at, datePreset))
      .filter(
        (b) =>
          !needle ||
          [b.title, b.body_uz, b.body_ru].some((v) => v?.toLowerCase().includes(needle)),
      );
  }, [allItems, search, statusFilter, segmentFilter, datePreset]);

  const activeFilters =
    statusFilter.length + segmentFilter.length + (datePreset !== 'all' ? 1 : 0);
  const clearFilters = () => {
    setStatusFilter([]);
    setSegmentFilter([]);
    setDatePreset('all');
  };

  const dateLabel: Record<DatePreset, string> = {
    all: '',
    today: t('filters.today', 'Bugun'),
    '7d': t('filters.last7', '7 kun'),
    '30d': t('filters.last30', '30 kun'),
    '90d': t('filters.last90', '90 kun'),
  };
  const chips = [
    ...statusFilter.map((s) => ({
      key: `st:${s}`,
      label: t(`status.${s}`, s),
      onRemove: () => setStatusFilter((v) => v.filter((x) => x !== s)),
    })),
    ...segmentFilter.map((s) => ({
      key: `sg:${s}`,
      label: segName(s),
      onRemove: () => setSegmentFilter((v) => v.filter((x) => x !== s)),
    })),
    datePreset !== 'all' && {
      key: 'date',
      label: dateLabel[datePreset],
      onRemove: () => setDatePreset('all'),
    },
  ].filter(Boolean) as { key: string; label: string; onRemove: () => void }[];

  // Desktop (lg+) table columns. The whole row opens the detail modal (a single
  // "view" action → no 3-dot); a trailing chevron signals it's clickable.
  // (H17) memoized so an unrelated re-render doesn't rebuild this array.
  const cols: Column<PlatformBroadcastRow>[] = useMemo(() => [
    {
      key: 'title',
      label: t('platformBroadcasts.titleLabel', 'Sarlavha'),
      width: '1.8fr',
      render: (b) => <span className="block truncate font-medium">{b.title}</span>,
      sortValue: (b) => b.title.toLowerCase(),
    },
    {
      key: 'segment',
      label: t('platformBroadcasts.audience', 'Auditoriya'),
      width: '1fr',
      render: (b) => (
        <span className="block truncate text-small text-muted">{segName(b.segment)}</span>
      ),
      sortValue: (b) => segName(b.segment).toLowerCase(),
    },
    {
      key: 'status',
      label: t('platformBroadcasts.statusLabel', 'Holat'),
      width: '120px',
      render: (b) => (
        <Badge tone={STATUS_TONE[b.status] ?? 'neutral'}>
          {t(`status.${b.status}`, b.status)}
        </Badge>
      ),
      sortValue: (b) => b.status,
    },
    {
      key: 'delivery',
      label: t('platformBroadcasts.delivery', 'Yetkazildi'),
      width: '120px',
      align: 'right',
      render: (b) => (
        <span className="text-caption tabular-nums">
          <span className="font-medium text-success">{b.sent_count}</span>
          <span className="text-muted"> / </span>
          <span className="text-danger">{b.failed_count}</span>
        </span>
      ),
      sortValue: (b) => b.sent_count,
    },
    {
      key: 'created',
      label: t('platformBroadcasts.colCreated', 'Sana'),
      width: '110px',
      align: 'right',
      render: (b) => (
        <span className="tabular-nums text-small text-muted">
          {b.created_at ? shortDate(b.created_at) : '—'}
        </span>
      ),
      sortValue: (b) => b.created_at ?? '',
    },
    {
      key: 'chevron',
      label: '',
      width: '32px',
      align: 'right',
      render: () => <ChevronRight size={16} className="text-muted" />,
    },
  ], [t, segName]);

  // Whether to show the filter toolbar: always show once data has loaded or
  // a filter is active (matches TenantsPage showToolbar logic).
  const showToolbar = list.isLoading || allItems.length > 0 || activeFilters > 0 || !!search.trim();

  return (
    <>
      {/* ── Desktop header ─────────────────────────────────────────────────── */}
      <div className="hidden md:block">
        <PageHeader
          title={t('nav.broadcasts', 'Eʼlonlar')}
          subtitle={subtitle}
        />
      </div>

      {/* ── Mobile header (Telegram-safe) — title only ─────────────────────── */}
      <div className="md:hidden">
        <MobileHeader title={t('nav.broadcasts', 'Eʼlonlar')} />
        <p className="mb-3 max-w-prose text-small leading-snug text-muted">{subtitle}</p>
      </div>

      <FloatingCreateAction
        onClick={() => setComposeOpen(true)}
        label={t('platformBroadcasts.composeTitle', 'Yangi eʼlon')}
      />

      <div className="space-y-4 sm:space-y-5">

        {/* ── Filter toolbar ─────────────────────────────────────────────── */}
        {showToolbar && (
          <div>
            <FilterBar>
              <SearchInput
                value={search}
                onChange={setSearch}
                className="flex-1"
                placeholder={t(
                  'platformBroadcasts.searchPh',
                  "Sarlavha yoki matn bo'yicha qidirish…",
                )}
              />
              <FilterButton count={activeFilters} onClick={() => setFilterOpen(true)} />
            </FilterBar>
            <ActiveFilters filters={chips} onClearAll={clearFilters} />
          </div>
        )}

        {/* ── Main content: loading / error / empty / list ─────────────────── */}
        {list.isLoading ? (
          <SkeletonList rows={6} />
        ) : list.isError ? (
          /* (#422) An errored list previously read as "no broadcasts yet",
             masking delivery problems. */
          <ErrorCard error={list.error} onRetry={() => list.refetch()} />
        ) : allItems.length === 0 ? (
          <EmptyState
            tone="accent"
            size="compact"
            icon={<Megaphone size={26} />}
            title={t('platformBroadcasts.emptyTitle', "Hali eʼlon yoʻq")}
            body={t(
              'platformBroadcasts.emptyBody',
              'Eʼlonlar bot orqali tanlangan ijarachilar va foydalanuvchilarga yetkaziladi. Birinchi eʼloningizni yarating.',
            )}
          />
        ) : items.length === 0 ? (
          <EmptyState
            size="compact"
            icon={<Megaphone size={26} />}
            title={t('common.noResults', 'Topilmadi')}
            body={t(
              'platformBroadcasts.noResultsBody',
              'Filtr yoki qidiruvga mos eʼlon topilmadi.',
            )}
            action={
              activeFilters > 0 || search.trim() ? (
                <Button
                  variant="outline"
                  size="md"
                  onClick={() => {
                    clearFilters();
                    setSearch('');
                  }}
                >
                  {t('common.clear', 'Tozalash')}
                </Button>
              ) : undefined
            }
          />
        ) : (
          <>
            {/* Desktop (lg+): dense sortable + paginated table that uses the width.
                Below lg the sidebar leaves too little room — show cards instead. */}
            <div className="hidden lg:block">
              <DataTable
                columns={cols}
                rows={items}
                rowKey={(b) => b.id}
                onRowClick={(b) => setDetail(b)}
              />
            </div>

            {/* Mobile + tablet: richer cards. Wrapper is div[role=button] so
                nested interactive elements nest legally; tap target ≥44px. */}
            <ul className="space-y-2.5 lg:hidden">
              {items.map((b) => (
                <li key={b.id}>
                  <div
                    role="button"
                    tabIndex={0}
                    onClick={() => setDetail(b)}
                    onKeyDown={(e) => {
                      if (e.key === 'Enter' || e.key === ' ') {
                        e.preventDefault();
                        setDetail(b);
                      }
                    }}
                    className="cursor-pointer rounded-card border border-line bg-surface p-4 transition hover:border-ink/15 hover:shadow-card focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ink/20"
                  >
                    <div className="flex items-start gap-3">
                      <span className="grid h-10 w-10 shrink-0 place-items-center rounded-xl bg-accent/10 text-accent">
                        <Megaphone size={18} />
                      </span>
                      <div className="min-w-0 flex-1">
                        <div className="truncate font-medium">{b.title}</div>
                        <div className="truncate text-caption text-muted">{segName(b.segment)}</div>
                      </div>
                      <ChevronRight size={16} className="mt-1 shrink-0 text-muted" />
                    </div>
                    <div className="mt-3 flex flex-wrap items-center gap-2">
                      <Badge tone={STATUS_TONE[b.status] ?? 'neutral'}>
                        {t(`status.${b.status}`, b.status)}
                      </Badge>
                      <span className="text-caption tabular-nums text-muted">
                        <span className="font-medium text-success">{b.sent_count}</span>
                        <span className="text-muted"> / </span>
                        <span className="text-danger">{b.failed_count}</span>
                      </span>
                    </div>
                    <div className="mt-3 flex items-center justify-between gap-2 border-t border-line pt-3 text-caption text-muted">
                      <span>{t('platformBroadcasts.delivery', 'Yetkazildi')}</span>
                      <span className="shrink-0 tabular-nums">
                        {b.created_at ? shortDate(b.created_at) : '—'}
                      </span>
                    </div>
                  </div>
                </li>
              ))}
            </ul>
          </>
        )}
      </div>

      {/* ── Broadcast detail modal ─────────────────────────────────────────── */}
      <DetailModal
        open={!!detail}
        onClose={() => setDetail(null)}
        title={detail?.title ?? ''}
        subtitle={
          detail ? (
            <Badge tone={STATUS_TONE[detail.status] ?? 'neutral'}>
              {t(`status.${detail.status}`, detail.status)}
            </Badge>
          ) : undefined
        }
      >
        {detail && (
          <DefList>
            <DefRow label={t('platformBroadcasts.audience', 'Auditoriya')}>
              {segName(detail.segment)}
            </DefRow>
            <DefRow label={t('platformBroadcasts.delivery', 'Yetkazildi')}>
              <span className="font-medium text-success">{detail.sent_count}</span>
              <span className="text-muted"> / </span>
              <span className="text-danger">{detail.failed_count}</span>
            </DefRow>
            <DefRow label={t('platformBroadcasts.colCreated', 'Sana')}>
              {detail.created_at ? shortDate(detail.created_at) : '—'}
            </DefRow>
            <DefRow label={t('platformBroadcasts.bodyLabel', 'Matn (UZ)')}>
              <span className="whitespace-pre-wrap break-words">{detail.body_uz}</span>
            </DefRow>
            {detail.body_ru && (
              <DefRow label={t('platformBroadcasts.bodyRuLabel', 'Matn (RU)')}>
                <span className="whitespace-pre-wrap break-words">{detail.body_ru}</span>
              </DefRow>
            )}
            {detail.media_url && (
              <DefRow label={t('platformBroadcasts.media', 'Rasm havolasi')}>
                <a
                  href={detail.media_url}
                  target="_blank"
                  rel="noreferrer"
                  className="break-all text-accent hover:underline"
                >
                  {detail.media_url}
                </a>
              </DefRow>
            )}
            {detail.button_label && (
              <DefRow label={t('platformBroadcasts.btnLabel', 'Tugma matni')}>
                {detail.button_label}
              </DefRow>
            )}
            {detail.button_url && (
              <DefRow label={t('platformBroadcasts.btnUrl', 'Tugma havolasi')}>
                <a
                  href={detail.button_url}
                  target="_blank"
                  rel="noreferrer"
                  className="break-all text-accent hover:underline"
                >
                  {detail.button_url}
                </a>
              </DefRow>
            )}
          </DefList>
        )}
      </DetailModal>

      {/* ── Compose modal ─────────────────────────────────────────────────── */}
      <ComposeModal
        open={composeOpen}
        onClose={() => setComposeOpen(false)}
        onSent={() => {
          setComposeOpen(false);
          list.refetch();
        }}
      />

      {/* ── Filter drawer ─────────────────────────────────────────────────── */}
      <FilterDrawer
        open={filterOpen}
        onClose={() => setFilterOpen(false)}
        onClear={clearFilters}
      >
        <CheckGroup
          label={t('platformBroadcasts.statusLabel', 'Holat')}
          value={statusFilter}
          onChange={setStatusFilter}
          options={statusOptions}
        />
        <CheckGroup
          label={t('platformBroadcasts.audience', 'Auditoriya')}
          value={segmentFilter}
          onChange={setSegmentFilter}
          options={segmentOptions}
        />
        <DateRangeGroup value={datePreset} onChange={setDatePreset} />
      </FilterDrawer>
    </>
  );
}

// ── Constants ──────────────────────────────────────────────────────────────────

const TITLE_MAX = 160;
const BODY_MAX = 4000;
const BTN_LABEL_MAX = 64;
const URL_MAX = 500;

// ── ComposeModal ───────────────────────────────────────────────────────────────

function ComposeModal({
  open,
  onClose,
  onSent,
}: {
  open: boolean;
  onClose: () => void;
  onSent: () => void;
}) {
  const { t, i18n } = useTranslation();
  const lng = i18n.language;
  const segs = usePlatformSegments();
  const send = useSendPlatformBroadcast();

  const [segment, setSegment] = useState('owners');
  const [title, setTitle] = useState('');
  const [bodyUz, setBodyUz] = useState('');
  const [bodyRu, setBodyRu] = useState('');
  const [mediaUrl, setMediaUrl] = useState('');
  const [btnLabel, setBtnLabel] = useState('');
  const [btnUrl, setBtnUrl] = useState('');

  // Reset all fields when the modal closes.
  useEffect(() => {
    if (!open) {
      setSegment('owners');
      setTitle('');
      setBodyUz('');
      setBodyRu('');
      setMediaUrl('');
      setBtnLabel('');
      setBtnUrl('');
    }
  }, [open]);

  const reach = (segs.data ?? []).find((s) => s.key === segment)?.count ?? 0;

  // Inline URL validation — only an actually-malformed value blocks submit.
  const mediaUrlError = !isValidHttpUrl(mediaUrl)
    ? t('platformBroadcasts.urlError', 'http(s):// bilan boshlanadigan toʻgʻri havola kiriting')
    : null;
  const btnUrlError = !isValidHttpUrl(btnUrl)
    ? t('platformBroadcasts.urlError', 'http(s):// bilan boshlanadigan toʻgʻri havola kiriting')
    : null;

  // (#422) Block Send while segments are errored: composing against a phantom
  // reach of 0 risks a no-op or wrong-segment send to the whole user base.
  const canSend =
    !!title.trim() &&
    !!bodyUz.trim() &&
    !mediaUrlError &&
    !btnUrlError &&
    !send.isPending &&
    !segs.isError;

  const submit = async () => {
    if (!canSend) return;
    // (#653) Branded, danger-styled, Esc-cancellable, focus-trapped confirm.
    const confirmed = await confirmDialog({
      message: t('platformBroadcasts.confirm', {
        count: reach,
        defaultValue: `Xabar ${reach} foydalanuvchiga yuborilsinmi?`,
      }),
      danger: true,
      confirmLabel: t('platformBroadcasts.send', 'Yuborish'),
    });
    if (!confirmed) return;
    try {
      await send.mutateAsync({
        title: title.trim(),
        body_uz: bodyUz.trim(),
        body_ru: bodyRu.trim() || undefined,
        media_url: mediaUrl.trim() || undefined,
        button_label: btnLabel.trim() || undefined,
        button_url: btnUrl.trim() || undefined,
        segment,
      });
      toast.success(t('platformBroadcasts.sent', 'Eʼlon yuborildi'));
      onSent();
    } catch (e) {
      toast.error(apiErrorMessage(e));
    }
  };

  return (
    <Modal
      open={open}
      onClose={onClose}
      size="lg"
      title={t('platformBroadcasts.composeTitle', 'Yangi eʼlon')}
      subtitle={t(
        'platformBroadcasts.composeSub',
        'Xabar tanlangan auditoriyaga bot orqali yuboriladi.',
      )}
      footer={
        <>
          <Button variant="outline" size="md" onClick={onClose}>
            {t('common.cancel', 'Bekor qilish')}
          </Button>
          <Button variant="primary" size="md" disabled={!canSend} onClick={submit}>
            {send.isPending ? (
              <Loader2 size={14} className="animate-spin" />
            ) : (
              <Send size={14} />
            )}
            {send.isPending
              ? t('platformBroadcasts.sending', 'Yuborilmoqda…')
              : t('platformBroadcasts.send', 'Yuborish')}
          </Button>
        </>
      }
    >
      <div className="flex flex-col gap-5">
        {/* ── Audience + reach ──────────────────────────────────────────── */}
        <section className="flex flex-col gap-3">
          <h3 className="kicker text-muted">
            {t('platformBroadcasts.audienceGroup', 'Auditoriya')}
          </h3>
          <Field label={t('platformBroadcasts.audience', 'Auditoriya')}>
            {segs.isError ? (
              /* (#422) An errored segments call previously looked like an empty
                 dropdown with "~0 oluvchi" — surface it and offer a retry. */
              <div
                role="alert"
                className="flex items-center justify-between gap-2 rounded-xl border border-danger/30 bg-[color:var(--c-danger-soft)] px-3 py-2 text-small text-danger"
              >
                {t('platformBroadcasts.segmentsError', 'Segmentlar yuklanmadi')}
                <Button variant="outline" size="sm" onClick={() => segs.refetch()}>
                  {t('common.retry', 'Qayta urinish')}
                </Button>
              </div>
            ) : (
              <Select
                value={segment}
                onChange={setSegment}
                options={(segs.data ?? []).map((s) => ({
                  value: s.key,
                  label: `${segLabel(s, lng)} · ${s.count}`,
                }))}
                placeholder={t('platformBroadcasts.audience', 'Auditoriya')}
              />
            )}
          </Field>
          <p
            className={`inline-flex items-center gap-1.5 text-caption ${
              segs.isError ? 'text-danger' : 'text-muted'
            }`}
          >
            <Users size={13} />
            {segs.isError
              ? t('platformBroadcasts.reachError', "Auditoriyani hisoblab bo'lmadi")
              : t('platformBroadcasts.reach', {
                  count: reach,
                  defaultValue: `~${reach} oluvchi`,
                })}
          </p>
        </section>

        {/* ── Message content ───────────────────────────────────────────── */}
        <section className="flex flex-col gap-3">
          <h3 className="kicker text-muted">
            {t('platformBroadcasts.messageGroup', 'Xabar matni')}
          </h3>
          <Field
            label={t('platformBroadcasts.titleLabel', 'Sarlavha')}
            hint={`${title.length}/${TITLE_MAX}`}
          >
            <input
              className={inputCls}
              value={title}
              maxLength={TITLE_MAX}
              onChange={(e) => setTitle(e.target.value)}
              placeholder={t('platformBroadcasts.titlePlaceholder', 'Muddato yangiligi')}
            />
          </Field>
          <Field
            label={t('platformBroadcasts.bodyLabel', 'Matn (UZ)')}
            hint={`${bodyUz.length}/${BODY_MAX}`}
          >
            <Textarea
              value={bodyUz}
              maxLength={BODY_MAX}
              rows={5}
              onChange={setBodyUz}
              placeholder={t('platformBroadcasts.bodyPlaceholder', 'Eʼlon matni…') ?? ''}
            />
          </Field>
          <Field
            label={t('platformBroadcasts.bodyRuLabel', 'Matn (RU) — ixtiyoriy')}
            hint={`${bodyRu.length}/${BODY_MAX} · ${t(
              'platformBroadcasts.bodyRuHint',
              'RU foydalanuvchilarga shu matn yuboriladi',
            )}`}
          >
            <Textarea
              value={bodyRu}
              maxLength={BODY_MAX}
              rows={4}
              onChange={setBodyRu}
              placeholder={t('platformBroadcasts.bodyRuPlaceholder', 'Matn rus tilida…')}
            />
          </Field>
        </section>

        {/* ── Media + inline button ─────────────────────────────────────── */}
        <section className="flex flex-col gap-3">
          <h3 className="kicker text-muted">
            {t('platformBroadcasts.attachGroup', 'Rasm va tugma (ixtiyoriy)')}
          </h3>
          <Field
            label={t('platformBroadcasts.media', 'Rasm havolasi')}
            error={mediaUrlError}
          >
            <input
              className={inputCls}
              value={mediaUrl}
              inputMode="url"
              maxLength={URL_MAX}
              onChange={(e) => setMediaUrl(e.target.value)}
              placeholder="https://…/banner.jpg"
            />
          </Field>
          <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
            <Field label={t('platformBroadcasts.btnLabel', 'Tugma matni')}>
              <input
                className={inputCls}
                value={btnLabel}
                maxLength={BTN_LABEL_MAX}
                onChange={(e) => setBtnLabel(e.target.value)}
                placeholder={t('platformBroadcasts.btnLabelPh', 'Batafsil') ?? ''}
              />
            </Field>
            <Field
              label={t('platformBroadcasts.btnUrl', 'Tugma havolasi')}
              error={btnUrlError}
            >
              <input
                className={inputCls}
                value={btnUrl}
                inputMode="url"
                maxLength={URL_MAX}
                onChange={(e) => setBtnUrl(e.target.value)}
                placeholder="https://…"
              />
            </Field>
          </div>
        </section>
      </div>
    </Modal>
  );
}
