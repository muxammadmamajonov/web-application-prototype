import { useEffect, useMemo, useState } from 'react';
import { useTranslation } from 'react-i18next';
import {
  Boxes, ChevronRight, FolderTree, ImageIcon, Pencil, Tag, Trash2,
} from 'lucide-react';
import { SettingsShell, type SettingsTab } from '@/components/layout/SettingsShell';
import { Button } from '@/components/ui/Button';
import { SectionCard } from '@/components/ui/SectionCard';
import { Field, inputCls } from '@/components/ui/Modal';
import { DetailDrawer, DetailSection, DetailRow, DangerZone } from '@/components/ui/DetailDrawer';
import { Select } from '@/components/ui/Select';
import { MultiSelect } from '@/components/ui/MultiSelect';
import { NumberChips } from '@/components/ui/NumberChips';
import { Badge } from '@/components/ui/Badge';
import {
  FilterBar, FilterButton, FilterDrawer, ActiveFilters,
  CheckGroup, RadioGroup,
} from '@/components/ui/FilterDrawer';
import { SearchInput } from '@/components/ui/SearchInput';
import { EmptyState } from '@/components/ui/EmptyState';
import { SkeletonList } from '@/components/ui/Skeleton';
import { ErrorCard } from '@/components/ui/ErrorCard';
import { CategoryIcon } from '@/components/ui/CategoryIcon';
import { FloatingCreateAction } from '@/components/ui/FloatingCreateAction';
import { Wizard } from '@/components/ui/Wizard';
import { ImageUploadField } from '@/components/ui/ImageUploadField';
import { confirmDialog, toast } from '@/lib/feedback';
import { api } from '@/lib/api';
import { apiErrorMessage } from '@/lib/errors';
import { cn } from '@/lib/cn';
import {
  useCatalogCategories, useAllCatalogBrands, useCatalogModelsFlat, useCatalogColors,
  useOnboardingOptions,
  type CatalogCategoryRow, type CatalogBrandRow, type CatalogModelRow, type CatalogColorRow,
} from '@/lib/queries';
import {
  useCreateBrand, useCreateCatalogModel, useUpdateBrand, useUpdateCatalogModel,
  useDeleteCatalogCategory, useDeleteCatalogBrand, useDeleteCatalogModel,
} from '@/lib/mutations';
import { slugify, type AttrField } from '@/lib/catalogSchema';
import { CategorySchemaModal, type CategoryFull } from './_modals/CategorySchemaModal';

/**
 * Catalog manager — same two-pane master/detail as Settings/Profile
 * (SettingsShell, ?tab=): the section rail (Categories / Brands / Products)
 * beside the active section on desktop, drill-in on mobile.
 */
export default function PlatformCatalogPage() {
  const { t, i18n } = useTranslation();
  const lng = i18n.language?.startsWith('ru') ? 'ru' : 'uz';

  const cats = useCatalogCategories();
  const catName = (id: string | null | undefined) => {
    const c = cats.data?.find((x) => x.id === id);
    return c ? (lng === 'ru' ? c.name_ru : c.name_uz) : '—';
  };

  const tabs: SettingsTab[] = [
    { key: 'categories', label: t('platformCatalog.categories', 'Kategoriyalar'), sub: t('platformCatalog.categoriesSub', 'Maydonlar va variantlar sxemasi'), icon: FolderTree },
    { key: 'brands',     label: t('platformCatalog.brands', 'Brendlar'),          sub: t('platformCatalog.brandsSub', 'Brendlar va ularning kategoriyalari'), icon: Tag },
    { key: 'models',     label: t('platformCatalog.models', 'Mahsulotlar'),        sub: t('platformCatalog.modelsSub', "Modellar — kategoriya va brend bo'yicha"), icon: Boxes },
  ];

  return (
    <SettingsShell title={t('nav.catalog')} tabs={tabs}>
      {(key) => {
        switch (key) {
          case 'categories': return <CategoriesTab cats={cats} lng={lng} />;
          case 'brands':     return <BrandsTab cats={cats} lng={lng} catName={catName} />;
          case 'models':     return <ModelsTab cats={cats} lng={lng} catName={catName} />;
          default:           return null;
        }
      }}
    </SettingsShell>
  );
}

// ── shared list primitives ───────────────────────────────────────────────────

/**
 * The tab's create action. `SettingsShell` renders exactly one tab body at a
 * time, so the three tabs below can each declare their own without two floating
 * buttons ever landing on top of each other. (Both shell panes hold a copy in
 * the DOM, but one of them is always inside a `display:none` breakpoint
 * wrapper, so only one is ever rendered — same as the old action row.)
 */
function TabCreateAction({ onClick, label }: { onClick: () => void; label: string }) {
  return <FloatingCreateAction onClick={onClick} label={label} />;
}

/** A list of tappable rows inside a card; each opens its detail drawer.
 *  Empty has two distinct flavours: a first-run "no data yet" (accent) panel,
 *  and a filtered "no results" panel with a Clear action. */
