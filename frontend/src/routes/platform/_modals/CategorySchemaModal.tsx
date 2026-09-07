import { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import type { TFunction } from 'i18next';
import { Plus, Trash2, X } from 'lucide-react';
import { Field, inputCls } from '@/components/ui/Modal';
import { Switch } from '@/components/ui/Switch';
import { Select } from '@/components/ui/Select';
import { EmojiPicker } from '@/components/ui/EmojiPicker';
import { Wizard } from '@/components/ui/Wizard';
import { BusinessTypePicker } from '@/components/ui/BusinessTypePicker';
import { cn } from '@/lib/cn';
import { apiErrorMessage } from '@/lib/errors';
import { useCreateCategory, useUpdateCategory } from '@/lib/mutations';
import { useCatalogColors } from '@/lib/queries';
import {
  ATTR_PRESETS, FIELD_TYPES, ID_PRESETS, slugify,
  type AttrField, type FieldType, type IdField,
} from '@/lib/catalogSchema';

export interface CategoryFull {
  id: string;
  slug: string;
  name_uz: string;
  name_ru: string;
  icon_emoji: string | null;
  icon_url: string | null;
  attribute_schema: AttrField[];
  identifier_schema: IdField[];
  business_types?: string[];
}

/** The category builder = "design the schema": name, icon, the variant/attribute
 *  fields, and the per-unit identifiers. All buttons, no JSON. */
export function CategorySchemaModal({ open, onClose, editing }: {
  open: boolean; onClose: () => void; editing?: CategoryFull | null;
}) {
  const { t, i18n } = useTranslation();
  const lng = i18n.language?.startsWith('ru') ? 'ru' : 'uz';
  const create = useCreateCategory();
  const update = useUpdateCategory();
  const busy = create.isPending || update.isPending;

  const [nameUz, setNameUz] = useState('');
  const [nameRu, setNameRu] = useState('');
  const [slug, setSlug] = useState('');
  const [slugTouched, setSlugTouched] = useState(false);
  const [emoji, setEmoji] = useState<string>('');
  const [iconUrl, setIconUrl] = useState('');
  const [fields, setFields] = useState<AttrField[]>([]);
  const [ids, setIds] = useState<IdField[]>([]);
  const [bizTypes, setBizTypes] = useState<string[]>([]);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    if (!open) return;
    setErr(null);
    if (editing) {
      setNameUz(editing.name_uz); setNameRu(editing.name_ru); setSlug(editing.slug);
      setSlugTouched(true);
      setEmoji(editing.icon_emoji ?? ''); setIconUrl(editing.icon_url ?? '');
      setFields(editing.attribute_schema ?? []);
      setIds(editing.identifier_schema ?? []);
      setBizTypes(editing.business_types ?? []);
    } else {
      setNameUz(''); setNameRu(''); setSlug(''); setSlugTouched(false);
      setEmoji(''); setIconUrl(''); setFields([]); setIds([]); setBizTypes([]);
    }
  }, [open, editing]);

  // Auto-slug from the Uzbek name until the user edits the slug manually.
  useEffect(() => { if (!slugTouched) setSlug(slugify(nameUz)); }, [nameUz, slugTouched]);

  const setField = (i: number, patch: Partial<AttrField>) =>
    setFields((f) => f.map((x, idx) => (idx === i ? { ...x, ...patch } : x)));
  const removeField = (i: number) => setFields((f) => f.filter((_, idx) => idx !== i));
  const addPreset = (key: keyof typeof ATTR_PRESETS) => setFields((f) => [...f, { ...ATTR_PRESETS[key] }]);
  const addCustomField = () => setFields((f) => [...f, {
    key: '', label_uz: '', label_ru: '', type: 'text', variant: false, required: false,
  }]);

  const setId = (i: number, patch: Partial<IdField>) =>
    setIds((s) => s.map((x, idx) => (idx === i ? { ...x, ...patch } : x)));
  const removeId = (i: number) => setIds((s) => s.filter((_, idx) => idx !== i));
  const addIdPreset = (key: keyof typeof ID_PRESETS) =>
    setIds((s) => [...s, ...ID_PRESETS[key].map((x) => ({ ...x }))]);
  const addCustomId = () => setIds((s) => [...s, {
    key: '', label_uz: '', label_ru: '', type: 'text', required: false, max_len: 32,
  }]);

  const submit = async () => {
    setErr(null);
    // Derive stable keys from labels; ensure uniqueness.
    const usedKeys = new Set<string>();
    const keyFor = (raw: string, fallback: string) => {
      let k = slugify(raw).replace(/-/g, '_') || fallback;
      let n = 1; const base = k;
      while (usedKeys.has(k)) { k = `${base}_${n++}`; }
      usedKeys.add(k);
      return k;
    };
    const attribute_schema = fields
      .filter((f) => f.label_uz.trim())
      .map((f) => ({ ...f, key: f.key || keyFor(f.label_uz, 'field') }));
    const usedIds = new Set<string>();
    const idKeyFor = (raw: string, fallback: string) => {
      let k = slugify(raw).replace(/-/g, '_') || fallback;
      let n = 1; const base = k;
      while (usedIds.has(k)) { k = `${base}_${n++}`; }
      usedIds.add(k);
      return k;
    };
    const identifier_schema = ids
      .filter((s) => s.label_uz.trim())
      .map((s) => ({ ...s, key: s.key || idKeyFor(s.label_uz, 'id') }));

    const body = {
      slug: slug.trim(), name_uz: nameUz.trim(), name_ru: nameRu.trim(),
      icon_emoji: emoji || null, icon_url: iconUrl.trim() || null,
      attribute_schema, identifier_schema, business_types: bizTypes,
    };
    try {
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
      title={editing ? t('platformCatalog.editCategory', 'Kategoriyani tahrirlash') : t('platformCatalog.newCategory')}
      submitLabel={editing ? t('common.save', 'Saqlash') : t('common.create', 'Yaratish')}
      submitting={busy}
      error={err}
      onClose={onClose}
      onSubmit={submit}
      steps={[
        {
          key: 'basics',
          title: t('platformCatalog.stepBasics', 'Nomi va belgisi'),
          valid: !!nameUz.trim() && !!nameRu.trim(),
          render: () => (
            <div className="space-y-5">
              <div className="grid gap-3">
                <Field label={t('platformCatalog.nameUz')} required>
                  <input className={inputCls} value={nameUz} onChange={(e) => setNameUz(e.target.value)} placeholder="Telefon" />
                </Field>
                <Field label={t('platformCatalog.nameRu')} required>
                  <input className={inputCls} value={nameRu} onChange={(e) => setNameRu(e.target.value)} placeholder="Телефон" />
                </Field>
              </div>
              <div>
                <div className="mb-2 text-micro uppercase tracking-wider text-muted">{t('platformCatalog.icon', 'Belgi (emoji)')}</div>
                <EmojiPicker inline value={emoji || null} onChange={setEmoji} />
              </div>
            </div>
          ),
        },
        {
          key: 'segment',
          title: t('platformCatalog.bizTypes', 'Biznes turlari'),
          render: () => <BusinessTypePicker value={bizTypes} onChange={setBizTypes} lng={lng} />,
        },
        {
          key: 'fields',
          title: t('platformCatalog.fields', 'Maydonlar'),
          render: () => (
            <div className="space-y-5">
              {/* ── Fields / variants ─────────────────────────────────── */}
              <div className="rounded-card border border-line p-3">
                <div className="text-sm font-semibold">{t('platformCatalog.fields', 'Maydonlar va variantlar')}</div>
                <p className="mb-3 mt-0.5 text-caption text-muted">{t('platformCatalog.fieldsHint', 'Variant — model menyusida qiymat tanlanadigan o‘q (rang, xotira). Qiymatlarni model ichida belgilaysiz.')}</p>
                <div className="space-y-2.5">
                  {fields.map((f, i) => (
                    <AttrEditor key={i} field={f} lng={lng} onChange={(p) => setField(i, p)} onRemove={() => removeField(i)} t={t} />
                  ))}
                </div>
                <div className="mt-3 flex flex-wrap gap-1.5">
                  {(['color', 'storage', 'ram', 'size'] as const).map((p) => (
                    <button key={p} type="button" onClick={() => addPreset(p)}
                            className="inline-flex items-center gap-1 rounded-pill border border-dashed border-line px-3 py-1.5 text-caption text-ink-soft hover:border-accent hover:text-accent">
                      <Plus size={12} /> {lng === 'ru' ? ATTR_PRESETS[p].label_ru : ATTR_PRESETS[p].label_uz}
                    </button>
                  ))}
                  <button type="button" onClick={addCustomField}
                          className="inline-flex items-center gap-1 rounded-pill border border-dashed border-line px-3 py-1.5 text-caption text-ink-soft hover:border-accent hover:text-accent">
                    <Plus size={12} /> {t('platformCatalog.customField', 'Boshqa maydon')}
                  </button>
                </div>
              </div>

              {/* ── Identifiers ───────────────────────────────────────── */}
              <div className="rounded-card border border-line p-3">
                <div className="text-sm font-semibold">{t('platformCatalog.identifiers', 'Noyob identifikatorlar')}</div>
                <p className="mb-3 mt-0.5 text-caption text-muted">{t('platformCatalog.identifiersHint', 'Sotuvchi sotuv paytida to‘ldiradi — masalan IMEI, VIN. Har bir dona uchun noyob.')}</p>
                <div className="space-y-2.5">
                  {ids.map((s, i) => (
                    <IdEditor key={i} field={s} onChange={(p) => setId(i, p)} onRemove={() => removeId(i)} t={t} />
                  ))}
                </div>
                <div className="mt-3 flex flex-wrap gap-1.5">
                  {(['imei', 'vin', 'serial'] as const).map((p) => (
                    <button key={p} type="button" onClick={() => addIdPreset(p)}
                            className="inline-flex items-center gap-1 rounded-pill border border-dashed border-line px-3 py-1.5 text-caption text-ink-soft hover:border-accent hover:text-accent">
                      <Plus size={12} /> {p.toUpperCase()}
                    </button>
                  ))}
                  <button type="button" onClick={addCustomId}
                          className="inline-flex items-center gap-1 rounded-pill border border-dashed border-line px-3 py-1.5 text-caption text-ink-soft hover:border-accent hover:text-accent">
                    <Plus size={12} /> {t('platformCatalog.customField', 'Boshqa maydon')}
                  </button>
                </div>
              </div>
            </div>
          ),
        },
      ]}
    />
  );
}

