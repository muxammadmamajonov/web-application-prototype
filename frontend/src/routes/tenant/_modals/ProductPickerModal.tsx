import { useEffect, useMemo, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { ArrowLeft, ChevronRight, Loader2, Plus, Search } from 'lucide-react';
import { Modal, Field, inputCls } from '@/components/ui/Modal';
import { Button } from '@/components/ui/Button';
import { ProductThumb } from '@/components/ui/ProductThumb';
import { CategoryIcon } from '@/components/ui/CategoryIcon';
import { cn } from '@/lib/cn';
import { toast } from '@/lib/feedback';
import { apiErrorMessage } from '@/lib/errors';
import { focusFirstInvalid, validate, hasErrors } from '@/lib/forms';
import {
  useCatalogBrands, useCatalogCategories, useCatalogModels, useTenantSettings,
  type CatalogModelRow,
} from '@/lib/queries';
import { useProposeModel } from '@/lib/mutations';

interface Props {
  open:    boolean;
  onClose: () => void;
  /** Called with the chosen model + variant axes the user filled in. */
  onPick:  (picked: PickedProduct) => void;
}

export interface PickedProduct {
  catalog_model_id: string;
  model_name:       string;
  brand_name:       string;
  category_slug:    string;
  variant_label:    string;
  attributes:       Record<string, string>;
}

type Step = 'category' | 'brand' | 'model' | 'variant';

/**
 * 4-step catalog picker:
 *   Category (Telefon/Noutbuk/…) → Brand (Apple/Samsung/…) → Model (iPhone 15
 *   Pro Max) → Variant (Black, 256GB).
 *
 * Each step is searchable. "Back" returns to the previous step preserving
 * earlier choices. "Pick" emits the final selection.
 */
export function ProductPickerModal({ open, onClose, onPick }: Props) {
  const { t, i18n } = useTranslation();
  const lng = i18n.language?.startsWith('ru') ? 'ru' : 'uz';

  const [step, setStep]         = useState<Step>('category');
  const [catSlug, setCatSlug]   = useState<string | null>(null);
  const [catName, setCatName]   = useState<string>('');
  const [brandId, setBrandId]   = useState<string | null>(null);
  const [brandName, setBrandName] = useState<string>('');
  const [model, setModel]       = useState<CatalogModelRow | null>(null);
  const [variant, setVariant]   = useState<Record<string, string>>({});
  const [search, setSearch]     = useState('');
  const [showAll, setShowAll]   = useState(false);

  // Filter the catalog to the tenant's segment so a furniture shop doesn't
  // wade through phones. "general"/"other" or the toggle → show everything.
  const settings = useTenantSettings();
  // Defensive optional chain on `profile` too — a settings bundle that arrives
  // without `profile` must degrade to "no segment", not crash the host page.
  const bizType  = settings.data?.profile?.business_type ?? null;
  const segmented = !!bizType && bizType !== 'general' && bizType !== 'other';
  const cats     = useCatalogCategories(segmented && !showAll ? bizType : null);
  const brands   = useCatalogBrands(catSlug);
  // Brands can span categories now, so scope models to the chosen category.
  const selectedCat = cats.data?.find((c) => c.slug === catSlug) ?? null;
  const catId    = selectedCat?.id ?? null;
  const models   = useCatalogModels(brandId, catId);

  // ── "Add missing model" (tenant proposes a model not yet in the catalog) ──
  const propose = useProposeModel();
  const [adding, setAdding]   = useState(false);
  const [nName,  setNName]    = useState('');
  const [nYear,  setNYear]    = useState('');
  const [nAxes,  setNAxes]    = useState<Record<string, string>>({});
  // (§4) Inline validation errors for the "add missing model" sub-form.
  const [addErrors, setAddErrors] = useState<Record<string, string>>({});
  const variantAxes = (selectedCat?.attribute_schema ?? []).filter((f) => f.variant);

  const resetAddForm = () => { setAdding(false); setNName(''); setNYear(''); setNAxes({}); setAddErrors({}); };

  const submitNewModel = async () => {
    if (!brandId || !catId) return;
    // (§4) Validate the model name inline instead of silently no-op'ing on empty.
    const errs: Record<string, string> = {
      nName: validate.required(nName, "Model nomi kiritilishi shart"),
    };
    setAddErrors(errs);
    if (hasErrors(errs)) { focusFirstInvalid(); return; }
    // Build attributes_template: each variant axis → its comma-separated values.
    const tpl: Record<string, string[]> = {};
    for (const f of variantAxes) {
      const raw = (nAxes[f.key] ?? '').split(',').map((s) => s.trim()).filter(Boolean);
      if (raw.length) tpl[f.key] = raw;
    }
    try {
      const m = await propose.mutateAsync({
        brand_id: brandId, category_id: catId, name: nName.trim(),
        release_year: nYear ? Number(nYear) : null,
        attributes_template: tpl,
      }) as CatalogModelRow;
      await models.refetch();
      resetAddForm();
      pickModel(m);   // jump straight to the variant step with the new model
    } catch (ex) {
      toast.error(apiErrorMessage(ex));
    }
  };

  // Reset on close
  useEffect(() => {
    if (open) return;
    setStep('category'); setCatSlug(null); setCatName('');
    setBrandId(null); setBrandName('');
    setModel(null); setVariant({}); setSearch('');
    resetAddForm();
  }, [open]);

  // Reset search when step changes
  useEffect(() => { setSearch(''); }, [step]);

  const filteredCats = useMemo(() => {
    const items = cats.data ?? [];
    if (!search) return items;
    return items.filter((c) =>
      (lng === 'ru' ? c.name_ru : c.name_uz).toLowerCase().includes(search.toLowerCase())
      || c.slug.includes(search.toLowerCase()),
    );
  }, [cats.data, search, lng]);

  // "Show all" fetches every category unfiltered, so the tenant's own segment
  // must be re-derived client-side to split it from the rest — otherwise the
  // toggle just replaces one flat list with a bigger flat list, and nothing
  // tells the shop which categories are actually theirs.
  const inSegmentCats = useMemo(() => {
    if (!segmented || !bizType) return filteredCats;
    return filteredCats.filter((c) => !c.business_types.length || c.business_types.includes(bizType));
  }, [filteredCats, segmented, bizType]);
  const otherCats = useMemo(() => {
    if (!segmented || !showAll || !bizType) return [];
    return filteredCats.filter((c) => c.business_types.length > 0 && !c.business_types.includes(bizType));
  }, [filteredCats, segmented, showAll, bizType]);

  const filteredBrands = useMemo(() => {
    const items = brands.data ?? [];
    if (!search) return items;
    return items.filter((b) => b.name.toLowerCase().includes(search.toLowerCase()));
  }, [brands.data, search]);

  const filteredModels = useMemo(() => {
    const items = models.data ?? [];
    if (!search) return items;
    return items.filter((m) => m.name.toLowerCase().includes(search.toLowerCase()));
  }, [models.data, search]);

  const pickModel = (m: CatalogModelRow) => {
    setModel(m);
    // Pre-fill first option of each axis if the template has any.
    const tpl = m.attributes_template || {};
    const seeded: Record<string, string> = {};
    for (const [axis, options] of Object.entries(tpl)) {
      if (Array.isArray(options) && options.length > 0) seeded[axis] = options[0];
    }
    setVariant(seeded);
    setStep('variant');
  };

  const selectCategory = (c: { slug: string; name_ru: string; name_uz: string }) => {
    setCatSlug(c.slug);
    setCatName(lng === 'ru' ? c.name_ru : c.name_uz);
    setStep('brand');
  };

  const confirmPick = () => {
    if (!model) return;
    const label = Object.entries(variant).map(([, v]) => v).filter(Boolean).join(' · ');
    onPick({
      catalog_model_id: model.id,
      model_name:       model.name,
      brand_name:       brandName,
      category_slug:    catSlug ?? '',
      variant_label:    label,
      attributes:       variant,
    });
    onClose();
  };

  const stepIndex = { category: 1, brand: 2, model: 3, variant: 4 }[step];

  return (
    <Modal
      open={open}
      onClose={onClose}
      title={t('catalog.pickTitle')}
      subtitle={`${stepIndex} / 4 · ${stepLabel(step, t)}`}
      size="lg"
      footer={
        <div className="flex w-full items-center justify-between">
          {step !== 'category' ? (
            <Button variant="outline" size="md" onClick={() => goBack(step, setStep)}>
              <ArrowLeft size={14} /> {t('catalog.back')}
            </Button>
          ) : <span />}
          {step === 'variant' && (
            <Button variant="primary" size="md" onClick={confirmPick} disabled={!model}>
              {t('catalog.pickConfirm')}
            </Button>
          )}
        </div>
      }
    >
      {/* Breadcrumb */}
      <div className="mb-4 flex flex-wrap items-center gap-2 text-caption text-ink-soft">
        {catName && <span className="font-medium">{catName}</span>}
        {catName && brandName && <ChevronRight size={12} />}
        {brandName && <span className="font-medium">{brandName}</span>}
        {brandName && model && <ChevronRight size={12} />}
        {model && <span className="font-medium text-accent">{model.name}</span>}
      </div>

      {/* Search */}
      {step !== 'variant' && (
        <div className="relative mb-4">
          <Search size={14} className="absolute left-3 top-1/2 -translate-y-1/2 text-muted" />
          <input
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder={t('catalog.search')}
            className="h-10 w-full rounded-xl border border-line bg-cream pl-9 pr-3 text-sm focus:border-ink/30 focus:bg-white focus:outline-none"
          />
        </div>
      )}

      {step === 'category' && (
        <>
          {segmented && (
            <button type="button" onClick={() => setShowAll((v) => !v)}
                    className="mb-3 inline-flex items-center gap-1.5 rounded-pill border border-line px-3 py-1.5 text-caption font-medium text-ink-soft hover:bg-ink/5">
              {showAll
                ? t('catalog.showSegment', 'Faqat mening sohamni ko‘rsatish')
                : t('catalog.showAll', 'Barcha toifalarni ko‘rsatish')}
            </button>
          )}
          {cats.isLoading && <CenterLoader />}
          {!cats.isLoading && filteredCats.length === 0 && <PickerEmpty />}
          {!cats.isLoading && filteredCats.length > 0 && (
            showAll && segmented ? (
              <>
                {inSegmentCats.length > 0 && (
                  <>
                    <div className="mb-2 text-micro uppercase tracking-wider text-muted">
                      {t('catalog.mySegment', 'Mening soham')}
                    </div>
                    <CategoryGrid cats={inSegmentCats} lng={lng} t={t} onPick={selectCategory} />
                  </>
                )}
                {otherCats.length > 0 && (
                  <>
                    <div className="mb-2 mt-4 text-micro uppercase tracking-wider text-muted">
                      {t('catalog.otherSegments', 'Boshqa sohalar')}
                    </div>
                    <CategoryGrid cats={otherCats} lng={lng} t={t} onPick={selectCategory} />
                  </>
                )}
              </>
            ) : (
              <CategoryGrid cats={filteredCats} lng={lng} t={t} onPick={selectCategory} />
            )
          )}
        </>
      )}

      {step === 'brand' && (
        <>
          {brands.isLoading && <CenterLoader />}
          {!brands.isLoading && filteredBrands.length === 0 && <PickerEmpty />}
          <div className="grid grid-cols-2 gap-3 sm:grid-cols-3">
            {filteredBrands.map((b) => (
              <button
                key={b.id}
                type="button"
                onClick={() => { setBrandId(b.id); setBrandName(b.name); setStep('model'); }}
                className="flex flex-col items-start gap-2 rounded-xl border border-line bg-cream p-4 text-left transition hover:border-ink/30 hover:bg-white"
              >
                {b.logo_url
                  ? <img src={b.logo_url} alt="" loading="lazy" className="h-8 w-8 object-contain" />
                  : <span className="grid h-8 w-8 place-items-center rounded-lg bg-surface text-sm font-bold text-muted">{b.name.charAt(0)}</span>}
                <span className="text-micro uppercase tracking-wider text-muted">{b.model_count} {t('common.models')}</span>
                <span className="text-body font-semibold">{b.name}</span>
              </button>
            ))}
          </div>
        </>
      )}

      {step === 'model' && (
        <>
          {models.isLoading && <CenterLoader />}
          {!models.isLoading && filteredModels.length === 0 && <PickerEmpty />}
          {!models.isLoading && filteredModels.length > 0 && (
            <ul className="divide-y divide-line rounded-xl border border-line">
              {filteredModels.map((m) => (
                <li key={m.id}>
                  <button
                    type="button"
                    onClick={() => pickModel(m)}
                    className="flex w-full items-center gap-3 px-4 py-3 text-left transition hover:bg-cream"
                  >
                    <ProductThumb photo={m.image_url} category={catName} brand={brandName} size={40} rounded="lg" />
                    <div className="min-w-0 flex-1">
                      <div className="truncate text-sm font-semibold">{m.name}</div>
                    </div>
                    <ChevronRight size={16} className="shrink-0 text-muted" />
                  </button>
                </li>
              ))}
            </ul>
          )}

          {/* "Model not found? Add it" — propose a tenant-scoped model under the
              selected brand + category. No photo is requested from the tenant. */}
          {!models.isLoading && !adding && (
            <button
              type="button"
              onClick={() => { setAdding(true); setNName(search.trim()); }}
              className="mt-3 flex w-full items-center justify-center gap-2 rounded-xl border border-dashed border-line px-4 py-3 text-sm font-medium text-accent hover:bg-[color:var(--c-accent-soft)]"
            >
              <Plus size={15} /> {t('catalog.addModelCta', { brand: brandName, defaultValue: `"{{brand}}" modelini topmadingizmi? Qo'shing` })}
            </button>
          )}

          {adding && (
            <div className="mt-3 space-y-3 rounded-card border border-line bg-cream p-4">
              <div className="text-micro uppercase tracking-wider text-muted">
                {t('catalog.addModelTitle', { brand: brandName, defaultValue: `Yangi model · {{brand}}` })}
              </div>
              <Field label={t('catalog.modelName', 'Model nomi')} required error={addErrors.nName}>
                <input className={inputCls} value={nName} autoFocus
                       placeholder={t('catalog.modelNamePh', 'Masalan: iPhone 18 Pro Max')}
                       onChange={(e) => { setNName(e.target.value); setAddErrors((p) => (p.nName ? { ...p, nName: '' } : p)); }} />
              </Field>
              {variantAxes.map((f) => (
                <Field key={f.key}
                       label={lng === 'ru' ? f.label_ru : f.label_uz}
                       hint={t('catalog.axisHint', "Vergul bilan ajrating: 128, 256, 512")}>
                  <input className={inputCls}
                         value={nAxes[f.key] ?? ''}
                         onChange={(e) => setNAxes((a) => ({ ...a, [f.key]: e.target.value }))} />
                </Field>
              ))}
              <Field label={t('catalog.releaseYear', 'Chiqarilgan yili')}>
                <input className={inputCls} inputMode="numeric" value={nYear}
                       onChange={(e) => setNYear(e.target.value.replace(/\D/g, '').slice(0, 4))} />
              </Field>
              <div className="flex gap-2">
                <Button variant="primary" size="md" type="button"
                        loading={propose.isPending}
                        onClick={() => void submitNewModel()}>
                  {t('catalog.addModelSave', "Qo'shish va tanlash")}
                </Button>
                <Button variant="outline" size="md" type="button" onClick={resetAddForm}>
                  {t('common.cancel')}
                </Button>
              </div>
            </div>
          )}
        </>
      )}

      {step === 'variant' && model && (
        <div className="space-y-4">
          <div className="rounded-xl bg-cream p-4">
            <div className="text-micro uppercase tracking-wider text-muted">{brandName}</div>
            <div className="text-subsection font-semibold">{model.name}</div>
          </div>

          {Object.entries(model.attributes_template || {}).map(([axis, options]) => (
            <div key={axis}>
              <div className="mb-2 text-micro uppercase tracking-wider text-muted">{axisLabel(axis, t)}</div>
              <div className="flex flex-wrap gap-2">
                {(options as string[]).map((opt) => (
                  <button
                    key={opt}
                    type="button"
                    onClick={() => setVariant((p) => ({ ...p, [axis]: opt }))}
                    className={cn(
                      'h-9 rounded-pill border px-4 text-sm font-medium transition',
                      variant[axis] === opt
                        ? 'border-ink bg-ink text-white'
                        : 'border-line bg-cream text-ink-soft hover:text-ink',
                    )}
                  >
                    {opt}
                  </button>
                ))}
              </div>
            </div>
          ))}

          {Object.keys(model.attributes_template || {}).length === 0 && (
            <div className="rounded-xl border border-dashed border-line bg-cream p-4 text-sm text-muted">
              {t('catalog.noVariants')}
            </div>
          )}
        </div>
      )}
    </Modal>
  );
}

function CategoryGrid({ cats, lng, t, onPick }: {
  cats: import('@/lib/queries').CatalogCategoryRow[];
  lng: 'uz' | 'ru';
  t: (k: string) => string;
  onPick: (c: import('@/lib/queries').CatalogCategoryRow) => void;
}) {
  return (
    <div className="grid grid-cols-2 gap-3 sm:grid-cols-3">
      {cats.map((c) => (
        <button
          key={c.id}
          type="button"
          onClick={() => onPick(c)}
          className="flex flex-col items-start gap-2 rounded-xl border border-line bg-cream p-4 text-left transition hover:border-ink/30 hover:bg-white"
        >
          <CategoryIcon iconUrl={c.icon_url} size="sm" />
          <span className="text-micro uppercase tracking-wider text-muted">{c.brand_count} {t('common.brands')}</span>
          <span className="text-body font-semibold">{lng === 'ru' ? c.name_ru : c.name_uz}</span>
        </button>
      ))}
    </div>
  );
}

function goBack(step: Step, setStep: (s: Step) => void) {
  const order: Step[] = ['category', 'brand', 'model', 'variant'];
  const idx = order.indexOf(step);
  if (idx > 0) setStep(order[idx - 1]);
}

function stepLabel(step: Step, t: (k: string) => string) {
  return ({
    category: t('catalog.stepCategory'),
    brand:    t('catalog.stepBrand'),
    model:    t('catalog.stepModel'),
    variant:  t('catalog.stepVariant'),
  } as const)[step];
}

function axisLabel(axis: string, t: (k: string) => string) {
  const map: Record<string, string> = {
    color:        t('catalog.axisColor'),
    storage:      t('catalog.axisStorage'),
    ram:          t('catalog.axisRam'),
    size:         t('catalog.axisSize'),
    resolution:   t('catalog.axisResolution'),
    capacity:     t('catalog.axisCapacity'),
    transmission: t('catalog.axisTransmission'),
    engine:       t('catalog.axisEngine'),
    material:     t('catalog.axisMaterial'),
  };
  return map[axis] ?? axis;
}

function CenterLoader() {
  return (
    <div className="grid place-items-center py-16 text-sm text-muted">
      <Loader2 size={20} className="animate-spin" />
    </div>
  );
}

function PickerEmpty() {
  const { t } = useTranslation();
  return (
    <div className="grid place-items-center gap-1 py-12 text-center">
      <Search size={20} className="text-muted" />
      <p className="text-sm text-muted">{t('catalog.pickerEmpty', { defaultValue: 'Hech narsa topilmadi' })}</p>
    </div>
  );
}