function ItemList({
  loading, error, onRetry, empty, icon, emptyTitle, emptyBody,
  filtered, onClear, children,
}: {
  loading: boolean; error?: boolean; onRetry?: () => void; empty?: boolean;
  icon: React.ReactNode; emptyTitle: string; emptyBody?: string;
  filtered?: boolean; onClear?: () => void;
  children: React.ReactNode;
}) {
  const { t } = useTranslation();
  if (loading) return <SkeletonList rows={5} />;
  if (error) return <ErrorCard error={null} onRetry={onRetry} />;
  if (empty) {
    return filtered ? (
      <EmptyState
        size="compact"
        icon={icon}
        title={t('common.noResults', 'Topilmadi')}
        body={t('platformCatalog.noResultsBody', 'Filtr yoki qidiruvga mos natija topilmadi.')}
        action={onClear ? <Button variant="outline" size="md" onClick={onClear}>{t('common.clear', 'Tozalash')}</Button> : undefined}
      />
    ) : (
      <EmptyState size="compact" tone="accent" icon={icon} title={emptyTitle} body={emptyBody} />
    );
  }
  return (
    <SectionCard className="p-0 overflow-hidden" bodyClassName="p-0">
      <ul className="divide-y divide-line">{children}</ul>
    </SectionCard>
  );
}

/** One tappable row inside the item list card. */
function Row({ media, title, subtitle, badge, onClick }: {
  media: React.ReactNode; title: string; subtitle: string;
  badge?: React.ReactNode; onClick: () => void;
}) {
  return (
    <li>
      <button
        onClick={onClick}
        className="flex min-h-[52px] w-full items-center gap-3 px-4 py-3 text-left transition hover:bg-ink/[0.03] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ink/20 md:px-5"
      >
        <span className="shrink-0">{media}</span>
        <span className="min-w-0 flex-1">
          <span className="block truncate text-small font-semibold">{title}</span>
          <span className="block truncate text-caption text-muted">{subtitle}</span>
        </span>
        {badge && <span className="shrink-0">{badge}</span>}
        <ChevronRight size={16} className="shrink-0 text-muted" />
      </button>
    </li>
  );
}

/** Brand row media: the uploaded logo when set, else a letter-initial tile. */
function BrandAvatar({ logoUrl, name }: { logoUrl: string | null | undefined; name: string }) {
  if (logoUrl) {
    return (
      <img src={logoUrl} alt="" loading="lazy"
           className="h-10 w-10 rounded-xl border border-line bg-surface object-contain" />
    );
  }
  return (
    <span className="grid h-10 w-10 place-items-center rounded-xl bg-[color:var(--c-accent-soft)] text-accent text-base font-semibold">
      {name.slice(0, 1).toUpperCase()}
    </span>
  );
}

async function confirmDelete(t: ReturnType<typeof useTranslation>['t'], run: () => Promise<unknown>) {
  if (!(await confirmDialog({ message: t('platformCatalog.deleteConfirm'), danger: true, confirmLabel: t('common.delete') }))) return;
  try { await run(); }
  catch (ex) {
    toast.error(apiErrorMessage(ex));
  }
}

// ── Categories ─────────────────────────────────────────────────────────────