type TFn = TFunction;

function AttrEditor({ field, lng, onChange, onRemove, t }: {
  field: AttrField; lng: 'uz' | 'ru'; onChange: (p: Partial<AttrField>) => void; onRemove: () => void; t: TFn;
}) {
  const isNum = field.type === 'number' || field.type === 'decimal';
  // The colour swatches below preview the GLOBAL palette a `color` axis draws
  // from — so they have to be that palette, read from `catalog_colors`, not a
  // copy of it. Cached for 5 minutes and only fetched when this modal is open.
  const colors = useCatalogColors();
  return (
    <div className="rounded-xl border border-line bg-cream p-2.5">
      <div className="flex items-center gap-2">
        <input className={cn(inputCls, 'h-9')} value={field.label_uz}
               onChange={(e) => onChange({ label_uz: e.target.value })} placeholder={t('platformCatalog.fieldNameUz', 'Maydon (UZ)')} />
        <input className={cn(inputCls, 'h-9')} value={field.label_ru}
               onChange={(e) => onChange({ label_ru: e.target.value })} placeholder={t('platformCatalog.fieldNameRu', 'Maydon (RU)')} />
        <button type="button" onClick={onRemove} title={t('common.delete', 'O‘chirish')}
                className="grid h-9 w-9 shrink-0 place-items-center rounded-lg border border-line text-danger hover:bg-[color:var(--c-danger-soft)]">
          <Trash2 size={14} />
        </button>
      </div>
      <div className="mt-2 flex flex-wrap items-center gap-2">
        <Select
          className="w-36"
          value={field.type}
          onChange={(v) => onChange({ type: v as FieldType })}
          options={FIELD_TYPES.map((ty) => ({ value: ty, label: t(`platformCatalog.type_${ty}`, ty) }))}
        />
        <label className="inline-flex items-center gap-1.5 text-caption font-medium">
          <Switch size="sm" checked={field.variant} onChange={(v) => onChange({ variant: v })} />
          {t('platformCatalog.variant', 'Variant')}
        </label>
        <label className="inline-flex items-center gap-1.5 text-caption font-medium">
          <Switch size="sm" checked={field.required} onChange={(v) => onChange({ required: v })} />
          {t('platformCatalog.required', 'Majburiy')}
        </label>
        <Select
          className="w-40"
          value={field.filled_by ?? 'tenant'}
          onChange={(v) => onChange({ filled_by: v as 'platform' | 'tenant' })}
          options={[
            { value: 'tenant', label: t('platformCatalog.byTenant', 'Tenant to‘ldiradi') },
            { value: 'platform', label: t('platformCatalog.byPlatform', 'Platforma belgilaydi') },
          ]}
        />
        <input className={cn(inputCls, 'h-9 w-24')} inputMode="numeric" value={field.max ?? ''}
               onChange={(e) => onChange({ max: e.target.value ? Number(e.target.value.replace(/\D/g, '')) : null })}
               placeholder={t('platformCatalog.maxSelect', 'Maks. son')} />
        {isNum && (
          <>
            <input className={cn(inputCls, 'h-9 w-24')} value={field.unit ?? ''}
                   onChange={(e) => onChange({ unit: e.target.value })} placeholder={t('platformCatalog.unit', 'Birlik')} />
            <input className={cn(inputCls, 'h-9 w-28')} inputMode="numeric" value={field.max_len ?? ''}
                   onChange={(e) => onChange({ max_len: e.target.value ? Number(e.target.value.replace(/\D/g, '')) : null })}
                   placeholder={t('platformCatalog.maxLen', 'Maks. uzunlik')} />
          </>
        )}
        {field.type === 'text' && (
          <input className={cn(inputCls, 'h-9 w-28')} inputMode="numeric" value={field.max_len ?? ''}
                 onChange={(e) => onChange({ max_len: e.target.value ? Number(e.target.value.replace(/\D/g, '')) : null })}
                 placeholder={t('platformCatalog.maxLen', 'Maks. uzunlik')} />
        )}
      </div>
      {field.type === 'color' && (
        <div className="mt-2 flex items-center gap-1.5">
          <span className="text-micro text-muted">{t('platformCatalog.colorPoolNote', 'Global rang palitrasidan:')}</span>
          {/* Nothing is invented while the palette is unknown. These swatches
              exist ONLY to show which colours the axis offers, so a placeholder
              row would be a lie about the one thing the control communicates —
              and that is precisely what the hardcoded list had become: it drew
              its own hexes (#1c1f29 for "black" against the catalogue's
              #000000) and claimed "+4" where the real palette holds ~106. */}
          {colors.isLoading ? (
            <span className="text-micro text-muted">{t('common.loading', 'Yuklanmoqda…')}</span>
          ) : colors.isError || (colors.data?.length ?? 0) === 0 ? (
            <span className="text-micro text-muted">
              {t('platformCatalog.colorPoolUnavailable', 'Palitra hozircha mavjud emas')}
            </span>
          ) : (
            <div className="flex flex-wrap items-center gap-1">
              {/* The endpoint already filters to active colours and orders them
                  by sort_order, so this shows the same first swatches an editor
                  will see in the palette itself. */}
              {colors.data!.slice(0, 10).map((c) => (
                <span key={c.slug} className="h-4 w-4 rounded-full border border-line"
                      style={{ background: c.hex ?? 'transparent' }}
                      title={lng === 'ru' ? c.name_ru : c.name_uz} />
              ))}
              {colors.data!.length > 10 && (
                <span className="text-micro text-muted">+{colors.data!.length - 10}</span>
              )}
            </div>
          )}
        </div>
      )}
      {field.type === 'enum' && (
        <EnumValues field={field} lng={lng} onChange={onChange} t={t} />
      )}
    </div>
  );
}

