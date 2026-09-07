import { useCallback, useEffect, useMemo, useState, lazy, Suspense } from 'react';
import { useSearchParams } from 'react-router';
import { useTranslation } from 'react-i18next';
import { Eye, Pencil, ShoppingBag, Trash2, Upload } from 'lucide-react';
import { useNavigate, useParams } from 'react-router';
import { PageHeader } from '@/components/ui/PageHeader';
import { FloatingCreateAction } from '@/components/ui/FloatingCreateAction';
import { Button } from '@/components/ui/Button';
import { DesktopActionBar } from '@/components/layout/TopBar';
import { Badge } from '@/components/ui/Badge';
import { RowCard } from '@/components/ui/RowCard';
import { DataTable, type Column } from '@/components/ui/DataTable';
import { RowActionMenu, type RowAction } from '@/components/ui/RowActionMenu';
import { SearchInput } from '@/components/ui/SearchInput';
import { SkeletonList } from '@/components/ui/Skeleton';
import { ErrorCard } from '@/components/ui/ErrorCard';
import { EmptyState } from '@/components/ui/EmptyState';
import { ProductThumb } from '@/components/ui/ProductThumb';
import {
  FilterBar, FilterButton, FilterDrawer, RadioGroup, CheckGroup, RangeGroup,
  matchesRange, rangeActive,
} from '@/components/ui/FilterDrawer';
import { DetailModal, DefList, DefRow } from '@/components/ui/DetailModal';
import { useTenantProducts, type ProductRow } from '@/lib/queries';
import { useDeleteProduct } from '@/lib/mutations';
import { money } from '@/lib/format';
import { useCurrency } from '@/lib/currency';
import { MobileHeader } from '@/components/mobile/primitives';
import { confirmDialog, toast } from '@/lib/feedback';
import { apiErrorMessage, isNotFound } from '@/lib/errors';
const ProductModal = lazy(() => import('./_modals/ProductModal').then(m => ({ default: m.ProductModal })));

type ActiveFilter = '' | 'active' | 'inactive' | 'low';
type SortKey = 'new' | 'priceDesc' | 'priceAsc' | 'stockAsc' | 'name';