function CategoriesTab({ cats, lng }: { cats: ReturnType<typeof useCatalogCategories>; lng: 'uz' | 'ru' }) {
  const { t } = useTranslation();
  const onboarding = useOnboardingOptions();
  const [creating, setCreating] = useState(false);
  const [editing, setEditing] = useState<CategoryFull | null>(null);
  const [detail, setDetail] = useState<CatalogCategoryRow | null>(null);
  const [q, setQ] = useState('');
  const [sort, setSort] = useState<'az' | 'za'>('az');
  const [fBiz, setFBiz] = useState<string[]>([]);
  const [filterOpen, setFilterOpen] = useState(false);
  const del = useDeleteCatalogCategory();

  const bizOptions = (onboarding.data?.business_types ?? []).map((bt) => ({ value: bt.slug, label: lng === 'ru' ? bt.name_ru : bt.name_uz }));
  const activeFilterCount = fBiz.length + (sort !== 'az' ? 1 : 0);
  const clearAllFilters = () => { setFBiz([]); setSort('az'); };

  const filtered = (cats.data ?? [])
    .filter((c) => (lng === 'ru' ? c.name_ru : c.name_uz).toLowerCase().includes(q.trim().toLowerCase()))
    .filter((c) => fBiz.length === 0 || (c.business_types ?? []).some((bt) => fBiz.includes(bt)))
    .slice()
    .sort((a, b) => {
      const an = lng === 'ru' ? a.name_ru : a.name_uz;
      const bn = lng === 'ru' ? b.name_ru : b.name_uz;
      return sort === 'za' ? bn.localeCompare(an) : an.localeCompare(bn);
    });

  const chips = [
    fBiz.length > 0 && { key: 'biz', label: t('platformCatalog.bizTypes', 'Biznes turlari'), onRemove: () => setFBiz([]) },
    sort !== 'az' && { key: 'sort', label: 'Z–A', onRemove: () => setSort('az') },
  ].filter(Boolean) as { key: string; label: string; onRemove: () => void }[];

  const toFull = (c: CatalogCategoryRow): CategoryFull => ({
    id: c.id, slug: c.slug, name_uz: c.name_uz, name_ru: c.name_ru,
    icon_emoji: c.icon_emoji, icon_url: c.icon_url,
    attribute_schema: c.attribute_schema ?? [], identifier_schema: c.identifier_schema ?? [],
    business_types: c.business_types ?? [],
  });


  const isFiltered = !!q.trim() || activeFilterCount > 0;

  // (#6) Summary strip and KpiCard grid removed — on a list page they are a
  // preamble the reader scrolls past. The figures live on Overview.

  return (
    <>
      <TabCreateAction onClick={() => setCreating(true)} label={t('platformCatalog.newCategory')} />

      {/* Search + filter toolbar */}
      <FilterBar>
        <SearchInput value={q} onChange={setQ} className="flex-1"
          placeholder={t('platformCatalog.searchCategories', 'Kategoriya qidirish…') ?? ''} />
        <FilterButton count={activeFilterCount} onClick={() => setFilterOpen(true)} />
      </FilterBar>
      <ActiveFilters filters={chips} onClearAll={clearAllFilters} />

      <FilterDrawer open={filterOpen} onClose={() => setFilterOpen(false)} onClear={clearAllFilters}>
        <RadioGroup<'az' | 'za'>
          label={t('common.sort', 'Saralash')}
          value={sort} onChange={setSort}
          options={[{ value: 'az', label: 'A–Z' }, { value: 'za', label: 'Z–A' }]}
        />
        {bizOptions.length > 0 && (
          <CheckGroup label={t('platformCatalog.bizTypes', 'Biznes turlari')}
                      value={fBiz} onChange={setFBiz} options={bizOptions} />
        )}
      </FilterDrawer>

      <ItemList
        loading={cats.isLoading} error={cats.isError} onRetry={() => cats.refetch()}
        empty={filtered.length === 0}
        icon={<FolderTree size={24} />}
        emptyTitle={t('platformCatalog.noCategories')}
        emptyBody={t('platformCatalog.noCategoriesBody', 'Birinchi kategoriyani yarating — maydonlar va variantlar sxemasini belgilang.')}
        filtered={isFiltered}
        onClear={() => { setQ(''); clearAllFilters(); }}
      >
        {filtered.map((c) => (
          <Row key={c.id}
               media={<CategoryIcon iconUrl={c.icon_url} />}
               title={`${c.icon_emoji ? `${c.icon_emoji} ` : ''}${lng === 'ru' ? c.name_ru : c.name_uz}`}
               subtitle={`${c.brand_count} ${t('common.brands')} · ${(c.attribute_schema ?? []).length} ${t('platformCatalog.fieldsShort', 'maydon')}`}
               badge={
                 (c.business_types ?? []).length > 0
                   ? <Badge tone="neutral">{(c.business_types ?? []).length}</Badge>
                   : undefined
               }
               onClick={() => setDetail(c)} />
        ))}
      </ItemList>

      {/* Category detail — DetailDrawer for a proper right-side panel with DangerZone */}
      <DetailDrawer
        open={!!detail}
        onClose={() => setDetail(null)}
        title={detail ? `${detail.icon_emoji ?? ''} ${lng === 'ru' ? detail.name_ru : detail.name_uz}`.trim() : ''}
        subtitle={detail?.slug}
      >
        {detail && (
          <>
            <DetailSection title={t('platformCatalog.overview', "Umumiy ma'lumot")}>
              <DetailRow label={t('platformCatalog.brands', 'Brendlar')}>{String(detail.brand_count)}</DetailRow>
              <DetailRow label={t('platformCatalog.bizTypes', 'Biznes turlari')}>
                {(detail.business_types ?? []).join(', ') || t('platformCatalog.allSegments', 'Barchaga')}
              </DetailRow>
              <DetailRow label={t('platformCatalog.fieldsShort', 'Maydonlar')}>
                {String((detail.attribute_schema ?? []).length)}
              </DetailRow>
            </DetailSection>

            {(detail.attribute_schema ?? []).length > 0 && (
              <DetailSection title={t('platformCatalog.fields', 'Maydonlar')}>
                <ul className="space-y-1.5">
                  {detail.attribute_schema.map((f) => (
                    <li key={f.key} className="flex items-center justify-between rounded-xl border border-line bg-cream px-3 py-2">
                      <span className="text-small font-medium">{lng === 'ru' ? f.label_ru : f.label_uz}</span>
                      <span className="text-micro text-muted">
                        {t(`platformCatalog.type_${f.type}`, f.type)}
                        {f.required ? ` · ${t('platformCatalog.required', 'majburiy')}` : ''}
                        {` · ${f.filled_by === 'platform' ? t('platformCatalog.byPlatform', 'platforma') : t('platformCatalog.byTenant', 'tenant')}`}
                      </span>
                    </li>
                  ))}
                </ul>
              </DetailSection>
            )}

            <div className="flex gap-2 px-4 pb-2 pt-2">
              <Button
                variant="outline"
                size="md"
                className="flex-1"
                onClick={() => { setEditing(toFull(detail)); setDetail(null); }}
              >
                <Pencil size={14} /> {t('common.edit', 'Tahrirlash')}
              </Button>
            </div>

            <DangerZone
              title={t('platformCatalog.deleteCategory', "Kategoriyani o'chirish")}
              description={t('platformCatalog.deleteCategoryHint', "Kategoriyani o'chirish barcha bog'liq brendlar va modellarni ham o'chiradi.")}
            >
              <Button
                variant="danger"
                size="md"
                loading={del.isPending}
                onClick={() =>
                  confirmDelete(t, () => del.mutateAsync(detail.id)).then(() => setDetail(null))
                }
              >
                <Trash2 size={14} />
                {t('common.delete', "O'chirish")}
              </Button>
            </DangerZone>
          </>
        )}
      </DetailDrawer>

      <CategorySchemaModal open={creating || !!editing} editing={editing}
                           onClose={() => { setCreating(false); setEditing(null); }} />
    </>
  );
}

// ── Brands ───────────────────────────────────────────────────────────────────