function EnumValues({ field, onChange, t }: {
  field: AttrField; lng: 'uz' | 'ru'; onChange: (p: Partial<AttrField>) => void; t: TFn;
}) {
  const [draft, setDraft] = useState('');
  const vals = field.values ?? [];
  const add = () => {
    const v = draft.trim();
    if (!v) return;
    if (!vals.some((x) => x.value === slugify(v))) {
      onChange({ values: [...vals, { value: slugify(v) || v, label_uz: v, label_ru: v }] });
    }
    setDraft('');
  };
  return (
    <div className="mt-2 flex flex-wrap items-center gap-1.5">
      {vals.map((v) => (
        <span key={v.value} className="inline-flex items-center gap-1 rounded-pill border border-line bg-surface px-2.5 py-1 text-caption">
          {v.label_uz}
          <button type="button" onClick={() => onChange({ values: vals.filter((x) => x.value !== v.value) })}
                  aria-label={t('platformCatalog.removeValue', { value: v.label_uz, defaultValue: 'Qiymatni olib tashlash: {{value}}' })}
                  className="text-muted hover:text-danger"><X size={11} /></button>
        </span>
      ))}
      <input className="min-w-[100px] flex-1 bg-transparent px-1 py-1 text-small outline-none" value={draft}
             onChange={(e) => setDraft(e.target.value)}
             onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ',') { e.preventDefault(); add(); } }}
             onBlur={add} placeholder={t('platformCatalog.addValue', 'Qiymat — Enter')} />
    </div>
  );
}

