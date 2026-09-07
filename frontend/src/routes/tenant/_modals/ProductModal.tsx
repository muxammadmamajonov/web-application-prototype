import { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Layers } from 'lucide-react';
import { Modal, Field, inputCls } from '@/components/ui/Modal';
import { Switch } from '@/components/ui/Switch';
import { Select } from '@/components/ui/Select';
import { Button } from '@/components/ui/Button';
import { MoneyInput } from '@/components/ui/FormFields';
import {
  useCreateProduct, useUpdateProduct, type ProductWrite,
} from '@/lib/mutations';
import { useTenantSettings, type ProductRow, type CustomFieldDef } from '@/lib/queries';
import { ProductPickerModal, type PickedProduct } from './ProductPickerModal';
import { StockPanel } from './StockModal';
import { useCurrency } from '@/lib/currency';
import { apiErrorMessage } from '@/lib/errors';

// Keys the catalog picker writes into attributes — never treated as
// user-editable named fields, so they survive an edit untouched.
const RESERVED_ATTR_KEYS = new Set(['variant', 'variant_label']);

interface Props {
  open: boolean;
  onClose: () => void;
  existing?: ProductRow | null;
}

const EMPTY: ProductWrite = {
  name_uz: '',
  name_ru: '',
  sku: '',
  category: '',
  brand: '',
  catalog_model_id: null,
  base_price: 0,
  cost_price: undefined,
  stock: undefined,
  min_stock_level: undefined,
  photo_urls: [],
  is_active: true,
};

/** Add or edit a product. Uses ProductPickerModal to pull category/brand/model
 *  from the global catalog and pre-fills name + brand + category. */