function BrandsTab({ cats, lng, catName }: {
  cats: ReturnType<typeof useCatalogCategories>; lng: 'uz' | 'ru'; catName: (id: string | null | undefined) => string;
}) {
  const { t } = useTranslation();
  const brands = useAllCatalogBrands();
  const onboarding = useOnboardingOptions();
  const [creating, setCreating] = useState(false);
  const [editing, setEditing] = useState<CatalogBrandRow | null>(null);
  const [detail, setDetail] = useState<CatalogBrandRow | null>(null);
  const [q, setQ] = useState('');
  const [sort, setSort] = useState<'az' | 'za'>('az');
  const [fCats, setFCats] = useState<string[]>([]);
  const [fBiz, setFBiz] = useState<string[]>([]);
  const [filterOpen, setFilterOpen] = useState(false);
  const del = useDeleteCatalogBrand();

  const bizOptions = (onboarding.data?.business_types ?? []).map((bt) => ({ value: bt.slug, label: lng === 'ru' ? bt.name_ru : bt.name_uz }));
  const catBiz = useMemo(() => {
    const m = new Map<string, string[]>();
    (cats.data ?? []).forEach((c) => m.set(c.id, c.business_types ?? []));
    return m;
  }, [cats.data]);
  const activeFilterCount = fCats.length + fBiz.length + (sort !== 'az' ? 1 : 0);
  const clearAllFilters = () => { setFCats([]); setFBiz([]); setSort('az'); };

  const filtered = (brands.data ?? [])
    .filter((b) => b.name.toLowerCase().includes(q.trim().toLowerCase()))
    .filter((b) => fCats.length === 0 || (b.category_ids ?? []).some((cid) => fCats.includes(cid)))
    .filter((b) => fBiz.length === 0 || (b.category_ids ?? []).some((cid) => (catBiz.get(cid) ?? []).some((bt) => fBiz.includes(bt))))
    .sort((a, b) => (sort === 'za' ? b.name.localeCompare(a.name) : a.name.localeCompare(b.name)));

  const chips = [
    fCats.length > 0 && { key: 'cats', label: t('platformCatalog.categories', 'Kategoriyalar'), onRemove: () => setFCats([]) },
    fBiz.length > 0 && { key: 'biz', label: t('platformCatalog.bizTypes', 'Biznes turlari'), onRemove: () => setFBiz([]) },
    sort !== 'az' && { key: 'sort', label: 'Z–A', onRemove: () => setSort('az') },
  ].filter(Boolean) as { key: string; label: string; onRemove: () => void }[];

  const isFiltered = !!q.trim() || activeFilterCount > 0;


  return (
    <>
      <TabCreateAction onClick={() => setCreating(true)} label={t('platformCatalog.newBrand')} />

      <FilterBar>
        <SearchInput value={q} onChange={setQ} className="flex-1"
          placeholder={t('platformCatalog.searchBrands', 'Brend qidirish…') ?? ''} />
        <FilterButton count={activeFilterCount} onClick={() => setFilterOpen(true)} />
      </FilterBar>
      <ActiveFilters filters={chips} onClearAll={clearAllFilters} />

      <FilterDrawer open={filterOpen} onClose={() => setFilterOpen(false)} onClear={clearAllFilters}>
        <RadioGroup<'az' | 'za'>
          label={t('common.sort', 'Saralash')}
          value={sort} onChange={setSort}
          options={[{ value: 'az', label: 'A–Z' }, { value: 'za', label: 'Z–A' }]}
        />
        {bizOptions.length > 0 && (
          <CheckGroup label={t('platformCatalog.bizTypes', 'Biznes turlari')}
                      value={fBiz} onChange={setFBiz} options={bizOptions} />
        )}
        <CheckGroup
          label={t('platformCatalog.inCategories', 'Kategoriyalar')}
          value={fCats} onChange={setFCats} collapsible
          searchPlaceholder={t('platformCatalog.searchCategories', 'Kategoriya qidirish…') ?? ''}
          options={(cats.data ?? []).map((c) => ({ value: c.id, label: lng === 'ru' ? c.name_ru : c.name_uz }))}
        />
      </FilterDrawer>

      <ItemList
        loading={brands.isLoading} error={brands.isError} onRetry={() => brands.refetch()}
        empty={filtered.length === 0}
        icon={<Tag size={24} />}
        emptyTitle={t('platformCatalog.noBrands')}
        emptyBody={t('platformCatalog.noBrandsBody', "Birinchi brendni qo'shing va uni kategoriyalarga bog'lang.")}
        filtered={isFiltered}
        onClear={() => { setQ(''); clearAllFilters(); }}
      >
        {filtered.map((b) => (
          <Row key={b.id}
               media={<BrandAvatar logoUrl={b.logo_url} name={b.name} />}
               title={b.name}
               subtitle={`${(b.category_ids ?? []).length} ${t('platformCatalog.categories', 'kategoriya').toLowerCase()}`}
               badge={
                 (b.category_ids ?? []).length > 0
                   ? <Badge tone="neutral">{(b.category_ids ?? []).length}</Badge>
                   : undefined
               }
               onClick={() => setDetail(b)} />
        ))}
      </ItemList>

      {/* Brand detail — DetailDrawer */}
      <DetailDrawer
        open={!!detail}
        onClose={() => setDetail(null)}
        title={detail?.name ?? ''}
        subtitle={detail ? `${(detail.category_ids ?? []).length} ${t('platformCatalog.categories', 'kategoriya').toLowerCase()}` : ''}
      >
        {detail && (
          <>
            <DetailSection title={t('platformCatalog.inCategories', 'Kategoriyalar')}>
              {(detail.category_ids ?? []).length === 0 ? (
                <p className="text-caption text-muted">—</p>
              ) : (
                <div className="flex flex-wrap gap-1.5">
                  {(detail.category_ids ?? []).map((cid) => (
                    <span key={cid} className="rounded-pill border border-line bg-cream px-3 py-1 text-caption">
                      {catName(cid)}
                    </span>
                  ))}
                </div>
              )}
            </DetailSection>

            <div className="flex gap-2 px-4 pb-2 pt-2">
              <Button
                variant="outline"
                size="md"
                className="flex-1"
                onClick={() => { setEditing(detail); setDetail(null); }}
              >
                <Pencil size={14} /> {t('common.edit', 'Tahrirlash')}
              </Button>
            </div>

            <DangerZone
              title={t('platformCatalog.deleteBrand', "Brendni o'chirish")}
              description={t('platformCatalog.deleteBrandHint', "Brendni o'chirish unga bog'liq modellarni ham olib tashlaydi.")}
            >
              <Button
                variant="danger"
                size="md"
                loading={del.isPending}
                onClick={() =>
                  confirmDelete(t, () => del.mutateAsync(detail.id)).then(() => setDetail(null))
                }
              >
                <Trash2 size={14} />
                {t('common.delete', "O'chirish")}
              </Button>
            </DangerZone>
          </>
        )}
      </DetailDrawer>

      <BrandModal open={creating || !!editing} editing={editing} cats={cats.data ?? []} lng={lng}
                  onClose={() => { setCreating(false); setEditing(null); }} />
    </>
  );
}