function IdEditor({ field, onChange, onRemove, t }: {
  field: IdField; onChange: (p: Partial<IdField>) => void; onRemove: () => void; t: TFn;
}) {
  return (
    <div className="rounded-xl border border-line bg-cream p-2.5">
      <div className="flex flex-wrap items-center gap-2">
        <input className={cn(inputCls, 'h-9 min-w-[120px] flex-1')} value={field.label_uz}
               onChange={(e) => {
                 // Mirror to RU only when RU hasn't been touched (same value as UZ).
                 const isMirrored = field.label_ru === field.label_uz || !field.label_ru;
                 onChange({ label_uz: e.target.value, ...(isMirrored ? { label_ru: e.target.value } : {}) });
               }}
               placeholder="IMEI 1 (UZ)" />
        <input className={cn(inputCls, 'h-9 min-w-[120px] flex-1')} value={field.label_ru}
               onChange={(e) => onChange({ label_ru: e.target.value })}
               placeholder="IMEI 1 (RU)" />
        <Select
          className="w-28 shrink-0"
          value={field.type}
          onChange={(v) => onChange({ type: v as 'text' | 'number' })}
          options={[
            { value: 'text', label: t('platformCatalog.type_text', 'Matn') },
            { value: 'number', label: t('platformCatalog.type_number', 'Raqam') },
          ]}
        />
        <label className="inline-flex shrink-0 items-center gap-1.5 text-caption font-medium">
          <Switch size="sm" checked={field.required} onChange={(v) => onChange({ required: v })} />
          {t('platformCatalog.required', 'Majburiy')}
        </label>
        <button type="button" onClick={onRemove} title={t('common.delete', 'O‘chirish')}
                className="grid h-9 w-9 shrink-0 place-items-center rounded-lg border border-line text-danger hover:bg-[color:var(--c-danger-soft)]">
          <Trash2 size={14} />
        </button>
      </div>
    </div>
  );
}