export function ProductModal({ open, onClose, existing }: Props) {
  const { t } = useTranslation();
  const currency = useCurrency();
  const create = useCreateProduct();
  const update = useUpdateProduct();
  const settings = useTenantSettings();
  const productFields: CustomFieldDef[] = settings.data?.product_form?.fields ?? [];
  const [form, setForm] = useState<ProductWrite>(EMPTY);
  const [attrs, setAttrs] = useState<Record<string, string>>({});
  const [pickerOpen, setPickerOpen] = useState(false);
  const [variantLabel, setVariantLabel] = useState('');
  // (#537) Per-field validation errors (rendered under each Field) + a separate
  // server-error banner for backend failures.
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [serverErr, setServerErr] = useState<string | null>(null);

  useEffect(() => {
    if (!open) return;
    if (existing) {
      setForm({
        name_uz:          existing.name_uz,
        name_ru:          existing.name_ru ?? '',
        sku:              existing.sku ?? '',
        category:         existing.category ?? '',
        brand:            existing.brand ?? '',
        catalog_model_id: existing.catalog_model_id,
        base_price:       existing.base_price,
        cost_price:       existing.cost_price ?? undefined,
        stock:            existing.stock ?? undefined,
        min_stock_level:  existing.min_stock_level ?? undefined,
        photo_urls:       existing.photo_urls ?? [],
        is_active:        existing.is_active,
        // Carry the stored attributes forward so a routine edit doesn't wipe the
        // picker-set variant/variant_label (backend PATCH replaces this JSON
        // wholesale; named fields are re-layered on top in onSubmit).
        attributes:       (existing.attributes as Record<string, unknown> | undefined) ?? undefined,
      });
      const raw = (existing.attributes ?? {}) as Record<string, unknown>;
      const v = raw.variant_label;
      setVariantLabel(typeof v === 'string' ? v : '');
      // Hydrate named attribute values (everything except picker-reserved keys).
      const hydrated: Record<string, string> = {};
      for (const [k, val] of Object.entries(raw)) {
        if (RESERVED_ATTR_KEYS.has(k)) continue;
        if (val != null && typeof val !== 'object') hydrated[k] = String(val);
      }
      setAttrs(hydrated);
    } else {
      setForm(EMPTY);
      setVariantLabel('');
      setAttrs({});
    }
    setErrors({}); setServerErr(null);
  }, [open, existing]);

  const setF = <K extends keyof ProductWrite>(k: K, v: ProductWrite[K]) =>
    setForm((p) => ({ ...p, [k]: v }));

  const onPick = (p: PickedProduct) => {
    setForm((prev) => ({
      ...prev,
      catalog_model_id: p.catalog_model_id,
      brand:            p.brand_name,
      category:         p.category_slug,
      name_uz:          prev.name_uz || `${p.brand_name} ${p.model_name}`.trim(),
      name_ru:          prev.name_ru || `${p.brand_name} ${p.model_name}`.trim(),
      base_price:       prev.base_price || 0,
      attributes:       { ...(prev.attributes ?? {}), variant: p.attributes, variant_label: p.variant_label },
    }));
    setVariantLabel(p.variant_label);
  };

  const setAttr = (key: string, value: string) =>
    setAttrs((p) => ({ ...p, [key]: value }));

  const onSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setServerErr(null);
    // (#537) Collect ALL field errors into a map so each renders under its own
    // control (and the first is focused) instead of a single bottom banner.
    const v: Record<string, string> = {};
    // Products must be backed by a catalog model — select one (or add a missing
    // model) via the catalog picker rather than free-typing a product.
    if (!form.catalog_model_id) v.catalog_model = t('products.errPickCatalog', "Katalogdan mahsulot tanlang");
    if (!form.name_uz?.trim()) v.name_uz = t('products.errNameRequired');
    if (!form.base_price || form.base_price <= 0) v.base_price = t('products.errPriceRequired');

    // Validate named catalog fields against their per-field rules.
    for (const f of productFields) {
      const val = (attrs[f.key] ?? '').trim();
      if (f.required && !val) {
        v[f.key] = t('products.errFieldRequired', { label: f.label, defaultValue: `"${f.label}" majburiy` });
        continue;
      }
      if (val && f.type === 'imei') {
        if (!/^\d+$/.test(val)) {
          v[f.key] = t('products.errImeiDigits', { label: f.label, defaultValue: `"${f.label}" faqat raqamlardan iborat bo'lishi kerak` });
        } else {
          const max = f.max_length ?? 14;
          if (val.length > max) {
            v[f.key] = t('products.errImeiLen', { label: f.label, max, defaultValue: `"${f.label}" maksimal ${max} ta raqam` });
          }
        }
      }
    }

    if (Object.keys(v).length > 0) {
      setErrors(v);
      // Focus the first control Field marked aria-invalid (Field sets this when
      // its `error` prop is present), scoped to this form.
      setTimeout(() => {
        const el = document.getElementById('product-form')?.querySelector<HTMLElement>('[aria-invalid="true"]');
        el?.focus();
        el?.scrollIntoView({ block: 'center' });
      }, 0);
      return;
    }
    setErrors({});

    const body: ProductWrite = { ...form };
    // strip empty optional strings so backend Pydantic doesn't blow up
    (['name_ru', 'sku', 'category', 'brand'] as const).forEach((k) => {
      if (body[k] === '' || body[k] === null) delete body[k];
    });

    // Merge named attribute values onto any picker-supplied attributes
    // (variant / variant_label), dropping empty entries.
    const mergedAttrs: Record<string, unknown> = { ...(form.attributes ?? {}) };
    for (const f of productFields) {
      const val = (attrs[f.key] ?? '').trim();
      if (val) mergedAttrs[f.key] = val;
      else delete mergedAttrs[f.key];
    }
    if (Object.keys(mergedAttrs).length) body.attributes = mergedAttrs;

    try {
      if (existing) await update.mutateAsync({ id: existing.id, body });
      else await create.mutateAsync(body);
      onClose();
    } catch (ex) {
      setServerErr(apiErrorMessage(ex));
    }
  };

  const busy = create.isPending || update.isPending;

  return (
    <>
      <Modal
        open={open}
        onClose={onClose}
        title={existing ? t('products.edit') : t('products.create')}
        subtitle={existing?.name_uz ?? t('products.modalSub')}
        size="lg"
        footer={
          <>
            <Button variant="outline" size="md" onClick={onClose} loading={busy}>
              {t('common.cancel')}
            </Button>
            <Button variant="primary" size="md" type="submit" form="product-form" loading={busy}>
              {t('common.save')}
            </Button>
          </>
        }
      >
        <form id="product-form" onSubmit={onSubmit} className="grid gap-4 md:grid-cols-2">
          {/* Catalog picker — anchor of the experience */}
          <div className="md:col-span-2 flex items-center justify-between gap-3 rounded-xl border border-dashed border-line bg-cream p-4">
            <div className="min-w-0">
              <div className="text-micro uppercase tracking-wider text-muted">{t('products.fromCatalog')}</div>
              {form.catalog_model_id ? (
                <div className="text-sm">
                  <span className="font-semibold">{form.brand}</span> · {form.name_uz}
                  {variantLabel && <span className="text-ink-soft"> · {variantLabel}</span>}
                </div>
              ) : (
                <div className="text-sm text-ink-soft">{t('products.pickFromCatalogHint')}</div>
              )}
            </div>
            <Button variant="outline" size="sm" onClick={() => setPickerOpen(true)} type="button">
              <Layers size={14} /> {t('products.pickFromCatalog')}
            </Button>
          </div>
          {errors.catalog_model && (
            <p className="md:col-span-2 -mt-3 text-micro font-medium text-danger" role="alert">{errors.catalog_model}</p>
          )}

          <Field label={t('products.nameUz')} required error={errors.name_uz}>
            <input className={inputCls} value={form.name_uz} required
                   onChange={(e) => setF('name_uz', e.target.value)} />
          </Field>
          <Field label={t('products.nameRu')}>
            <input className={inputCls} value={form.name_ru ?? ''}
                   onChange={(e) => setF('name_ru', e.target.value)} />
          </Field>

          <Field label={t('products.sku')} hint="IMEI / Code">
            <input className={inputCls} value={form.sku ?? ''}
                   onChange={(e) => setF('sku', e.target.value)} />
          </Field>
          <Field label={t('products.stock')}>
            <input type="number" min={0} className={inputCls} value={form.stock ?? ''}
                   onChange={(e) => setF('stock', e.target.value ? Number(e.target.value) : undefined)} />
          </Field>

          <Field label={t('products.basePrice')} required hint={currency} error={errors.base_price}>
            <MoneyInput value={form.base_price || 0} onChange={(n) => setF('base_price', n)} />
          </Field>
          <Field label={t('products.costPrice')} hint={currency}>
            <MoneyInput value={form.cost_price ?? 0} onChange={(n) => setF('cost_price', n === 0 ? undefined : n)} />
          </Field>
          <Field label={t('products.minStockLevel', 'Minimal qoldiq')} hint={t('products.minStockHint', 'Ogohlantirish chegarasi')}>
            <input type="number" min={0} className={inputCls} value={form.min_stock_level ?? ''}
                   onChange={(e) => setF('min_stock_level', e.target.value ? Number(e.target.value) : undefined)} />
          </Field>

          {/* Tenant-defined named catalog fields (brand, model, storage,
              colour, IMEI 1/2…) with per-field rules. */}
          {productFields.length > 0 && (
            <div className="md:col-span-2 mt-1 border-t border-line pt-3">
              <div className="mb-2 text-micro uppercase tracking-wider text-muted">
                {t('products.attrsTitle', 'Xususiyatlar')}
              </div>
              <div className="grid gap-4 md:grid-cols-2">
                {productFields.map((f) => (
                  <Field key={f.key} label={f.label} required={f.required} error={errors[f.key]}
                         hint={f.type === 'imei' ? t('products.imeiHint', 'Faqat raqam, 14 tagacha') : undefined}>
                    {f.type === 'select' ? (
                      <Select
                        value={attrs[f.key] ?? ''}
                        onChange={(v) => setAttr(f.key, v)}
                        placeholder="—"
                        options={(f.options ?? []).map((o) => ({ value: o, label: o }))}
                      />
                    ) : f.type === 'imei' ? (
                      <input className={inputCls} inputMode="numeric" required={f.required}
                             maxLength={f.max_length ?? 14} value={attrs[f.key] ?? ''}
                             onChange={(e) => setAttr(f.key, e.target.value.replace(/\D/g, '').slice(0, f.max_length ?? 14))} />
                    ) : (
                      <input className={inputCls} required={f.required}
                             type={f.type === 'date' ? 'date' : f.type === 'number' ? 'number' : f.type === 'phone' ? 'tel' : 'text'}
                             value={attrs[f.key] ?? ''}
                             onChange={(e) => setAttr(f.key, e.target.value)} />
                    )}
                  </Field>
                ))}
              </div>
            </div>
          )}

          {/* Photos come from the catalog model — tenants aren't asked to upload. */}

          {/* Stock adjustment lives here (no separate Stock action on the list row).
              Only meaningful once the product exists, so it's edit-only. */}
          {existing && (
            <div className="md:col-span-2 mt-1 border-t border-line pt-3">
              <div className="mb-2 text-micro uppercase tracking-wider text-muted">
                {t('stock.title', 'Ombor harakati')}
              </div>
              <StockPanel product={existing} />
            </div>
          )}

          <label className="md:col-span-2 mt-1 flex items-center gap-2 text-sm">
            <Switch checked={!!form.is_active} onChange={(v) => setF('is_active', v)} />
            {t('products.isActive')}
          </label>

          {serverErr && (
            <div className="md:col-span-2 rounded-xl border border-danger/30 bg-[color:var(--c-danger-soft)] px-4 py-3 text-sm text-danger" role="alert">
              {serverErr}
            </div>
          )}
        </form>
      </Modal>

      <ProductPickerModal open={pickerOpen} onClose={() => setPickerOpen(false)} onPick={onPick} />
    </>
  );
}