function BrandModal({ open, onClose, editing, cats, lng }: {
  open: boolean; onClose: () => void; editing: CatalogBrandRow | null;
  cats: CatalogCategoryRow[]; lng: 'uz' | 'ru';
}) {
  const { t } = useTranslation();
  const create = useCreateBrand();
  const update = useUpdateBrand();
  const busy = create.isPending || update.isPending;
  const [name, setName] = useState('');
  const [logoUrl, setLogoUrl] = useState('');
  const [catIds, setCatIds] = useState<string[]>([]);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    if (!open) return;
    setErr(null);
    if (editing) { setName(editing.name); setLogoUrl(editing.logo_url ?? ''); setCatIds(editing.category_ids ?? []); }
    else { setName(''); setLogoUrl(''); setCatIds([]); }
  }, [open, editing]);

  const submit = async () => {
    setErr(null);
    const slug = slugify(name) || (editing?.slug ?? '');
    if (catIds.length === 0) { setErr(t('platformCatalog.pickCategory', 'Kamida bitta kategoriya tanlang')); return; }
    const logo_url = logoUrl.trim() || null;
    try {
      if (editing) await update.mutateAsync({ id: editing.id, body: { category_ids: catIds, slug, name: name.trim(), logo_url } });
      else await create.mutateAsync({ category_ids: catIds, slug, name: name.trim(), logo_url });
      onClose();
    } catch (ex) {
      setErr(apiErrorMessage(ex));
    }
  };

  return (
    <Wizard
      open={open}
      title={editing ? t('common.edit', 'Tahrirlash') : t('platformCatalog.newBrand')}
      submitLabel={editing ? t('common.save', 'Saqlash') : t('common.create', 'Yaratish')}
      submitting={busy}
      error={err}
      onClose={onClose}
      onSubmit={submit}
      steps={[
        {
          key: 'name',
          title: t('platformCatalog.brandName', 'Brend nomi'),
          valid: !!name.trim(),
          render: () => (
            <div className="space-y-3">
              <Field label={t('platformCatalog.brandName')} required>
                <input className={inputCls} value={name} onChange={(e) => setName(e.target.value)} placeholder="Apple" autoFocus />
              </Field>
              <Field label={t('platformCatalog.brandLogo', 'Brend logosi (ixtiyoriy)')}>
                <ImageUploadField value={logoUrl} onChange={setLogoUrl} round />
              </Field>
            </div>
          ),
        },
        {
          key: 'cats',
          title: t('platformCatalog.brandCategories', 'Kategoriyalar'),
          valid: catIds.length > 0,
          render: () => (
            <div>
              <p className="mb-3 text-caption text-muted">{t('platformCatalog.brandCategoriesHint', 'Bu brend qaysi kategoriyalarda ishlatiladi (bir nechta)')}</p>
              <CheckGroup label={t('platformCatalog.categories', 'Kategoriyalar')}
                          value={catIds} onChange={setCatIds} searchable collapsible={false}
                          searchPlaceholder={t('platformCatalog.searchCategories', 'Kategoriya qidirish…') ?? ''}
                          options={cats.map((c) => ({ value: c.id, label: lng === 'ru' ? c.name_ru : c.name_uz }))} />
            </div>
          ),
        },
      ]}
    />
  );
}

// ── Models (Products) ──────────────────────────────────────────────────────

