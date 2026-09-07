/** Catalog "schema" types — a category defines which fields/variants exist and
 *  which per-unit identifiers (IMEI/VIN) the seller fills at sale time. */

export type FieldType = 'color' | 'enum' | 'number' | 'decimal' | 'text' | 'bool';

interface EnumValue {
  value: string;
  label_uz: string;
  label_ru: string;
}

/** A variant/attribute axis on a category (color, storage, …). */
export interface AttrField {
  key: string;
  label_uz: string;
  label_ru: string;
  type: FieldType;
  /** Variant = multi-value, chosen per model (e.g. the colors a model comes in).
   *  Non-variant = a single descriptive attribute. */
  variant: boolean;
  required: boolean;
  unit?: string | null;        // display suffix for number/decimal, e.g. "GB"
  max_len?: number | null;     // max digits (number) or chars (text)
  values?: EnumValue[];        // for type 'enum' (custom list); 'color' draws on catalog_colors
  /** Who supplies the value: the platform pre-defines it on the catalog model,
   *  or the tenant fills it in their own product. Defaults to "tenant". */
  filled_by?: 'platform' | 'tenant';
  /** Max number of values a tenant may select for this field (e.g. 10). */
  max?: number | null;
}

/** A per-unit unique identifier captured at sale time (IMEI, VIN, …).
 *  `validator` selects the shape check `catalogIdentifiers.ts` (the mirror of
 *  `app/core/catalog_identifiers.py`) applies on top of the generic
 *  required/max_len/numeric check every field gets — see that module for the
 *  full list (`imei` / `vin` / `mac` / `inventory` / `plate` / `serial`). */
export interface IdField {
  key: string;
  label_uz: string;
  label_ru: string;
  type: 'text' | 'number';
  required: boolean;
  min_len?: number | null;
  max_len?: number | null;
  validator?: string | null;
}

// ── Global colour palette ────────────────────────────────────────────────────
// Deliberately NOT declared here. Colours live in the `catalog_colors` table
// (editable by HQ, ~106 rows live) and are served by GET /catalog/colors —
// use `useCatalogColors()`.
//
// This file used to carry a 19-entry COLOR_POOL literal that the category
// builder rendered as "the global palette". It had drifted from the table in
// both content and value: it showed a seventh of the real colours and its own
// hexes for them (#1c1f29 for black against the catalogue's #000000), so the
// preview of which colours an axis offers was showing different colours than
// the axis actually offers. A copy of a catalogue cannot be kept honest, so
// there is no copy.

export const FIELD_TYPES: FieldType[] = ['color', 'enum', 'number', 'decimal', 'text', 'bool'];

// ── Field presets (one-click "global product behaviours") ─────────────────────
export const ATTR_PRESETS: Record<string, AttrField> = {
  color:   { key: 'color',   label_uz: 'Rang',    label_ru: 'Цвет',    type: 'color',  variant: true, required: false },
  // (#198) Capacity ladder as an enum so 1 TB / 2 TB are first-class, labelled
  // options — the old free "Memory (GB)" number gave no clear way to enter TB.
  storage: { key: 'storage', label_uz: 'Xotira', label_ru: 'Память', type: 'enum', variant: true, required: false, values: [
    { value: '64',   label_uz: '64 GB',  label_ru: '64 ГБ' },
    { value: '128',  label_uz: '128 GB', label_ru: '128 ГБ' },
    { value: '256',  label_uz: '256 GB', label_ru: '256 ГБ' },
    { value: '512',  label_uz: '512 GB', label_ru: '512 ГБ' },
    { value: '1024', label_uz: '1 TB',   label_ru: '1 ТБ' },
    { value: '2048', label_uz: '2 TB',   label_ru: '2 ТБ' },
  ] },
  ram:     { key: 'ram',     label_uz: 'RAM',     label_ru: 'ОЗУ',     type: 'number', variant: true, required: false, unit: 'GB', max_len: 3 },
  size:    { key: 'size',    label_uz: "O'lcham", label_ru: 'Размер',  type: 'enum',   variant: true, required: false, values: [] },
};

// Mirrors `backend/alembic/versions/r96_seed_identifier_validators.py`'s `imei`/
// `serial` helpers and `VIN_REQ` — same keys, bounds and `validator`, so a
// category a platform admin builds by hand through this preset button gets
// the identical enforcement a seeded category gets (MUDD-113 M1: before this,
// the preset carried no `validator` at all, so a hand-built category's IMEI
// field accepted any 20-char string).
export const ID_PRESETS: Record<string, IdField[]> = {
  imei: [
    { key: 'imei1', label_uz: 'IMEI 1', label_ru: 'IMEI 1', type: 'text', required: true,
      min_len: 15, max_len: 15, validator: 'imei' },
    { key: 'imei2', label_uz: 'IMEI 2', label_ru: 'IMEI 2', type: 'text', required: false,
      min_len: 15, max_len: 15, validator: 'imei' },
  ],
  vin: [
    { key: 'vin', label_uz: 'VIN', label_ru: 'VIN', type: 'text', required: true,
      min_len: 17, max_len: 17, validator: 'vin' },
  ],
  serial: [
    { key: 'serial', label_uz: 'Seriya raqami', label_ru: 'Серийный номер', type: 'text', required: true,
      min_len: 4, max_len: 40, validator: 'serial' },
  ],
};

export function slugify(s: string): string {
  return s.toLowerCase().trim().replace(/[^a-z0-9]+/g, '-').replace(/^-+|-+$/g, '');
}

// Keyword → emoji map for the photo fallback. Covers uz / ru / en spellings so
// a product with no photo still shows a meaningful icon (e.g. a phone → 📱).
const EMOJI_KEYWORDS: Array<[RegExp, string]> = [
  [/phone|telefon|smartfon|смартфон|мобил|mobil/i, '📱'],
  [/laptop|noutbuk|ноутбук/i, '💻'],
  [/tablet|planshet|планшет|ipad/i, '📲'],
  [/watch|soat|часы|часи/i, '⌚️'],
  [/tv|televizor|телевизор/i, '📺'],
  [/car|avto|mashina|авто|машина/i, '🚗'],
  [/fridge|muzlatgich|холодильник/i, '🧊'],
  [/wash|kir yuvish|стиральн/i, '🧺'],
  [/mebel|furniture|мебель|divan|диван/i, '🛋️'],
  [/camera|kamera|камера|fotoapparat/i, '📷'],
  [/headphone|quloqchin|наушник|airpods/i, '🎧'],
  [/console|playstation|xbox|game|o'yin|игр/i, '🎮'],
  [/cloth|kiyim|одежд|ko'ylak/i, '👕'],
  [/shoe|oyoq|обув|krossovka/i, '👟'],
];

/** Pick a fallback emoji from a product's category/brand text. */
export function categoryEmoji(category?: string | null, brand?: string | null): string {
  const hay = `${category ?? ''} ${brand ?? ''}`;
  for (const [re, em] of EMOJI_KEYWORDS) if (re.test(hay)) return em;
  return '📦';
}