export default function TenantProductsPage() {
  const { t, i18n } = useTranslation();
  const currency = useCurrency();
  const navigate = useNavigate();
  const locale = useParams<{ locale: string }>().locale ?? (i18n.language.startsWith('ru') ? 'ru' : 'uz');
  const [q, setQ] = useState('');
  const [debouncedQ, setDebouncedQ] = useState('');
  useEffect(() => { const id = setTimeout(() => setDebouncedQ(q.trim()), 250); return () => clearTimeout(id); }, [q]);

  const [searchParams, setSearchParams] = useSearchParams();
  const [active, setActiveState] = useState<ActiveFilter>(
    (searchParams.get('filter') as ActiveFilter | null) ?? ''
  );
  
  const setActive = (f: ActiveFilter) => {
    setActiveState(f);
    const next = new URLSearchParams(searchParams);
    if (f) next.set('filter', f);
    else next.delete('filter');
    setSearchParams(next);
  };
  const [brands, setBrands] = useState<string[]>([]);
  const [priceRange, setPriceRange] = useState({ min: '', max: '' });
  const [stockRange, setStockRange] = useState({ min: '', max: '' });
  const [sort, setSort] = useState<SortKey>('new');
  const [filterOpen, setFilterOpen] = useState(false);

  const query = useTenantProducts(debouncedQ ? { q: debouncedQ } : undefined);
  const serverRows = useMemo(() => query.data?.items ?? [], [query.data]);
  // Brand facet derived from the loaded rows (sorted, with per-brand counts).
  const brandOptions = useMemo(() => {
    const counts = new Map<string, number>();
    for (const p of serverRows) {
      const b = (p.brand ?? '').trim();
      if (b) counts.set(b, (counts.get(b) ?? 0) + 1);
    }
    return [...counts.entries()]
      .sort((a, b) => a[0].localeCompare(b[0]))
      .map(([b, n]) => ({ value: b, label: `${b} · ${n}` }));
  }, [serverRows]);
  const rows = useMemo(() => serverRows
    .filter((p) => {
      if (active === 'active') return p.is_active;
      if (active === 'inactive') return !p.is_active;
      if (active === 'low') return typeof p.min_stock_level === 'number' && typeof p.stock === 'number' && p.stock <= p.min_stock_level;
      return true;
    })
    .filter((p) => (brands.length ? brands.includes((p.brand ?? '').trim()) : true))
    .filter((p) => matchesRange(p.base_price, priceRange))
    .filter((p) => matchesRange(p.stock, stockRange))
    .slice()
    .sort((a, b) => {
      if (sort === 'priceDesc') return b.base_price - a.base_price;
      if (sort === 'priceAsc') return a.base_price - b.base_price;
      if (sort === 'stockAsc') return (a.stock ?? 0) - (b.stock ?? 0);
      if (sort === 'name') return a.name_uz.localeCompare(b.name_uz);
      return 0; // 'new' — keep server order
    }), [serverRows, active, brands, priceRange, stockRange, sort]);
  const activeFilters = (active ? 1 : 0) + (brands.length ? 1 : 0)
    + (rangeActive(priceRange) ? 1 : 0) + (rangeActive(stockRange) ? 1 : 0) + (sort !== 'new' ? 1 : 0);
  const resetAll = () => { setActive(''); setBrands([]); setPriceRange({ min: '', max: '' }); setStockRange({ min: '', max: '' }); setSort('new'); };

  const [open, setOpen] = useState(false);
  const [editing, setEditing] = useState<ProductRow | null>(null);
  const [detail, setDetail] = useState<ProductRow | null>(null);
  const del = useDeleteProduct();

  const openCreate = () => { setEditing(null); setOpen(true); };
  const openEdit = useCallback((p: ProductRow) => { setDetail(null); setEditing(p); setOpen(true); }, []);

  const onDelete = useCallback(async (p: ProductRow) => {
    if (!(await confirmDialog({ message: t('products.deleteConfirm', { name: p.name_uz }), danger: true, confirmLabel: t('common.delete') }))) return;
    try {
      await del.mutateAsync(p.id);
      setDetail(null);
      toast.success(t('products.deleted', { defaultValue: "Mahsulot o'chirildi" }));
    } catch (ex) {
      if (isNotFound(ex)) { setDetail(null); toast.info(t('products.alreadyDeleted', { defaultValue: "Mahsulot allaqachon o'chirilgan" })); return; }
      toast.error(apiErrorMessage(ex));
    }
  }, [t, del]);

  // (H17) stable across renders (pure function of its argument) so the `cols`
  // useMemo below doesn't rebuild every render.
  const isLow = useCallback((p: ProductRow) =>
    typeof p.min_stock_level === 'number' && typeof p.stock === 'number' && p.stock <= p.min_stock_level, []);

  // Shared row "⋯" actions, wired only to existing page-level handlers
  // (open detail, edit, delete). Delete names the product in the confirm.
  // (H17) stable across renders so the `cols` useMemo below doesn't rebuild
  // every render.
  const rowActions = useCallback((p: ProductRow): RowAction[] => [
    { key: 'view', label: t('common.viewDetails', 'Batafsil'), icon: Eye, onClick: () => setDetail(p) },
    { key: 'edit', label: t('common.edit', 'Tahrirlash'), icon: Pencil, onClick: () => openEdit(p) },
    { key: 'delete', label: t('common.delete', "O'chirish"), icon: Trash2, destructive: true, onClick: () => onDelete(p) },
  ], [t, openEdit, onDelete]);

  // Desktop (md+) table columns. Mobile keeps the RowCard list below. (H17)
  const cols: Column<ProductRow>[] = useMemo(() => [
    {
      key: 'name', label: t('products.colName', { defaultValue: 'Mahsulot' }), width: '1.6fr',
      sortValue: (p) => p.name_uz.toLowerCase(),
      render: (p) => (
        <span className="flex min-w-0 items-center gap-2.5">
          <ProductThumb photo={p.photo_urls?.[0]} category={p.category} brand={p.brand} size={32} />
          <span className="block min-w-0">
            <span className="block truncate font-medium">{p.name_uz}</span>
            {p.sku && <span className="block truncate font-mono text-micro text-muted">{p.sku}</span>}
          </span>
        </span>
      ),
    },
    {
      key: 'brand', label: t('products.brand', 'Brend'), width: '1fr',
      sortValue: (p) => (p.brand ?? '').toLowerCase(),
      render: (p) => <span className="block truncate text-small text-muted">{[p.brand, p.category].filter(Boolean).join(' · ') || '—'}</span>,
    },
    {
      key: 'price', label: t('products.basePrice', 'Narx'), width: '130px', align: 'right',
      sortValue: (p) => p.base_price,
      render: (p) => <span className="tabular-nums">{money(p.base_price)}</span>,
    },
    {
      key: 'stock', label: t('products.stock', 'Qoldiq'), width: '92px', align: 'right',
      sortValue: (p) => p.stock ?? 0,
      render: (p) => <span className={`tabular-nums${isLow(p) ? ' font-medium text-[color:var(--c-danger-strong)]' : ''}`}>{p.stock ?? 0}</span>,
    },
    {
      key: 'status', label: t('customers.colStatus', 'Holat'), width: '116px',
      sortValue: (p) => (isLow(p) ? 0 : p.is_active ? 2 : 1),
      render: (p) => isLow(p)
        ? <Badge tone="danger">{t('stock.low', 'kam')} · {p.stock}</Badge>
        : (p.is_active ? <Badge tone="success">{t('status.active')}</Badge> : <Badge tone="neutral">{t('status.inactive')}</Badge>),
    },
    {
      key: 'actions', label: '', width: '48px', align: 'right',
      render: (p) => <RowActionMenu actions={rowActions(p)} ariaLabel={t('common.actions', 'Amallar')} />,
    },
  ], [t, rowActions, isLow]);

  return (
    <>
      <div className="hidden md:block">
        <PageHeader
          title={t('nav.products')}
          subtitle={t('products.subtitle', { count: query.data?.total ?? 0 })}
          actions={
            <DesktopActionBar>
              <Button variant="outline" size="md" onClick={() => navigate(`/${locale}/tenant/import`)}>
                <Upload size={16} /> {t('import.cta', 'Import')}
              </Button>
            </DesktopActionBar>
          }
        />
      </div>

      <div className="md:hidden">
        <MobileHeader title={t('nav.products')} />
      </div>

      <FloatingCreateAction onClick={openCreate} label={t('products.create')} />

      <FilterBar>
        <SearchInput value={q} onChange={setQ} className="flex-1"
          placeholder={t('products.searchPh', { defaultValue: 'Nomi, brend yoki SKU…' })} />
        <FilterButton count={activeFilters} onClick={() => setFilterOpen(true)} />
      </FilterBar>

      <FilterDrawer open={filterOpen} onClose={() => setFilterOpen(false)} onClear={resetAll}>
        <RadioGroup<ActiveFilter>
          label={t('customers.colStatus', 'Holat')}
          value={active}
          onChange={setActive}
          options={[
            { value: '', label: t('common.all', 'Hammasi') },
            { value: 'active', label: t('status.active', 'Faol') },
            { value: 'inactive', label: t('status.inactive', 'Nofaol') },
            { value: 'low', label: t('stock.low', 'Kam qoldiq') },
          ]}
        />
        {brandOptions.length > 0 && (
          <CheckGroup
            label={t('products.brand', 'Brend')}
            value={brands}
            onChange={setBrands}
            options={brandOptions}
          />
        )}
        <RangeGroup
          label={t('products.filterPrice', 'Narx')}
          unit={currency}
          value={priceRange}
          onChange={setPriceRange}
          step={1000}
        />
        <RangeGroup
          label={t('products.stock', 'Omborda qoldiq')}
          value={stockRange}
          onChange={setStockRange}
        />
        <RadioGroup<SortKey>
          label={t('common.sort', 'Saralash')}
          value={sort}
          onChange={setSort}
          options={[
            { value: 'new', label: t('products.sortNew', 'Eng yangi') },
            { value: 'priceDesc', label: t('products.sortPriceDesc', 'Narx (katta)') },
            { value: 'priceAsc', label: t('products.sortPriceAsc', 'Narx (kichik)') },
            { value: 'stockAsc', label: t('products.sortStockAsc', 'Qoldiq (kam)') },
            { value: 'name', label: t('products.sortName', 'Nomi (A→Z)') },
          ]}
        />
      </FilterDrawer>

      {query.isLoading ? (
        <SkeletonList rows={6} />
      ) : query.isError ? (
        <ErrorCard error={query.error} onRetry={() => query.refetch()} />
      ) : rows.length === 0 ? (
        <EmptyState
          icon={<ShoppingBag size={20} />}
          title={debouncedQ || activeFilters ? t('common.noResults') : t('products.emptyTitle')}
          body={!debouncedQ && !activeFilters ? t('products.emptyBody') : undefined}
          action={activeFilters > 0
            ? <Button variant="outline" size="md" onClick={resetAll}>{t('common.clear', 'Tozalash')}</Button>
            : undefined}
        />
      ) : (
        <>
          {/* Desktop (md+): dense, sortable, paginated table — full-width scannable. */}
          <div className="hidden md:block">
            <DataTable<ProductRow>
              columns={cols}
              rows={rows}
              isError={query.isError}
              error={query.error}
              onRetry={() => query.refetch()}
              rowKey={(p) => p.id}
              onRowClick={(p) => setDetail(p)}
              pageSize={25}
            />
          </div>
          {/* Mobile (<md): rich cards. Row opens detail; the kebab stopPropagations.
              The wrapper is a div[role=button] (not a <button>) so the menu's own
              buttons nest legally — RowCard renders a plain div without onClick. */}
          <ul className="space-y-2 md:hidden">
            {rows.map((p) => (
              <li key={p.id}>
                <div
                  role="button"
                  tabIndex={0}
                  onClick={() => setDetail(p)}
                  onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); setDetail(p); } }}
                  className="cursor-pointer rounded-card focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ink/20"
                >
                  <RowCard
                    leading={<ProductThumb photo={p.photo_urls?.[0]} category={p.category} brand={p.brand} size={44} />}
                    title={p.name_uz}
                    subtitle={[p.brand, p.category].filter(Boolean).join(' · ') || (p.sku ? `SKU: ${p.sku}` : '—')}
                    leftBadge={isLow(p)
                      ? <Badge tone="danger">{t('stock.low', 'kam')} · {p.stock}</Badge>
                      : (p.is_active ? <Badge tone="success">{t('status.active')}</Badge> : <Badge tone="neutral">{t('status.inactive')}</Badge>)}
                    trailing={
                      <span className="flex items-center gap-1">
                        <span className="text-body font-semibold tabular-nums">{money(p.base_price)}</span>
                        <RowActionMenu actions={rowActions(p)} ariaLabel={t('common.actions', 'Amallar')} />
                      </span>
                    }
                  />
                </div>
              </li>
            ))}
          </ul>
        </>
      )}

      <DetailModal
        open={!!detail}
        onClose={() => setDetail(null)}
        title={detail?.name_uz ?? ''}
        subtitle={detail ? ([detail.brand, detail.category].filter(Boolean).join(' · ') || undefined) : undefined}
        onEdit={detail ? () => openEdit(detail) : undefined}
        onDelete={detail ? () => onDelete(detail) : undefined}
        deleting={del.isPending}
      >
        {detail && (
          <DefList>
            <DefRow label={t('products.nameUz', 'Nomi (UZ)')}>{detail.name_uz}</DefRow>
            <DefRow label={t('products.nameRu', 'Nomi (RU)')}>{detail.name_ru}</DefRow>
            <DefRow label={t('products.sku', 'SKU / IMEI')}>{detail.sku}</DefRow>
            <DefRow label={t('products.basePrice', 'Sotuv narxi')}>{money(detail.base_price)} {currency}</DefRow>
            <DefRow label={t('products.costPrice', 'Tannarx')}>{detail.cost_price ? money(detail.cost_price) : null}</DefRow>
            <DefRow label={t('products.stock', 'Omborda qoldiq')}>
              <span className={isLow(detail) ? 'text-danger' : ''}>{detail.stock ?? 0}</span>
            </DefRow>
            <DefRow label={t('products.minStockLevel', 'Minimal qoldiq')}>{detail.min_stock_level}</DefRow>
            <DefRow label={t('customers.colStatus', 'Holat')}>
              {detail.is_active ? t('status.active', 'Faol') : t('status.inactive', 'Nofaol')}
            </DefRow>
          </DefList>
        )}
      </DetailModal>

      {open && (
        <Suspense fallback={<div className="text-center p-8">...</div>}>
          <ProductModal open={true} onClose={() => setOpen(false)} existing={editing} />
        </Suspense>
      )}
    </>
  );
}

// Re-export needed by InstallmentsPage which imports it from this module.