function ModelsTab({ cats, lng, catName }: {
  cats: ReturnType<typeof useCatalogCategories>; lng: 'uz' | 'ru'; catName: (id: string | null | undefined) => string;
}) {
  const { t } = useTranslation();
  const brands = useAllCatalogBrands();
  const onboarding = useOnboardingOptions();
  const [fCats, setFCats] = useState<string[]>([]);
  const [fBrands, setFBrands] = useState<string[]>([]);
  const [fBiz, setFBiz] = useState<string[]>([]);
  const [q, setQ] = useState('');
  const [sort, setSort] = useState<'default' | 'az' | 'za'>('default');
  // The flat endpoint takes a single category/brand; for multi-select we fetch
  // unfiltered and narrow client-side (catalog is small).
  const models = useCatalogModelsFlat({ q });
  const [creating, setCreating] = useState(false);
  const [editing, setEditing] = useState<CatalogModelRow | null>(null);
  const [detail, setDetail] = useState<CatalogModelRow | null>(null);
  const [filterOpen, setFilterOpen] = useState(false);
  const del = useDeleteCatalogModel();
  // Search lives in the shared toolbar; the drawer badge counts the drawer-owned
  // facets (business type / category / brand / sort).
  const activeFilterCount = fBiz.length + fCats.length + fBrands.length + (sort !== 'default' ? 1 : 0);
  const clearAllFilters = () => { setFBiz([]); setFCats([]); setFBrands([]); setSort('default'); };

  const brandName = (id: string) => brands.data?.find((b) => b.id === id)?.name ?? '—';
  const bizOptions = (onboarding.data?.business_types ?? []).map((bt) => ({ value: bt.slug, label: lng === 'ru' ? bt.name_ru : bt.name_uz }));
  // category id → its business-type slugs (a model inherits its category's segment).
  const catBiz = useMemo(() => {
    const m = new Map<string, string[]>();
    (cats.data ?? []).forEach((c) => m.set(c.id, c.business_types ?? []));
    return m;
  }, [cats.data]);

  const shown = useMemo(() => (models.data ?? [])
    .filter((m) =>
      (fCats.length === 0 || (m.category_id && fCats.includes(m.category_id)))
      && (fBrands.length === 0 || fBrands.includes(m.brand_id))
      && (fBiz.length === 0 || (m.category_id != null && (catBiz.get(m.category_id) ?? []).some((bt) => fBiz.includes(bt)))))
    .slice()
    .sort((a, b) => {
      if (sort === 'az') return a.name.localeCompare(b.name);
      if (sort === 'za') return b.name.localeCompare(a.name);
      return 0; // 'default' — keep server order
    }),
  [models.data, fCats, fBrands, fBiz, catBiz, sort]);

  const chips = [
    fCats.length > 0 && { key: 'cats', label: t('platformCatalog.categories', 'Kategoriyalar'), onRemove: () => setFCats([]) },
    fBrands.length > 0 && { key: 'brands', label: t('platformCatalog.brands', 'Brendlar'), onRemove: () => setFBrands([]) },
    fBiz.length > 0 && { key: 'biz', label: t('platformCatalog.bizTypes', 'Biznes turlari'), onRemove: () => setFBiz([]) },
    sort !== 'default' && { key: 'sort', label: sort === 'az' ? 'A–Z' : 'Z–A', onRemove: () => setSort('default') },
  ].filter(Boolean) as { key: string; label: string; onRemove: () => void }[];

  const isFiltered = !!q.trim() || activeFilterCount > 0;


  return (
    <>

      <TabCreateAction onClick={() => setCreating(true)} label={t('platformCatalog.newModel')} />

      <FilterBar>
        <SearchInput value={q} onChange={setQ} className="flex-1"
                     placeholder={t('platformCatalog.searchModels', 'Model nomi…') ?? ''} />
        <FilterButton count={activeFilterCount} onClick={() => setFilterOpen(true)} />
      </FilterBar>
      <ActiveFilters filters={chips} onClearAll={clearAllFilters} />

      <FilterDrawer open={filterOpen} onClose={() => setFilterOpen(false)} onClear={clearAllFilters}>
        {/* Compact facets first; the long category/brand lists collapse below so
            they no longer bury everything (the 200-category wall). */}
        <RadioGroup<'default' | 'az' | 'za'>
          label={t('common.sort', 'Saralash')}
          value={sort}
          onChange={setSort}
          options={[
            { value: 'default', label: t('platformCatalog.sortDefault', 'Standart') },
            { value: 'az', label: 'A–Z' },
            { value: 'za', label: 'Z–A' },
          ]}
        />
        {bizOptions.length > 0 && (
          <CheckGroup label={t('platformCatalog.bizTypes', 'Biznes turlari')}
                      value={fBiz} onChange={setFBiz} options={bizOptions} />
        )}
        <CheckGroup label={t('platformCatalog.categories', 'Kategoriyalar')}
                    value={fCats} onChange={setFCats} collapsible
                    searchPlaceholder={t('platformCatalog.searchCategories', 'Kategoriya qidirish…') ?? ''}
                    options={(cats.data ?? []).map((c) => ({ value: c.id, label: lng === 'ru' ? c.name_ru : c.name_uz }))} />
        <CheckGroup label={t('platformCatalog.brands', 'Brendlar')}
                    value={fBrands} onChange={setFBrands} collapsible
                    searchPlaceholder={t('platformCatalog.searchBrands', 'Brend qidirish…') ?? ''}
                    options={(brands.data ?? []).map((b) => ({ value: b.id, label: b.name }))} />
      </FilterDrawer>

      <ItemList
        loading={models.isLoading} error={models.isError} onRetry={() => models.refetch()}
        empty={shown.length === 0}
        icon={<Boxes size={24} />}
        emptyTitle={t('platformCatalog.noModels')}
        emptyBody={t('platformCatalog.noModelsBody', 'Birinchi modelni yarating — kategoriya, brend va variantlarni tanlang.')}
        filtered={isFiltered}
        onClear={() => { setQ(''); clearAllFilters(); }}
      >
        {shown.map((m) => (
          <Row key={m.id}
               media={m.image_url
                 ? <img src={m.image_url} alt="" loading="lazy" className="h-10 w-10 rounded-xl object-contain" />
                 : <span className="grid h-10 w-10 place-items-center rounded-xl bg-cream text-muted"><ImageIcon size={16} /></span>}
               title={m.name}
               subtitle={`${brandName(m.brand_id)} · ${catName(m.category_id)}`}
               onClick={() => setDetail(m)} />
        ))}
      </ItemList>

      {/* Model detail — DetailDrawer */}
      <DetailDrawer
        open={!!detail}
        onClose={() => setDetail(null)}
        title={detail?.name ?? ''}
        subtitle={detail ? `${brandName(detail.brand_id)} · ${catName(detail.category_id)}` : ''}
      >
        {detail && (
          <>
            <DetailSection title={t('platformCatalog.overview', "Umumiy ma'lumot")}>
              <DetailRow label={t('platformCatalog.brands', 'Brend')}>{brandName(detail.brand_id)}</DetailRow>
              <DetailRow label={t('platformCatalog.categories', 'Kategoriya')}>{catName(detail.category_id)}</DetailRow>
            </DetailSection>

            {Object.keys(detail.attributes_template ?? {}).length > 0 && (
              <DetailSection title={t('platformCatalog.attributesTemplate', 'Variantlar')}>
                <div className="space-y-1">
                  {Object.entries(detail.attributes_template).map(([k, vals]) => (
                    <div key={k} className="flex items-start justify-between gap-3 rounded-xl border border-line bg-cream px-3 py-2">
                      <span className="text-caption font-medium text-ink-soft">{k}</span>
                      <span className="text-right text-caption text-muted">{(vals as string[]).join(', ')}</span>
                    </div>
                  ))}
                </div>
              </DetailSection>
            )}

            <div className="flex gap-2 px-4 pb-2 pt-2">
              <Button
                variant="outline"
                size="md"
                className="flex-1"
                onClick={() => { setEditing(detail); setDetail(null); }}
              >
                <Pencil size={14} /> {t('common.edit', 'Tahrirlash')}
              </Button>
            </div>

            <DangerZone
              title={t('platformCatalog.deleteModel', "Modelni o'chirish")}
              description={t('platformCatalog.deleteModelHint', "Bu modelni o'chirish qaytarib bo'lmaydi.")}
            >
              <Button
                variant="danger"
                size="md"
                loading={del.isPending}
                onClick={() =>
                  confirmDelete(t, () => del.mutateAsync(detail.id)).then(() => setDetail(null))
                }
              >
                <Trash2 size={14} />
                {t('common.delete', "O'chirish")}
              </Button>
            </DangerZone>
          </>
        )}
      </DetailDrawer>

      <ModelModal open={creating || !!editing} editing={editing} cats={cats.data ?? []}
                  allBrands={brands.data ?? []} lng={lng}
                  onClose={() => { setCreating(false); setEditing(null); }} />
    </>
  );
}

function ModelModal({ open, onClose, editing, cats, allBrands, lng }: {
  open: boolean; onClose: () => void; editing: CatalogModelRow | null;
  cats: CatalogCategoryRow[]; allBrands: CatalogBrandRow[]; lng: 'uz' | 'ru';
}) {
  const { t } = useTranslation();
  const create = useCreateCatalogModel();
  const update = useUpdateCatalogModel();
  const colors = useCatalogColors();
  const busy = create.isPending || update.isPending;

  const [catId, setCatId] = useState('');
  const [brandId, setBrandId] = useState('');
  const [name, setName] = useState('');
  const [imageUrl, setImageUrl] = useState('');
  const [variants, setVariants] = useState<Record<string, string[]>>({});
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    if (!open) return;
    setErr(null);
    if (editing) {
      setCatId(editing.category_id ?? ''); setBrandId(editing.brand_id);
      setName(editing.name); setImageUrl(editing.image_url ?? '');
      api.get(`/catalog/models/${editing.id}`)
        .then((r) => setVariants((r.data as { attributes_template?: Record<string, string[]> }).attributes_template ?? {}))
        .catch(() => setVariants({}));
    } else {
      setCatId(''); setBrandId(''); setName(''); setImageUrl(''); setVariants({});
    }
  }, [open, editing]);

  const category = cats.find((c) => c.id === catId) ?? null;
  // Brands available for the chosen category.
  const brandOptions = allBrands.filter((b) => !catId || (b.category_ids ?? []).includes(catId));
  const variantAxes: AttrField[] = (category?.attribute_schema ?? []).filter((f) => f.variant);

  const toggleValue = (axis: string, value: string, max?: number | null) => {
    setVariants((prev) => {
      const cur = prev[axis] ?? [];
      if (cur.includes(value)) return { ...prev, [axis]: cur.filter((v) => v !== value) };
      if (max && cur.length >= max) return prev;
      return { ...prev, [axis]: [...cur, value] };
    });
  };

  const submit = async () => {
    setErr(null);
    if (!catId) { setErr(t('platformCatalog.pickCategory', 'Kategoriya tanlang')); return; }
    if (!brandId) { setErr(t('platformCatalog.pickBrand', 'Brend tanlang')); return; }
    const tpl: Record<string, string[]> = {};
    for (const [k, v] of Object.entries(variants)) if (v.length) tpl[k] = v;
    const slug = slugify(name) || (editing?.slug ?? '');
    try {
      const body = {
        brand_id: brandId, category_id: catId, slug, name: name.trim(),
        image_url: imageUrl.trim() || null, attributes_template: tpl,
      };
      if (editing) await update.mutateAsync({ id: editing.id, body });
      else await create.mutateAsync(body);
      onClose();
    } catch (ex) {
      setErr(apiErrorMessage(ex));
    }
  };

  return (
    <Wizard
      open={open}
      title={editing ? t('common.edit', 'Tahrirlash') : t('platformCatalog.newModel')}
      submitLabel={editing ? t('common.save', 'Saqlash') : t('common.create', 'Yaratish')}
      submitting={busy}
      error={err}
      onClose={onClose}
      onSubmit={submit}
      steps={[
        {
          key: 'identity',
          title: t('platformCatalog.stepIdentity', 'Kategoriya va brend'),
          valid: !!catId && !!brandId && !!name.trim(),
          render: () => (
            <div className="space-y-3">
              <Field label={t('platformCatalog.categories', 'Kategoriya')} required>
                <Select value={catId} onChange={(v) => { setCatId(v); setBrandId(''); setVariants({}); }}
                        placeholder={t('platformCatalog.pickCategory', 'Tanlang') ?? ''}
                        options={cats.map((c) => ({ value: c.id, label: lng === 'ru' ? c.name_ru : c.name_uz }))} />
              </Field>
              <Field label={t('platformCatalog.brands', 'Brend')} required>
                <Select value={brandId} onChange={setBrandId} disabled={!catId}
                        placeholder={t('platformCatalog.pickBrand', 'Tanlang') ?? ''}
                        options={brandOptions.map((b) => ({ value: b.id, label: b.name }))} />
              </Field>
              <Field label={t('platformCatalog.modelName')} required>
                <input className={inputCls} value={name} onChange={(e) => setName(e.target.value)} placeholder="iPhone 15 Pro" />
              </Field>
              <Field label={t('platformCatalog.modelImage', 'Mahsulot rasmi (ixtiyoriy)')}>
                <ImageUploadField value={imageUrl} onChange={setImageUrl} />
              </Field>
            </div>
          ),
        },
        {
          key: 'variants',
          title: t('platformCatalog.attributesTemplate', 'Variantlar'),
          render: () => (
            !catId ? (
              <p className="rounded-xl border border-dashed border-line bg-cream p-4 text-caption text-muted">
                {t('platformCatalog.pickCategoryFirst', 'Avval kategoriyani tanlang.')}
              </p>
            ) : variantAxes.length === 0 ? (
              <p className="rounded-xl border border-dashed border-line bg-cream p-4 text-caption text-muted">
                {t('platformCatalog.noVariantAxes', "Bu kategoriyada variant maydonlari yo'q.")}
              </p>
            ) : (
              <div className="grid gap-3">
                {variantAxes.map((axis) => (
                  <VariantAxis key={axis.key} axis={axis} lng={lng} colors={colors.data ?? []}
                               selected={variants[axis.key] ?? []}
                               onToggle={(v) => toggleValue(axis.key, v, axis.max)}
                               onSetValues={(vals) => setVariants((p) => ({ ...p, [axis.key]: vals }))} />
                ))}
              </div>
            )
          ),
        },
      ]}
    />
  );
}

function VariantAxis({ axis, lng, colors, selected, onToggle, onSetValues }: {
  axis: AttrField; lng: 'uz' | 'ru'; colors: CatalogColorRow[];
  selected: string[]; onToggle: (value: string) => void; onSetValues: (vals: string[]) => void;
}) {
  const { t } = useTranslation();
  const label = lng === 'ru' ? axis.label_ru : axis.label_uz;
  return (
    <div className="rounded-xl border border-line bg-cream p-3">
      <div className="mb-2 flex items-center justify-between text-caption font-semibold uppercase tracking-wider text-ink-soft">
        <span>{label}{axis.unit ? ` (${axis.unit})` : ''}</span>
        {axis.max ? <span className="text-micro normal-case text-muted">max {axis.max}</span> : null}
      </div>
      {axis.type === 'color' ? (
        // (#197) Searchable multi-select instead of a wall of ~150 color pills.
        <MultiSelect
          value={selected}
          onChange={(vals) => onSetValues(axis.max ? vals.slice(0, axis.max) : vals)}
          searchable
          placeholder={t('platformCatalog.pickColors', 'Ranglar — qidiring va tanlang') ?? ''}
          options={colors.map((c) => ({ value: c.slug, label: lng === 'ru' ? c.name_ru : c.name_uz, hint: c.slug }))}
        />
      ) : axis.type === 'enum' && (axis.values?.length ?? 0) > 0 ? (
        <div className="flex flex-wrap gap-1.5">
          {(axis.values ?? []).map((v) => {
            const sel = selected.includes(v.value);
            return (
              <button key={v.value} type="button" onClick={() => onToggle(v.value)}
                      className={cn('rounded-pill border px-3 py-1 text-caption transition',
                        sel ? 'border-ink bg-ink text-white' : 'border-line bg-surface hover:border-ink/30')}>
                {lng === 'ru' ? v.label_ru : v.label_uz}
              </button>
            );
          })}
        </div>
      ) : (
        <NumberChips values={selected} onChange={onSetValues} unit={axis.unit ?? ''}
                     placeholder={t('platformCatalog.addValue', 'Qiymat — Enter')} />
      )}
    </div>
  );
}
