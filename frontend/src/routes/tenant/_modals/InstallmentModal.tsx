import { useEffect, useMemo, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { AlertTriangle, Banknote, Check, FileText, Layers, Loader2, Pencil, Plus, ScanLine, Send, Trash2, UserPlus, Users } from 'lucide-react';
import { Modal, Field, inputCls } from '@/components/ui/Modal';
import { EmptyState } from '@/components/ui/EmptyState';
import { SkeletonList } from '@/components/ui/Skeleton';
import { SearchInput } from '@/components/ui/SearchInput';
import { StepForm } from '@/components/ui/StepForm';
import { DatePicker } from '@/components/ui/DatePicker';
import { Button } from '@/components/ui/Button';
import {
  useTenantCustomers, useTenantGuarantors, useTenantSettings,
  checkImei, type ImeiCheckResult, type InstallmentDetail,
  useCatalogCategories,
} from '@/lib/queries';
import { computeSchedule, type CalcFormula } from '@/lib/calc';
import { Select } from '@/components/ui/Select';
import {
  useCreateInstallment, useCreateGuarantor, useCreateCustomer, useReplaceInstallment,
  useResendCustomerInvite,
  type InstallmentItemWrite, type GuarantorWrite,
} from '@/lib/mutations';
import { PhoneInput, MoneyInput, Textarea } from '@/components/ui/FormFields';
import { displayPhone, phoneOrEmpty } from '@/lib/phone';
import { money } from '@/lib/format';
import { useCurrency } from '@/lib/currency';
import { useSubmitGuard } from '@/hooks/useSubmitGuard';
import { apiErrorMessage, errorCode } from '@/lib/errors';
import { confirmDialog, toast } from '@/lib/feedback';
import { cn } from '@/lib/cn';
import { BarcodeScanner } from '@/components/ui/BarcodeScanner';
import { ProductPickerModal, type PickedProduct } from './ProductPickerModal';
import {
  MAX_IDENTIFIER_LENGTH, SELECTABLE_IDENTIFIER_TYPES, validateIdentifier,
  type SelectableIdentifierType,
} from '@/lib/identifiers';
import {
  normalizedCategoryIdentifiers, parseCategoryIdentifierError,
  validateCategoryIdentifiers,
} from '@/lib/categoryIdentifiers';
import type { IdField } from '@/lib/catalogSchema';
import { clearDraft, readDraft, useWizardDraft } from '@/hooks/useWizardDraft';
import { useSession } from '@/store/session';
import { openContractPdf } from '@/lib/contractPdf';

const CUSTOMER_LIST_ID = 'installment-customer-list';
const customerOptionId = (id: string) => `installment-customer-${id}`;

interface Props {
  open: boolean;
  onClose: () => void;
  editing?: InstallmentDetail | null;
  /**
   * Opens the freshly created contract with the record-payment form expanded,
   * so the shop can take the customer's first instalment at the counter.
   *
   * MUST NOT be used to record the down payment. A down payment is not a
   * payment against the schedule: `compute_schedule` subtracts it from the goods
   * total before deriving `principal` (`financed = goods_total − down_payment`),
   * and completion keys off `paid_amount >= principal`. Recording it again
   * through the payment path would double-count it and retire the debt early by
   * exactly the down payment. Optional — callers that cannot navigate omit it.
   */
  onRecordPayment?: (installmentId: string) => void;
}

// (F-12) Stable per-submit idempotency key. crypto.randomUUID in the browser
// (secure context); a timestamp+random fallback keeps the jsdom test env working.
const newIdemKey = (): string =>
  globalThis.crypto?.randomUUID?.() ?? `idem-${Date.now()}-${Math.random().toString(36).slice(2)}`;

interface DraftItem {
  key:                 string;          // local row id for React
  product_id?:         string | null;
  catalog_model_id?:   string | null;
  product_name:        string;
  /** (MUDD-83) A `catalog_categories.slug` — WHAT this line sells.
   *
   *  One step, not four: the shop picks a kind and types a name. The old
   *  business_type -> catalog -> brand -> model chain is deliberately not
   *  reachable from here; it never let a phone shop record a car, and it made
   *  the commonest line in the product four clicks long. */
  category?:           string;
  variant_label?:      string;
  serial_number?:      string;
  /** What `serial_number` is. Absent until the shop states it. */
  identifier_type?:    SelectableIdentifierType;
  /** (MUDD-85/98) Category-schema-declared fields (IMEI 1/2, VIN, …), keyed by
   *  `catalog_categories.identifier_schema[].key`. A distinct thing from the
   *  single free-text `serial_number`/`identifier_type` pair above — an item
   *  can carry both, either, or neither. Deliberately excluded from the
   *  persisted wizard draft, same as `serial_number`: a device identity does
   *  not belong on a shared shop device (see `useWizardDraft` below). */
  identifiers?:        Record<string, string>;
  qty:                 number;
  unit_price:          number;
}

/** A line is finished when it names a product at a real price, any single
 *  identifier it carries is valid for the kind it was declared as, and every
 *  field the SELECTED category's schema requires is filled in and valid. */
function itemComplete(it: DraftItem, schema: IdField[]): boolean {
  return Boolean(it.product_name.trim()) && it.unit_price > 0 && it.qty > 0
    && validateIdentifier(it.identifier_type, it.serial_number).error === null
    && Object.keys(validateCategoryIdentifiers(schema, it.identifiers ?? {})).length === 0;
}

/**
 * Session-scoped key for the in-progress new-contract draft.
 *
 * MUST carry the acting user AND the active tenant: a shared key would let a
 * draft outlive a logout or a tenant switch on the same shop device, where the
 * next person at the counter would restore it. `useWizardDraft` clears on
 * commit and on explicit cancel, and its TTL bounds anything left behind.
 */
const draftKeyFor = (userId: string | null, tenantId: string | null): string =>
  `muddato.draft.installment.v2.${userId ?? 'anon'}.${tenantId ?? 'none'}`;

const newRow = (): DraftItem => ({
  key: crypto.randomUUID(),
  product_name: '',
  qty: 1,
  unit_price: 0,
});

/**
 * Create-installment wizard.
 *
 * Anatomy:
 *   1. Customer picker        — typeahead over existing tenant customers
 *   2. Items list             — repeatable rows; each row picks from the
 *      global catalog (via ProductPickerModal, tenant's segment first) OR
 *      free-text
 *   3. Terms                  — months + down payment + (optional) interest
 *   4. Live calc footer       — total, principal, monthly
 *
 * On submit we POST the InstallmentWrite shape the backend expects; the
 * server generates the MDD-YYYY-NNNNNN contract number and the per-month
 * monthly_amount.
 */
export function InstallmentModal({ open, onClose, editing, onRecordPayment }: Props) {
  const { t, i18n } = useTranslation();
  const currency = useCurrency();
  const isEdit = !!editing;
  const [customerDebouncedQ, setCustomerDebouncedQ] = useState('');
  const customers  = useTenantCustomers(customerDebouncedQ ? { q: customerDebouncedQ } : undefined);
  const guarantors = useTenantGuarantors();
  const create     = useCreateInstallment();
  // (F-12) Synchronous one-shot guard: a contract write is irreversible, so a
  // fast double-tap on "Save" must not fire create.mutateAsync twice before the
  // async `busy` flag re-renders the button disabled.
  const { pending: submitting, run: runSubmit } = useSubmitGuard();
  // (F-12) One idempotency key per contract-creation intent: stable across a
  // double-tap within an open modal, regenerated on close (below) so the next
  // contract is distinct. Sent as the Idempotency-Key header; the server dedups.
  const idemKeyRef = useRef<string>(newIdemKey());
  const replace    = useReplaceInstallment();
  const createGuarantor = useCreateGuarantor();
  const createCustomer  = useCreateCustomer();

  const [step,        setStep]        = useState(0);
  const [customerId,  setCustomerId]  = useState('');
  const [newC,        setNewC]        = useState<{ full_name: string; phone: string } | null>(null);
  const [months,      setMonths]      = useState(6);
  const [down,        setDown]        = useState(0);
  const [interest,    setInterest]    = useState<string>('');     // empty → unset
  // Financing model — initialised from the tenant's defaults once settings load.
  const [calcFormula, setCalcFormula] = useState<CalcFormula>('none');
  const [markup,      setMarkup]      = useState<string>('');     // percent, empty → use default
  const [calcTouched, setCalcTouched] = useState(false);
  const [startDate,   setStartDate]   = useState<string>('');     // empty → today
  const [items,       setItems]       = useState<DraftItem[]>([newRow()]);
  // Exactly one line is expanded at a time. Showing a second empty form before
  // the first is finished asks the shop to hold two half-entered products in
  // their head while a customer waits.
  const [activeKey,   setActiveKey]   = useState<string | null>(null);
  const [scanFor,     setScanFor]     = useState<string | null>(null);
  // Which item row the catalog picker is currently filling in. Only the one
  // open editor can be targeted, mirroring `scanFor`'s per-row-key pattern.
  const [catalogPickFor, setCatalogPickFor] = useState<string | null>(null);
  const [notes,       setNotes]       = useState('');
  const [err,         setErr]         = useState<string | null>(null);
  // What the wizard just created. Held so the closing moment can hand off —
  // contract number plus the next actions — rather than a toast that vanishes.
  const [created,     setCreated]     = useState<{ id: string; contract_number: string | null } | null>(null);
  const [customerQ,   setCustomerQ]   = useState('');
  // Which result the keyboard is on. -1 = none, and Enter on -1 does
  // nothing: "the first result" is not what someone who typed a query meant.
  const [highlight,   setHighlight]   = useState(-1);

  // Tenant calc defaults (formula, markup %, max months, min down %).
  const settings = useTenantSettings();
  const calcCfg = settings.data?.calc;

  // Seed formula/markup from tenant defaults once, until the user edits them.
  // Skipped entirely when editing — an existing contract carries its own
  // financing model (rehydrated below); re-seeding would silently re-amortize it.
  useEffect(() => {
    if (!open || calcTouched || !calcCfg || editing) return;
    // Clamp to a retained formula: a tenant row still carrying a deferred
    // default (annuity/differentiated) must not drag a NEW contract onto it.
    const seeded = calcCfg.default_calc_formula;
    setCalcFormula(seeded === 'none' || seeded === 'markup' ? seeded : 'none');
    if (calcCfg.default_markup_pct) setMarkup(String(calcCfg.default_markup_pct));
  }, [open, calcCfg, calcTouched, editing]);

  // `none` and `markup` are the retained formulas; `annuity`/`differentiated`
  // are deferred and must not be selectable. A deferred one stays listed ONLY
  // while an existing contract is already on it, so opening that contract shows
  // its real terms — it still cannot be switched onto one.
  const FORMULA_OPTIONS = useMemo(() => {
    const opts = [
      { value: 'none',   label: t('installments.formula_none', 'Ustamasiz (0%)') },
      { value: 'markup', label: t('installments.formula_markup', 'Ustama (%)') },
    ];
    if (calcFormula === 'annuity') {
      opts.push({ value: 'annuity', label: t('installments.formula_annuity', 'Annuitet (oylik %)') });
    }
    if (calcFormula === 'differentiated') {
      opts.push({ value: 'differentiated', label: t('installments.formula_differentiated', 'Differensial (oylik %)') });
    }
    return opts;
  }, [t, calcFormula]);

  // Guarantors (kafil)
  const [guarantorIds, setGuarantorIds] = useState<string[]>([]);
  const [guarantorQ,    setGuarantorQ]   = useState('');
  const [newG,          setNewG]         = useState<GuarantorWrite | null>(null);

  // IMEI/serial duplicate warnings, keyed by row key.
  const [imeiWarn,    setImeiWarn]    = useState<Record<string, ImeiCheckResult>>({});
  const [imeiModal,   setImeiModal]   = useState<ImeiCheckResult | null>(null);

  // (MUDD-98) A category-identifier value already recorded for this tenant is
  // knowable only from the server's response (see `categoryIdentifiers.ts`) —
  // held here so the offending field, not just a generic banner, shows it.
  const [catIdErr, setCatIdErr] = useState<{ itemKey: string; fieldKey: string; code: string } | null>(null);

  useEffect(() => {
    if (open) return;
    // reset on close
    setStep(0);
    setCustomerId(''); setMonths(6); setDown(0); setInterest(''); setStartDate('');
    setCalcFormula('none'); setMarkup(''); setCalcTouched(false);
    setItems([newRow()]); setNotes(''); setErr(null); setCreated(null); setCustomerQ('');
    setCustomerDebouncedQ(''); setNewC(null);
    setGuarantorIds([]); setGuarantorQ(''); setNewG(null);
    setImeiWarn({}); setImeiModal(null); setCatIdErr(null);
    idemKeyRef.current = newIdemKey();   // (F-12) fresh key for the next contract
  }, [open]);

  // Prefill when editing an existing (not-yet-paid) installment.
  useEffect(() => {
    if (!open || !editing) return;
    setStep(0);
    setCustomerId(editing.customer_id);
    setMonths(editing.months);
    setDown(editing.down_payment);
    // Rehydrate the contract's real financing model (interest_rate is stored as
    // a fraction; the UI field is a monthly %, so ×100). calcTouched=true keeps
    // the tenant-default seeding effect from clobbering these.
    setCalcFormula(editing.calc_formula ?? 'none');
    setMarkup(editing.markup_pct != null ? String(editing.markup_pct) : '');
    setInterest(editing.interest_rate != null ? String(Number((editing.interest_rate * 100).toFixed(4))) : '');
    setCalcTouched(true);
    setStartDate(editing.start_date ?? '');
    setItems(editing.items.length
      ? editing.items.map((it) => ({
          key: crypto.randomUUID(),
          product_id: it.product_id ?? null,
          catalog_model_id: it.catalog_model_id ?? null,
          product_name: it.product_name_snapshot,
          category: it.category ?? undefined,
          variant_label: it.variant_label ?? undefined,
          serial_number: it.serial_number ?? undefined,
          identifiers: it.identifiers ?? {},
          qty: it.qty, unit_price: it.unit_price,
        }))
      : [newRow()]);
    setGuarantorIds(editing.guarantors.map((g) => g.id));
    setNotes('');
  }, [open, editing]);

  const visibleGuarantors = useMemo(() => {
    const all = guarantors.data?.items ?? [];
    if (!guarantorQ) return all.slice(0, 50);
    const q = guarantorQ.toLowerCase();
    return all.filter((g) =>
      g.full_name.toLowerCase().includes(q) || g.phone.includes(q),
    ).slice(0, 50);
  }, [guarantors.data, guarantorQ]);

  const toggleGuarantor = (id: string) =>
    setGuarantorIds((arr) => arr.includes(id) ? arr.filter((x) => x !== id) : [...arr, id]);

  const runImeiCheck = async (key: string, serial: string) => {
    const s = serial.trim();
    if (!s) { setImeiWarn((m) => { const n = { ...m }; delete n[key]; return n; }); return; }
    try {
      const res = await checkImei(s);
      if (res.in_tenant || res.cross_tenant_count > 0) {
        setImeiWarn((m) => ({ ...m, [key]: res }));
        setImeiModal(res);
      } else {
        setImeiWarn((m) => { const n = { ...m }; delete n[key]; return n; });
      }
    } catch { /* network — fail open, no warning */ }
  };

  /** A scanned code is normalised under the kind the line already declares, so
   *  a label printed `35-209900-176148-1` lands as the value the server stores
   *  and the duplicate check compares. */
  const onScanned = (key: string, raw: string) => {
    const it = items.find((i) => i.key === key);
    const kind = it?.identifier_type ?? 'SERIAL_OR_CONTRACT';
    const { value } = validateIdentifier(kind, raw);
    const next = value ?? raw.trim();
    patchItem(key, { serial_number: next, identifier_type: kind });
    void runImeiCheck(key, next);
  };

  const addNewGuarantor = async () => {
    if (!newG || !newG.full_name.trim() || !newG.phone.trim()) return;
    try {
      const created = await createGuarantor.mutateAsync(newG);
      setGuarantorIds((arr) => arr.includes(created.id) ? arr : [...arr, created.id]);
      setNewG(null);
    } catch (ex) {
      setErr(apiErrorMessage(ex));   // localized; never raw backend text
    }
  };

  const addNewCustomer = async () => {
    if (!newC || !newC.full_name.trim() || !newC.phone.trim()) return;
    try {
      const created = await createCustomer.mutateAsync({
        full_name: newC.full_name.trim(),
        phone: phoneOrEmpty(newC.phone),
      });
      const id = (created as { id: string }).id;
      // A walk-in the shop just created IS a concrete tenant-scoped selection —
      // it goes through the same setter as picking a row, so the "choose a
      // customer" error clears and Next unlocks for the same reason.
      selectCustomer(id);
      setNewC(null);
      setCustomerQ(''); setHighlight(-1);
    } catch (ex) {
      setErr(apiErrorMessage(ex));   // localized; never raw backend text
    }
  };

  // The typeahead debounce lives in `SearchInput` (250 ms), which feeds
  // `customerDebouncedQ` — the server-side `q` that lets the picker work past
  // the old 50-row client cap.
  const visibleCustomers = customers.data?.items ?? [];

  const goodsTotal = items.reduce((s, it) => s + it.qty * it.unit_price, 0);
  const interestRate = interest === '' ? 0 : Number(interest) / 100;
  const markupPct = markup === '' ? 0 : Number(markup);
  // Live preview that matches what the backend will charge (markup/interest applied).
  const calc = computeSchedule({
    goodsTotal, downPayment: down, months,
    formula: calcFormula, markupPct, interestRate,
  });
  const total     = calc.total;
  const principal = calc.principal;
  const monthly   = calc.monthly;
  const maxMonths = calcCfg?.default_max_months ?? 60;
  const minDownPct = calcCfg?.default_min_down_pct ?? 0;
  const minDown = Math.round((goodsTotal * minDownPct) / 100);
  const downTooLow = minDownPct > 0 && down < minDown;
  // (#1) The down payment cannot reach the goods total: `financed` would be zero
  // or negative and there is nothing left to put on instalments. Mirrors the
  // backend's calc.py `down_payment_too_high`, which uses the same `financed <= 0`
  // boundary — so equal-to-total is refused on both sides, not just greater-than.

  // Derived live rather than checked only on submit: the owner is typing this
  // number in front of a customer, and learning at the end of a four-step wizard
  // that the first figure was wrong means re-walking the whole thing.

  // `goodsTotal > 0` guards the empty wizard — without it a fresh form starts at
  // 0 >= 0 and greets the owner with an error before they have typed anything.
  const downTooHigh = goodsTotal > 0 && down >= goodsTotal;

  // ── Unsaved-changes guard ──────────────────────────────────────────────────
  // Signature of the editable draft, compared against a baseline captured just
  // AFTER open (i.e. after the reset/prefill effects apply), so closing a dirty
  // wizard via Esc / backdrop / Cancel prompts before discarding entered work.
  const draftSig = JSON.stringify({
    customerId, months, down, interest, calcFormula, markup, startDate, notes, guarantorIds,
    items: items.map((i) => [i.product_name, i.qty, i.unit_price, i.serial_number ?? '', i.catalog_model_id ?? '', i.variant_label ?? '', i.category ?? '', JSON.stringify(i.identifiers ?? {})]),
  });
  const baselineSig = useRef('');
  const [armBaseline, setArmBaseline] = useState(0);
  useEffect(() => { if (open) setArmBaseline((n) => n + 1); }, [open]);
  // Captures the baseline on the render AFTER reset/prefill have applied.
  // eslint-disable-next-line react-hooks/exhaustive-deps
  useEffect(() => { if (open && armBaseline) baselineSig.current = draftSig; }, [armBaseline]);
  const requestClose = async () => {
    if (open && baselineSig.current && draftSig !== baselineSig.current) {
      const ok = await confirmDialog({
        title: t('installments.discardTitle', { defaultValue: "Saqlanmagan o'zgarishlar" }),
        message: t('installments.discardMsg', { defaultValue: "Kiritilgan ma'lumotlar saqlanmaydi. Yopilsinmi?" }),
        confirmLabel: t('common.discardClose', { defaultValue: 'Yopish' }),
        cancelLabel: t('common.keepEditing', { defaultValue: 'Davom ettirish' }),
        danger: true,
      });
      if (!ok) return;            // declined → the draft stays for the retry
    }
    // Any close that actually happens is an explicit cancel, so the draft goes.
    // This MUST sit outside the dirty check — a restored draft matches its own
    // captured baseline, so a clear inside it would never run and the discarded
    // work would reappear on the next open.

    // Only the CREATE wizard owns a draft. Editing an existing contract shares
    // this component and this key but never writes one, so it must not delete
    // one either: otherwise opening any contract for editing throws away an
    // unrelated new-contract draft the user is midway through.
    if (!isEdit) clearDraft(draftKey);
    onClose();
  };

  // The one open editor. Defaults to the last line so a restored draft opens
  // where the shop stopped rather than on a line they had finished.
  const activeItem = items.find((it) => it.key === activeKey) ?? items[items.length - 1];
  const [itemErr, setItemErr] = useState<string | null>(null);

  // (MUDD-83) EVERY category, with no `business_type` argument.
  //
  // Passing the tenant's segment is what caused the reported bug: the API
  // filters by it, so an `electronics` shop was shown 15 of 114 categories and
  // could never file a sale under `avtomobillar`. A shop that sells phones and
  // fridges was simply stuck. The segment is not a fact about what a shop may
  // sell — it was only ever a default, and using it as a gate made the gate
  // the product.
  const categoriesQ = useCatalogCategories();
  const categoryOptions = useMemo(
    () => (categoriesQ.data ?? [])
      .filter((c) => c.is_active)
      .map((c) => ({
        value: c.slug,
        label: i18n.language.startsWith('ru') ? (c.name_ru || c.name_uz) : c.name_uz,
      })),
    [categoriesQ.data, i18n.language],
  );

  // (MUDD-85/98) The selected category's own identifier_schema — IMEI 1/2,
  // VIN, etc. A category with none (or none picked yet) renders nothing.
  const schemaForCategory = (slug?: string | null): IdField[] =>
    (categoriesQ.data ?? []).find((c) => c.slug === slug)?.identifier_schema ?? [];
  const fieldLabel = (f: IdField): string =>
    i18n.language.startsWith('ru') ? (f.label_ru || f.label_uz) : f.label_uz;

  const patchItem = (key: string, patch: Partial<DraftItem>) =>
    setItems((arr) => arr.map((it) => it.key === key ? { ...it, ...patch } : it));

  // The server derives the category from `catalog_model_id` when the row does
  // not carry one, so picking from the catalog is already enough. Setting it
  // here anyway means the SELECT below shows what will be stored instead of
  // sitting empty until the contract is saved — the shop should not have to
  // submit to find out what they picked.
  const onPickCatalogProduct = (p: PickedProduct) => {
    if (catalogPickFor) {
      patchItem(catalogPickFor, {
        catalog_model_id: p.catalog_model_id,
        product_name: `${p.brand_name} ${p.model_name}`.trim(),
        variant_label: p.variant_label || undefined,
        category: p.category_slug || undefined,
        // A new category means a new (possibly empty) schema — a value typed
        // for the OLD one (e.g. a VIN) must not silently ride along under a
        // key the new category's fields don't even show.
        identifiers: {},
      });
    }
    setCatalogPickFor(null);
  };

  /** Collapse the finished line and open a fresh one.
   *
   *  Guarded on completeness AND on a pending append, so a double-tap or a
   *  repeated Enter cannot leave two blank products behind. */
  const addingRef = useRef(false);
  const [focusItemKey, setFocusItemKey] = useState<string | null>(null);
  const addRow = () => {
    if (addingRef.current || !activeItem || !itemComplete(activeItem, schemaForCategory(activeItem.category))) return;
    addingRef.current = true;
    const row = newRow();
    setItems((arr) => [...arr, row]);
    setActiveKey(row.key);
    setItemErr(null);
    setFocusItemKey(row.key);
  };
  // Focus follows the editor, so the next product starts where the caret is.
  // Driven by an effect rather than a frame callback: the input does not exist
  // until React has committed the new row.
  useEffect(() => {
    if (!focusItemKey) return;
    document.getElementById(`item-name-${focusItemKey}`)?.focus();
    setFocusItemKey(null);
    addingRef.current = false;
  }, [focusItemKey]);
  const editRow = (key: string) => { setActiveKey(key); setItemErr(null); };
  useEffect(() => {
    if (activeKey && !items.some((it) => it.key === activeKey)) setActiveKey(null);
  }, [items, activeKey]);
  const removeRow = (key: string) => setItems((arr) => {
    if (arr.length <= 1) return arr;
    const next = arr.filter((it) => it.key !== key);
    if (key === activeKey) setActiveKey(next[next.length - 1]?.key ?? null);
    return next;
  });

  const onSubmit = async (e?: React.FormEvent) => {
    e?.preventDefault();
    setErr(null); setCatIdErr(null);
    if (!customerId) { setStep(0); setErr(t('installments.errCustomerRequired')); return; }
    // (MUDD-98) A category can require identifier fields (IMEI 1, VIN, …) that
    // have nothing to do with product_name/price/qty completeness. Checked
    // FIRST, ahead of the item-count gate below: an incomplete category field
    // already excludes its line from `validItems`, so with only one line on
    // the contract that gate would fire the generic "pick at least one
    // product" message instead of naming the actual missing field.
    for (const it of items) {
      const schema = schemaForCategory(it.category);
      if (schema.length === 0) continue;
      const errors = validateCategoryIdentifiers(schema, it.identifiers ?? {});
      const badKey = Object.keys(errors)[0];
      if (!badKey) continue;
      const field = schema.find((f) => f.key === badKey);
      const label = field ? fieldLabel(field) : badKey;
      setStep(1); setActiveKey(it.key);
      setItemErr(`${label}: ${t(`identifier.${errors[badKey]}`)}`);
      return;
    }
    const validItems = items.filter((it) => itemComplete(it, schemaForCategory(it.category)));
    if (validItems.length === 0) { setErr(t('installments.errItemRequired')); return; }
    // A half-typed identifier on the open line is a mistake, not an omission:
    // dropping it silently would issue a contract without the device's identity.
    const broken = items.find((it) => it.serial_number?.trim() && !itemComplete(it, schemaForCategory(it.category)));
    if (broken) {
      setStep(1); setActiveKey(broken.key);
      setItemErr(t(`identifier.${validateIdentifier(broken.identifier_type, broken.serial_number).error ?? 'identifier_type_unknown'}`));
      return;
    }
    // Down payment must leave something to finance (mirrors backend
    // calc.py:down_payment_too_high) — caught inline before the generic check
    // so the owner gets a specific message on the Terms step, not a raw 400.
    // Reuses the SAME derived flag the field renders, so the inline error and
    // the submit guard cannot disagree about what counts as too high.
    if (downTooHigh) {
      setStep(3);
      setErr(t('installments.errDownTooHigh', { defaultValue: "Boshlang'ich to'lov tovar narxidan kam bo'lishi kerak" }));
      return;
    }
    // Interest-bearing formulas need a rate, else they silently degrade to 0%.
    if ((calcFormula === 'annuity' || calcFormula === 'differentiated') && (interest === '' || Number(interest) <= 0)) {
      setStep(3);
      setErr(t('installments.errInterestRequired', { defaultValue: 'Bu hisoblash usuli uchun oylik foizni kiriting' }));
      return;
    }
    if (principal <= 0) { setErr(t('installments.errPrincipalTooLow')); return; }
    if (downTooLow) {
      setStep(3);
      setErr(t('installments.errDownTooLow', {
        amount: money(minDown), pct: minDownPct,
        defaultValue: `Boshlang'ich to'lov kamida ${money(minDown)} so'm (${minDownPct}%) bo'lishi kerak`,
      }));
      return;
    }

    const payload = {
      customer_id:  customerId,
      months,
      down_payment: down,
      interest_rate: interest === '' ? null : Number(interest) / 100,
      calc_formula:  calcFormula,
      markup_pct:    calcFormula === 'markup' ? (markup === '' ? null : Number(markup)) : null,
      start_date:   startDate || null,
      notes:        notes || null,
      guarantor_ids: guarantorIds,
      items: validItems.map<InstallmentItemWrite>((it) => ({
        product_id:            it.product_id ?? null,
        catalog_model_id:      it.catalog_model_id ?? null,
        product_name_snapshot: it.product_name.trim(),
        category:              it.category ?? null,
        variant_label:         it.variant_label ?? null,
        // Normalised here too, so what is sent equals what the server stores
        // and what the duplicate check compares.
        serial_number:         validateIdentifier(it.identifier_type, it.serial_number).value,
        identifier_type:       it.serial_number?.trim() ? (it.identifier_type ?? null) : null,
        // (MUDD-98) Only the selected category's OWN schema keys, normalised —
        // a value left over from a since-switched category never reaches the
        // wire as an `unknown_key` the server would then have to refuse.
        identifiers:           normalizedCategoryIdentifiers(schemaForCategory(it.category), it.identifiers ?? {}),
        qty:                   it.qty,
        unit_price:            it.unit_price,
      })),
    };

    await runSubmit(async () => {
    try {
      if (isEdit && editing) {
        await replace.mutateAsync({ id: editing.id, body: payload });
        toast.success(t('installments.toastUpdated', { defaultValue: 'Shartnoma yangilandi' }));
      } else {
        // A soft overage returns the contract with an `overage` note. Its
        // meaning is billed-as-extra at a per-unit price; that is built here
        // from the structured `overage_price` — never the server's
        // `extra_message`, which is untranslated prose.
        const res = await create.mutateAsync({ body: payload, idempotencyKey: idemKeyRef.current }) as {
          id: string; overage?: { overage_price?: number }; contract_number?: string | null;
        };
        if (res?.overage) {
          const price = res.overage.overage_price;
          toast.info(price
            ? t('installments.overageBilled', {
                price: `${money(price)} ${currency}`,
                defaultValue: 'Bu shartnoma tarif limitidan tashqari — oyiga qo‘shimcha {{price}}.',
              })
            : t('installments.overageBilledPlain', 'Bu shartnoma tarif limitidan tashqari hisoblanadi.'));
        }
        // (#516) Explicit success confirmation surfacing the generated contract no
        // (the normal path was previously silent before this irreversible write).
        toast.success(res?.contract_number
          ? t('installments.toastCreated', { no: res.contract_number, defaultValue: 'Shartnoma yaratildi · {{no}}' })
          : t('installments.toastCreatedPlain', { defaultValue: 'Shartnoma yaratildi' }));
        clearDraft(draftKey);   // committed — the draft has served its purpose
        setCreated({ id: res.id, contract_number: res.contract_number ?? null });
        return;
      }
      onClose();
    } catch (ex) {
      // (MUDD-98) A duplicate category-identifier value is a per-tenant check
      // against every OTHER contract on record — knowable only from this
      // response, never from client-side validation — so it is bound back to
      // the field that caused it here instead of falling through to the
      // generic error screen. Matched by which line actually sent that key,
      // since the 400 itself names only the key, not the line.
      const parsed = parseCategoryIdentifierError(errorCode(ex));
      const offender = parsed && validItems.find((it) =>
        parsed.key in normalizedCategoryIdentifiers(schemaForCategory(it.category), it.identifiers ?? {}));
      if (parsed && offender) {
        setStep(1); setActiveKey(offender.key);
        setCatIdErr({ itemKey: offender.key, fieldKey: parsed.key, code: parsed.code });
      } else {
        // Everything else, including a hard plan limit, goes through the
        // shared localized vocabulary — which builds the plan-limit sentence
        // from the structured `limit` the API sends, not from its prose.
        setErr(apiErrorMessage(ex));
      }
    }
    });
  };

  // A contract is a binding obligation, so the fifth step is a read-only
  // Summary: financed amount, down payment, monthly, months, total, formula,
  // restated immediately before the irreversible write.
  const STEP_COUNT = 5;
  const STEP_LABELS = [
    t('installments.customer'), t('installments.items'),
    t('installments.guarantors', 'Kafillar'), t('installments.terms'),
    t('installments.summary', 'Tasdiqlash'),
  ];
  const stepValid = step === 0 ? !!customerId
    : step === 1 ? items.some((it) => it.product_name.trim() && it.unit_price > 0 && it.qty > 0)
    : true;
  const inviteCustomer = useResendCustomerInvite();
  const [inviteLink, setInviteLink] = useState<string | null>(null);
  const sendInvite = async () => {
    if (!customerId) return;
    try {
      const inv = await inviteCustomer.mutateAsync(customerId);
      // Already connected: there is no link, and the operator should be told
      // that rather than left staring at a control that did nothing.
      if (inv.already_linked) {
        toast.success(t('customers.alreadyJoinedNote',
          { defaultValue: 'Bu mijoz allaqachon Telegram orqali ulangan — havola kerak emas.' }));
        return;
      }
      setInviteLink(inv.url ?? null);
    } catch (ex) { toast.error(apiErrorMessage(ex)); }
  };

  // Bounded, expiring, session-scoped draft recovery for the longest form in
  // the product. Two invariants hold it together:

  //  1. Hydrate before persisting. React runs effects in declaration order, so
  //     persistence is gated on `hydrated` — which only the restore effect sets
  //     — or the first write lands before the read and recovery never fires.
  //  2. Only commercial progress is persisted: step, free-text item lines,
  //     financial terms. Customer, guarantor and every identity field are
  //     excluded, so recovery is partial by design rather than storing PII on a
  //     shared shop device. See src/hooks/useWizardDraft.ts.
  const meId = useSession((st) => st.me?.id ?? null);
  const activeTenantId = useSession((st) => st.activeTenantId ?? null);
  const draftKey = useMemo(() => draftKeyFor(meId, activeTenantId), [meId, activeTenantId]);

  const [hydrated, setHydrated] = useState(false);

  // Restore FIRST — declared above the persist hook so its effect runs first.
  useEffect(() => {
    if (!open || isEdit) { setHydrated(false); return; }
    const d = readDraft<{
      step: number; months: number; down: number; interest: string;
      calcFormula: CalcFormula; markup: string; startDate: string;
      items: DraftItem[];
    }>(draftKey);
    const hasWork = d?.items?.some((i) => i.product_name?.trim() || i.unit_price > 0);
    if (d && hasWork) {
      setMonths(d.months); setDown(d.down); setInterest(d.interest);
      setCalcFormula(d.calcFormula); setMarkup(d.markup); setCalcTouched(true);
      setStartDate(d.startDate);
      const restored = d.items.length ? d.items : [newRow()];
      setItems(restored);
      // Open the first unfinished line — where the work actually stopped.
      setActiveKey((restored.find((i) => !itemComplete(i, schemaForCategory(i.category))) ?? restored[restored.length - 1]).key);
      // Never resume past the customer step: the customer is deliberately not in
      // the draft, so one must be chosen before anything can be created.
      setStep(0);
    } else if (d) {
      clearDraft(draftKey);   // an empty draft is noise
    }
    setHydrated(true);
    // `schemaForCategory` reads `categoriesQ.data` and is deliberately not a
    // dependency: this effect restores a draft once when the modal opens, and
    // must not re-run merely because the categories list finished loading —
    // it only picks which line to focus, and a stale schema here at worst
    // under-focuses by one line, never blocks the submit-time check.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, isEdit, draftKey]);

  // …then persist, and only once hydration has happened.
  useWizardDraft(draftKey, open && !isEdit && !created && hydrated, useMemo(() => ({
    step, months, down, interest, calcFormula, markup, startDate,
    // The draft keeps the same shape the editor now has, so recovery restores
    // finished lines as finished lines. Identifier VALUES stay out of it: they
    // are device identities on a shared shop device, and the approved policy
    // persists only commercial progress.
    items: items.map((i) => ({
      key: i.key, product_name: i.product_name, qty: i.qty, unit_price: i.unit_price,
      identifier_type: i.identifier_type, category: i.category,
    })),
  }), [step, months, down, interest, calcFormula, markup, startDate, items]));

  // One concrete, tenant-scoped id — chosen from the list, never
  // derived from the text. The server re-checks that it belongs to this tenant
  // and is not pending/blocked; this is the client half of the same rule.
  const selectCustomer = (id: string) => {
    setCustomerId(id);
    setErr(null);
  };

  /** Editing the query drops a selection the person can no longer see.
   *  Leaving it would let them search "Ali", select Ali, search "Vali", and
   *  create Vali's contract for Ali. */
  const onCustomerQuery = (v: string) => {
    setCustomerQ(v);
    setHighlight(-1);
    if (customerId) setCustomerId('');
  };

  /** Arrow moves the highlight; Enter picks THAT row and stops there.
   *  It never advances the wizard — StepForm owns that rule, and this handler
   *  consumes the key before it could reach anything else. */
  const onCustomerKeyDown = (e: React.KeyboardEvent<HTMLInputElement>) => {
    const native = e.nativeEvent as unknown as { isComposing?: boolean; keyCode?: number };
    if (native.isComposing || native.keyCode === 229) return;   // IME commit
    const rows = visibleCustomers;
    if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
      if (rows.length === 0) return;
      e.preventDefault();
      setHighlight((h) => {
        // From "nothing highlighted", Down means the first row and Up means the
        // last — the two ends a person reaches for. After that it simply wraps.
        if (h < 0) return e.key === 'ArrowDown' ? 0 : rows.length - 1;
        return ((e.key === 'ArrowDown' ? h + 1 : h - 1) + rows.length) % rows.length;
      });
      return;
    }
    if (e.key === 'Escape') { setHighlight(-1); return; }
    if (e.key !== 'Enter') return;
    // Always swallowed: with a highlight it selects, without one it does
    // nothing at all. Either way it is not a step change.
    e.preventDefault();
    const row = highlight >= 0 ? rows[highlight] : undefined;
    if (row) selectCustomer(row.id);
  };

  const selectedCustomerName = (customers.data?.items ?? []).find((c) => c.id === customerId)?.full_name ?? '';
  const validItemsPreview = items.filter((it) => it.product_name.trim() && it.unit_price > 0 && it.qty > 0);
  const itemsSummary = validItemsPreview.length === 0
    ? '—'
    : validItemsPreview.map((it) => `${it.product_name.trim()} × ${it.qty}`).join(', ');
  // Results changed under the highlight (a debounced query landed, a page
  // refetched): an index into the OLD list would select the wrong person.
  useEffect(() => { setHighlight(-1); }, [customerDebouncedQ]);

  const busy = create.isPending || replace.isPending || submitting;
  const goNext = () => { if (step < STEP_COUNT - 1) setStep((s) => s + 1); else void onSubmit(); };
  const goBack = () => { if (step === 0) void requestClose(); else setStep((s) => s - 1); };

  // After the write succeeds the wizard becomes a hand-off: contract number,
  // then the next actions. Inviting the customer to Telegram is an optional
  // follow-up here — joining is never a precondition of the sale.
  if (created) {
    return (
      <Modal
        open={open}
        onClose={onClose}
        title={t('installments.createdTitle', 'Shartnoma yaratildi')}
        subtitle={created.contract_number ?? undefined}
        size="md"
        footer={
          <Button variant="primary" size="md" onClick={onClose}>
            {t('common.done', 'Tayyor')}
          </Button>
        }
      >
        <div role="status" aria-live="polite" className="space-y-4">
          <p className="text-body text-ink-soft">
            {t('installments.createdBody', 'Endi nima qilamiz?')}
          </p>
          <div className="grid gap-2">
            <Button variant="outline" size="md" block onClick={() => void openContractPdf(created.id)}>
              <FileText size={16} /> {t('installments.contractPdf', 'Shartnoma PDF')}
            </Button>
            {onRecordPayment && (
              <Button variant="outline" size="md" block
                      onClick={() => { onClose(); onRecordPayment(created.id); }}>
                <Banknote size={16} /> {t('installments.recordFirstPayment', "To'lov qayd etish")}
              </Button>
            )}
            <Button variant="outline" size="md" block
                    disabled={!customerId}
                    loading={inviteCustomer.isPending}
                    onClick={() => void sendInvite()}>
              <Send size={16} />
              {t('installments.inviteCustomer', 'Mijozni Telegramga taklif qilish')}
            </Button>
          </div>
          {inviteLink && (
            <div className="rounded-control border border-line bg-cream p-3">
              <div className="text-micro uppercase tracking-wider text-muted">
                {t('installments.inviteLink', 'Taklif havolasi')}
              </div>
              <code className="mt-1 block break-all font-mono text-caption">{inviteLink}</code>
              <Button variant="outline" size="sm" className="mt-2"
                      onClick={() => void navigator.clipboard.writeText(inviteLink)}>
                {t('common.copy', 'Nusxa olish')}
              </Button>
            </div>
          )}
        </div>
      </Modal>
    );
  }

  return (
    <>
      <Modal
        open={open}
        onClose={requestClose}
        title={isEdit ? t('installments.edit', 'Shartnomani tahrirlash') : t('installments.create')}
        subtitle={`${step + 1}/${STEP_COUNT} · ${STEP_LABELS[step]}`}
        size="lg"
        footer={
          <div className="flex w-full items-center justify-between gap-3">
            <div className="text-left">
              <div className="text-micro uppercase tracking-wider text-muted">{t('installments.monthly')}</div>
              <div className="font-mono text-body font-semibold tabular-nums">{money(monthly)} {currency}</div>
            </div>
            <div className="flex gap-2">
              <Button variant="outline" size="md" onClick={goBack} loading={busy}>
                {step === 0 ? t('common.cancel') : t('common.back', 'Orqaga')}
              </Button>
              <Button variant="primary" size="md" onClick={goNext} disabled={!stepValid} loading={busy}>
                {step < STEP_COUNT - 1
                  ? t('common.next', 'Keyingisi')
                  : isEdit ? t('common.save') : t('installments.createContract', 'Shartnoma yaratish')}
              </Button>
            </div>
          </div>
        }
      >
        {/* progress dots — (#553) exposed as a progressbar so screen readers
            announce the step position/total; the bars themselves are decorative. */}
        <div className="mb-5 flex gap-1.5"
             role="progressbar"
             aria-valuemin={1} aria-valuemax={STEP_COUNT} aria-valuenow={step + 1}
             aria-label={t('installments.stepperLabel', 'Bosqichlar')}
             aria-valuetext={t('installments.stepStatus', {
               current: step + 1, total: STEP_COUNT, label: STEP_LABELS[step],
               defaultValue: 'Bosqich {{current}}/{{total}}: {{label}}',
             })}>
          {Array.from({ length: STEP_COUNT }).map((_, i) => (
            <span key={i} aria-hidden="true" className={cn('h-1.5 flex-1 rounded-pill transition', i <= step ? 'bg-ink' : 'bg-line')} />
          ))}
        </div>
        {/* Enter advances nothing. Moving on and the irreversible create are
            reachable only from the footer buttons, which sit outside this
            element; the shared policy lives in StepForm. */}
        <StepForm className="space-y-6">
          {/* CUSTOMER */}
          {step === 0 && (
          <section>
            <div className="mb-2 flex items-center justify-between gap-2">
              <div className="text-micro uppercase tracking-wider text-muted">
                {t('installments.customer')}
              </div>
              <button type="button"
                      className="inline-flex items-center gap-1 text-caption font-medium text-accent hover:underline"
                      onClick={() => setNewC(newC ? null : { full_name: '', phone: '' })}>
                <UserPlus size={14} /> {t('installments.addCustomer', 'Yangi mijoz')}
              </button>
            </div>

            {/* Inline new-customer (walk-in) — name + phone is enough to sell. */}
            {newC && (
              <div className="mb-3 grid gap-2 rounded-control border border-line bg-cream p-3 sm:grid-cols-2">
                <input className={inputCls}
                       aria-label={t('customers.fullName', 'F.I.Sh')}
                       placeholder={t('join.fullName', 'F.I.Sh')}
                       value={newC.full_name}
                       onChange={(e) => setNewC({ ...newC, full_name: e.target.value })} />
                <PhoneInput value={newC.phone} onChange={(v) => setNewC({ ...newC, phone: v })} />
                <div className="flex gap-2 sm:col-span-2">
                  <Button variant="primary" size="sm" type="button"
                          onClick={() => void addNewCustomer()}
                          disabled={createCustomer.isPending || !newC.full_name.trim() || !newC.phone.trim()}>
                    {createCustomer.isPending ? <Loader2 size={14} className="animate-spin" /> : <Plus size={14} />}
                    {t('installments.createAndSelect', 'Qo‘shish va tanlash')}
                  </Button>
                  <Button variant="outline" size="sm" type="button" onClick={() => setNewC(null)}>
                    {t('common.cancel')}
                  </Button>
                </div>
              </div>
            )}

            {/* A search box is not a selection: a contract binds a named
                person, so `customerId` must be one the shop pointed at. A
                WAI-ARIA combobox over the results — Arrow highlights, Enter
                picks the highlighted row and nothing else, editing the query
                drops a selection that is no longer visible. `data-enter-owner`
                tells StepForm this subtree handles its own Enter. */}
            <div data-enter-owner>
              <SearchInput
                value={customerQ}
                onChange={onCustomerQuery}
                onDebouncedChange={setCustomerDebouncedQ}
                minLength={1}
                placeholder={t('installments.customerSearchPh')}
                className="mb-2 max-w-none"
                onKeyDown={onCustomerKeyDown}
                combobox={{
                  listId: CUSTOMER_LIST_ID,
                  expanded: visibleCustomers.length > 0,
                  activeId: highlight >= 0 && visibleCustomers[highlight]
                    ? customerOptionId(visibleCustomers[highlight].id) : undefined,
                }}
              />
              <div className="max-h-[180px] overflow-y-auto rounded-control border border-line">
                {customers.isLoading && (
                  <div className="p-3"><SkeletonList rows={2} /></div>
                )}
                {!customers.isLoading && visibleCustomers.length === 0 && (
                  <EmptyState size="compact" icon={<Users size={18} />} title={t('installments.noCustomers')} />
                )}
                <ul id={CUSTOMER_LIST_ID} role="listbox"
                    aria-label={t('installments.customer')}
                    className="divide-y divide-line">
                  {/* Every customer of this tenant is selectable: a missing
                      Telegram link is not an eligibility condition. Linking is
                      offered as an optional action AFTER the contract exists. */}
                  {visibleCustomers.map((c, i) => (
                    <li
                      key={c.id}
                      id={customerOptionId(c.id)}
                      role="option"
                      aria-selected={customerId === c.id}
                      onMouseEnter={() => setHighlight(i)}
                      onClick={() => selectCustomer(c.id)}
                      className={cn(
                        'flex cursor-pointer items-center gap-3 px-3 py-2 text-body',
                        i === highlight && 'bg-cream',
                        customerId === c.id && 'font-medium',
                      )}
                    >
                      <span aria-hidden="true"
                            className={cn('grid h-4 w-4 shrink-0 place-items-center rounded-pill border',
                                          customerId === c.id ? 'border-ink' : 'border-line')}>
                        {customerId === c.id && <span className="h-2 w-2 rounded-pill bg-ink" />}
                      </span>
                      <span className="min-w-0 flex-1 truncate">
                        <span className="font-medium">{c.full_name}</span>
                        <span className="ml-2 text-caption text-ink-soft">{displayPhone(c.phone)}</span>
                      </span>
                    </li>
                  ))}
                </ul>
              </div>
              {/* Named, not implied by a disabled button with no reason. */}
              {!customerId && (
                <p className="mt-1.5 text-caption text-muted">
                  {t('installments.pickCustomerHint', 'Ro‘yxatdan mijozni tanlang — qidiruv matni tanlov emas.')}
                </p>
              )}
            </div>
          </section>
          )}

          {/* ITEMS */}
          {step === 1 && (
          <section>
            <div className="mb-2 text-micro uppercase tracking-wider text-muted">
              {t('installments.items')}
            </div>

            {/* Finished lines collapse to one row each: what was sold, how many,
                for how much, and the identifier — enough to check the sale at a
                glance without scrolling past four open forms. */}
            {items.filter((it) => it.key !== activeItem?.key && itemComplete(it, schemaForCategory(it.category))).length > 0 && (
              <ul className="mb-3 space-y-1.5">
                {items.map((it) => {
                  if (it.key === activeItem?.key || !itemComplete(it, schemaForCategory(it.category))) return null;
                  const filledIdFields = schemaForCategory(it.category)
                    .filter((f) => it.identifiers?.[f.key])
                    .map((f) => `${fieldLabel(f)} ${it.identifiers![f.key]}`);
                  return (
                    <li key={it.key} data-testid="item-summary"
                        className="flex items-center gap-2 rounded-control border border-line bg-surface px-2.5 py-2">
                      <Check size={14} className="shrink-0 text-success" aria-hidden="true" />
                      <span className="min-w-0 flex-1">
                        <span className="block truncate text-body font-medium">{it.product_name.trim()}</span>
                        <span className="block truncate text-caption tabular-nums text-muted">
                          {it.qty} × {money(it.unit_price)} {currency}
                          {it.serial_number ? ` · ${it.identifier_type === 'IMEI' ? 'IMEI' : t('installments.serialShort', 'Seriya')} ${it.serial_number}` : ''}
                          {filledIdFields.length > 0 ? ` · ${filledIdFields.join(' · ')}` : ''}
                        </span>
                      </span>
                      <button type="button" onClick={() => editRow(it.key)}
                              aria-label={t('installments.editItem', { name: it.product_name.trim(), defaultValue: 'Tahrirlash: {{name}}' })}
                              className="tap-target grid h-11 w-11 shrink-0 place-items-center rounded-icon text-ink-soft hover:bg-ink/5">
                        <Pencil size={16} aria-hidden="true" />
                      </button>
                      <button type="button" onClick={() => removeRow(it.key)}
                              aria-label={t('installments.removeItem', { name: it.product_name.trim(), defaultValue: 'Mahsulotni olib tashlash: {{name}}' })}
                              className="tap-target grid h-11 w-11 shrink-0 place-items-center rounded-icon text-danger hover:bg-[color:var(--c-danger-soft)]">
                        <Trash2 size={16} aria-hidden="true" />
                      </button>
                    </li>
                  );
                })}
              </ul>
            )}

            {/* The one open editor. */}
            {activeItem && (() => {
              const it = activeItem;
              const position = items.findIndex((x) => x.key === it.key) + 1;
              const identifier = validateIdentifier(it.identifier_type, it.serial_number);
              // (MUDD-85/98) What the SELECTED category itself requires at sale
              // time — IMEI 1/2 for phones, VIN for cars. Empty for a category
              // with no schema (or none picked yet), same as today.
              const categorySchema = schemaForCategory(it.category);
              return (
                <div key={it.key} data-testid="item-editor"
                     className="rounded-control border border-line bg-cream p-3">
                  <div className="mb-2 flex items-center justify-between gap-2">
                    <span className="text-caption text-muted">#{position}</span>
                    {items.length > 1 && (
                      <button type="button" onClick={() => removeRow(it.key)}
                              aria-label={t('installments.removeItem', {
                                name: it.product_name.trim() || `#${position}`,
                                defaultValue: 'Mahsulotni olib tashlash: {{name}}',
                              })}
                              className="tap-target grid h-11 w-11 place-items-center rounded-icon text-danger hover:bg-[color:var(--c-danger-soft)]">
                        <Trash2 size={16} aria-hidden="true" />
                      </button>
                    )}
                  </div>

                  {/* Catalog picker — the tenant's own segment first, "show
                      all" to cross it. Purely optional: it fills product_name
                      + catalog_model_id + variant_label; the row still works
                      as free text if the shop skips it. */}
                  <div className="mb-2 flex items-center justify-between gap-2 rounded-control border border-dashed border-line bg-surface px-2.5 py-2">
                    <div className="min-w-0 text-caption">
                      {it.catalog_model_id ? (
                        <span className="truncate font-medium text-ink">
                          {it.product_name}
                          {it.variant_label && <span className="text-ink-soft"> · {it.variant_label}</span>}
                        </span>
                      ) : (
                        <span className="text-muted">{t('installments.pickFromCatalogHint', 'Katalogdan tanlasa, nom va variant avtomatik to‘ldiriladi')}</span>
                      )}
                    </div>
                    <Button variant="outline" size="sm" type="button" onClick={() => setCatalogPickFor(it.key)}>
                      <Layers size={14} /> {t('installments.pickFromCatalog', 'Katalogdan')}
                    </Button>
                  </div>

                  {/* (MUDD-83) Category + name. That is the whole line.
                      The catalog picker above stays available and fills both,
                      but it is no longer the only way to say what was sold —
                      and it is no longer FOUR steps to say "a car". */}
                  <div className="mb-2">
                    <Field label={t('installments.itemCategory', 'Toifa')}
                           hint={t('installments.itemCategoryHint', 'Hisobot va filtr shu bo‘yicha ishlaydi')}>
                      <Select
                        id={`item-category-${it.key}`}
                        value={it.category ?? ''}
                        onChange={(v) => {
                          setCatIdErr(null);
                          // A new category means a new (possibly empty) schema
                          // — a value typed for the OLD one must not silently
                          // ride along under a key the new fields don't show.
                          patchItem(it.key, { category: v || undefined, identifiers: {} });
                        }}
                        // (MUDD-92 H1) `categoryOptions` only lists ACTIVE
                        // categories (that is what the picker should offer for
                        // a NEW pick), but an existing line can carry a slug
                        // that was retired since it was sold — the server now
                        // accepts that round-trip (see `_resolve_item_categories`)
                        // instead of 400ing it, so the field must not go blank
                        // and silently forget it. `useCatalogCategories` never
                        // returns a retired row's name, so the slug itself is
                        // the only label available — still visible beats gone,
                        // and picking anything else replaces it same as before.
                        options={it.category && !categoryOptions.some((o) => o.value === it.category)
                          ? [...categoryOptions, { value: it.category, label: it.category }]
                          : categoryOptions}
                        placeholder={categoriesQ.isPending
                          ? t('common.loading', 'Yuklanmoqda…')
                          : t('installments.itemCategoryPh', 'Toifani tanlang')}
                        disabled={categoriesQ.isPending}
                        searchable
                      />
                    </Field>
                  </div>

                  {/* (MUDD-85/98) The selected category's own identifier
                      fields — IMEI 1/2 for a phone, VIN for a car, etc. Only
                      the fields ITS schema declares render; a category with
                      none (or no category picked yet) shows nothing here. An
                      old contract's line can hit a NOW-required field it
                      never carried before (the category gained a schema after
                      it was sold) — the same "fill it in" bar applies to an
                      edit as to a new sale, so this renders identically for
                      both. */}
                  {categorySchema.length > 0 && (
                    <div className="mb-2 grid gap-2 sm:grid-cols-2">
                      {categorySchema.map((field) => {
                        const raw = it.identifiers?.[field.key] ?? '';
                        const liveErrors = raw
                          ? validateCategoryIdentifiers([field], { [field.key]: raw }) : {};
                        const serverCode = catIdErr && catIdErr.itemKey === it.key && catIdErr.fieldKey === field.key
                          ? catIdErr.code : undefined;
                        const shownCode = serverCode ?? liveErrors[field.key];
                        return (
                          <Field key={field.key}
                                 label={fieldLabel(field)}
                                 required={field.required}
                                 error={shownCode ? t(`identifier.${shownCode}`) : undefined}>
                            <input
                              className={inputCls}
                              inputMode={field.type === 'number' ? 'numeric' : 'text'}
                              maxLength={field.max_len ?? 64}
                              value={raw}
                              onChange={(e) => {
                                if (catIdErr && catIdErr.itemKey === it.key && catIdErr.fieldKey === field.key) {
                                  setCatIdErr(null);
                                }
                                patchItem(it.key, { identifiers: { ...it.identifiers, [field.key]: e.target.value } });
                              }}
                            />
                          </Field>
                        );
                      })}
                    </div>
                  )}

                  <div className="grid gap-2 md:grid-cols-[1fr_120px]">
                    <Field label={t('installments.itemName', 'Mahsulot nomi')} required>
                      <input
                        id={`item-name-${it.key}`}
                        className={inputCls}
                        placeholder={t('installments.itemNamePh')}
                        value={it.product_name}
                        onChange={(e) => patchItem(it.key, { product_name: e.target.value })}
                      />
                    </Field>
                    <Field label={t('installments.qty')}>
                      <input type="text" inputMode="numeric" className={inputCls}
                             value={it.qty}
                             onChange={(e) => patchItem(it.key, { qty: Math.max(1, Number(e.target.value.replace(/\D/g, '') || 0)) })} />
                    </Field>
                  </div>

                  <div className="mt-2 grid gap-2 md:grid-cols-2">
                    <Field label={t('installments.unitPrice')} hint={currency}>
                      <MoneyInput value={it.unit_price}
                                  onChange={(n) => patchItem(it.key, { unit_price: n })} />
                    </Field>
                    {/* An IMEI and a shop's own reference are different things
                        with different rules, so the line says which it carries
                        and is checked accordingly. */}
                    <Field label={t('installments.identifier', 'Identifikator')}
                           error={it.serial_number && identifier.error
                             ? t(`identifier.${identifier.error}`) : undefined}>
                      <div className="grid gap-1.5">
                        <Select
                          value={it.identifier_type ?? ''}
                          onChange={(v) => patchItem(it.key, {
                            identifier_type: (v || undefined) as SelectableIdentifierType | undefined,
                          })}
                          placeholder={t('installments.identifierNone', 'Yo‘q')}
                          options={SELECTABLE_IDENTIFIER_TYPES.map((k) => ({
                            value: k,
                            label: k === 'IMEI'
                              ? t('identifier.kindImei', 'IMEI')
                              : t('identifier.kindSerial', 'Seriya / shartnoma raqami'),
                          }))}
                        />
                        {it.identifier_type && (
                          <div className="flex gap-1.5">
                            <input className={inputCls}
                                   aria-label={t('installments.identifier', 'Identifikator')}
                                   inputMode={it.identifier_type === 'IMEI' ? 'numeric' : 'text'}
                                   maxLength={MAX_IDENTIFIER_LENGTH}
                                   value={it.serial_number ?? ''}
                                   onChange={(e) => patchItem(it.key, { serial_number: e.target.value })}
                                   onBlur={(e) => void runImeiCheck(it.key, e.target.value)} />
                            <button type="button" onClick={() => setScanFor(it.key)}
                                    aria-label={t('installments.scan', 'Skanerlash')}
                                    className="tap-target grid h-11 w-11 shrink-0 place-items-center rounded-control border border-line bg-white hover:border-ink/30">
                              <ScanLine size={16} aria-hidden="true" />
                            </button>
                          </div>
                        )}
                      </div>
                      {imeiWarn[it.key] && (
                        <div className="mt-1 flex items-center gap-1 text-micro text-danger">
                          <AlertTriangle size={12} />
                          {imeiWarn[it.key].in_tenant
                            ? t('installments.imeiDupHere', 'Bu IMEI sizda allaqachon sotilgan')
                            : t('installments.imeiDupCross', { count: imeiWarn[it.key].cross_tenant_count,
                                defaultValue: 'Bu IMEI boshqa do\'kon(lar)da sotilgan' })}
                        </div>
                      )}
                    </Field>
                  </div>

                  {/* (#524) Per-row subtotal so each line can be sanity-checked. */}
                  {it.unit_price > 0 && (
                    <div className="mt-2 flex items-center justify-between border-t border-line pt-2 text-caption">
                      <span className="tabular-nums text-muted">{it.qty} × {money(it.unit_price)}</span>
                      <span className="font-semibold tabular-nums">{money(it.qty * it.unit_price)} {currency}</span>
                    </div>
                  )}
                </div>
              );
            })()}

            {itemErr && (
              <p role="alert" className="mt-1.5 text-caption font-medium text-danger">{itemErr}</p>
            )}

            {/* Decoding happens in this tab; no frame is uploaded anywhere. */}
            <BarcodeScanner
              open={scanFor !== null}
              onClose={() => setScanFor(null)}
              onResult={(raw) => { if (scanFor) onScanned(scanFor, raw); }}
              title={t('installments.scanTitle', 'IMEI / shtrix-kodni skanerlash')}
            />

            <ProductPickerModal
              open={catalogPickFor !== null}
              onClose={() => setCatalogPickFor(null)}
              onPick={onPickCatalogProduct}
            />

            {/* Adding is only offered once the open line is finished — the
                reason is stated rather than left to a dead button. */}
            <div className="mt-3">
              {/* `sm` is 36px; this is a primary action on a phone, so the hit
                  area grows to 44px on coarse pointers only — desktop keeps the
                  compact size. */}
              <Button variant="outline" size="sm" type="button" onClick={addRow}
                      className="tap-target"
                      disabled={!activeItem || !itemComplete(activeItem, schemaForCategory(activeItem.category))}>
                <Plus size={14} /> {t('installments.addItem')}
              </Button>
              {activeItem && !itemComplete(activeItem, schemaForCategory(activeItem.category)) && (
                <p className="mt-1.5 text-caption text-muted">
                  {t('installments.finishItemFirst', 'Avval joriy mahsulotni to‘ldiring — nomi va narxi.')}
                </p>
              )}
            </div>

            {/* (#524) Goods total on the Items step — previously visible only in
                the step-3 calc panel, so the sale amount couldn't be checked here. */}
            {goodsTotal > 0 && (
              <div className="mt-3 flex items-center justify-between rounded-control border border-line bg-cream px-3 py-2.5 text-body">
                <span className="text-muted">{t('installments.itemsTotal', 'Mahsulotlar jami')}</span>
                <span className="font-semibold tabular-nums">{money(goodsTotal)} {currency}</span>
              </div>
            )}
          </section>
          )}

          {/* GUARANTORS (KAFIL) */}
          {step === 2 && (
          <section>
            {/* A guarantor is optional; say so, or the step reads as a
                mandatory stop. Skipping is the footer's ordinary Next. */}
            <div className="mb-2 flex items-center justify-between">
              <div className="text-micro uppercase tracking-wider text-muted">
                {t('installments.guarantors', 'Kafillar')}
                <span className="ml-2 normal-case tracking-normal text-muted">
                  {t('common.optional', 'ixtiyoriy')}
                </span>
                {guarantorIds.length > 0 && (
                  <span className="ml-2 rounded-pill bg-ink px-2 py-0.5 text-micro text-white">
                    {guarantorIds.length}
                  </span>
                )}
              </div>
              <Button variant="outline" size="sm" type="button"
                      onClick={() => setNewG({ full_name: '', phone: '', relationship: '' })}>
                <UserPlus size={14} /> {t('installments.addGuarantor', 'Yangi kafil')}
              </Button>
            </div>

            {newG && (
              <div className="mb-2 grid gap-2 rounded-control border border-line bg-cream p-3 md:grid-cols-3">
                <input className={inputCls}
                       aria-label={t('customers.fullName', 'F.I.O')}
                       placeholder={t('customers.fullName') ?? 'F.I.O'}
                       value={newG.full_name}
                       onChange={(e) => setNewG({ ...newG, full_name: e.target.value })} />
                <PhoneInput value={newG.phone} onChange={(v) => setNewG({ ...newG, phone: v })} />
                <input className={inputCls}
                       aria-label={t('installments.relationship', 'Kim bo\'ladi (aka, ota...)')}
                       placeholder={t('installments.relationship', 'Kim bo\'ladi (aka, ota...)') ?? ''}
                       value={newG.relationship ?? ''}
                       onChange={(e) => setNewG({ ...newG, relationship: e.target.value })} />
                <div className="flex gap-2 md:col-span-3">
                  <Button variant="primary" size="sm" type="button"
                          onClick={() => void addNewGuarantor()} disabled={createGuarantor.isPending}>
                    {createGuarantor.isPending && <Loader2 size={14} className="animate-spin" />}
                    {t('common.save')}
                  </Button>
                  <Button variant="outline" size="sm" type="button" onClick={() => setNewG(null)}>
                    {t('common.cancel')}
                  </Button>
                </div>
              </div>
            )}

            {/* Filters the loaded guarantors client-side, so no debounced
                callback — `onChange` keeps it responsive per keystroke. */}
            <SearchInput
              value={guarantorQ}
              onChange={setGuarantorQ}
              placeholder={t('installments.guarantorSearchPh', 'Kafil qidirish...')}
              className="mb-2 max-w-none"
            />
            <p className="mb-2 text-caption text-muted">
              {t('installments.guarantorSkipHint', 'Kafil shart emas — “Keyingisi” tugmasini bosib o‘tkazib yuborishingiz mumkin.')}
            </p>
            <div className="max-h-[160px] overflow-y-auto rounded-control border border-line">
              {!guarantors.isLoading && visibleGuarantors.length === 0 && (
                <EmptyState size="compact" icon={<Users size={18} />}
                            title={t('installments.noGuarantors', 'Kafillar yo\'q — yangi qo\'shing yoki tashlab keting')} />
              )}
              <ul className="divide-y divide-line">
                {visibleGuarantors.map((g) => (
                  <li key={g.id}>
                    <label className="flex cursor-pointer items-center gap-3 px-3 py-2 text-body hover:bg-cream">
                      <input type="checkbox"
                             checked={guarantorIds.includes(g.id)}
                             onChange={() => toggleGuarantor(g.id)} />
                      <span className="min-w-0 flex-1 truncate">
                        <span className="font-medium">{g.full_name}</span>
                        <span className="ml-2 text-caption text-ink-soft">{displayPhone(g.phone)}</span>
                        {g.relationship && <span className="ml-2 text-micro text-muted">· {g.relationship}</span>}
                      </span>
                    </label>
                  </li>
                ))}
              </ul>
            </div>
          </section>
          )}

          {/* TERMS */}
          {step === 3 && (<>
          <section>
            <div className="mb-2 text-micro uppercase tracking-wider text-muted">
              {t('installments.terms')}
            </div>
            <div className="grid gap-3 md:grid-cols-4">
              <Field label={t('installments.months')} required
                     hint={t('installments.maxMonthsHint', { max: maxMonths, defaultValue: `Maks. ${maxMonths} oy` })}>
                <input type="text" inputMode="numeric" className={inputCls}
                       value={months}
                       onChange={(e) => setMonths(Math.max(1, Math.min(maxMonths, Number(e.target.value.replace(/\D/g, '') || 6))))} />
              </Field>
              {/* (#520) With a tenant min-down rule the field is functionally
                  mandatory: mark it required and surface the threshold in the
                  hint up-front instead of only erroring after a violation. */}
              <Field label={t('installments.downPayment')} required={minDownPct > 0}
                     hint={minDownPct > 0
                       ? t('installments.minDownHint', { amount: money(minDown), pct: minDownPct, defaultValue: `Kamida ${money(minDown)} (${minDownPct}%)` })
                       : currency}
                     error={
                       // Too-high wins: when the payment already covers the goods,
                       // telling the owner it is below a MINIMUM is nonsense — both
                       // rules are violated and only one of them is actionable.
                       downTooHigh
                         ? t('installments.errDownTooHigh', { defaultValue: "Boshlang'ich to'lov tovar narxidan kam bo'lishi kerak" })
                         : downTooLow
                           ? t('installments.errDownTooLow', { amount: money(minDown), pct: minDownPct, defaultValue: `Kamida ${money(minDown)} (${minDownPct}%)` })
                           : undefined
                     }>
                <MoneyInput value={down} onChange={setDown} />
              </Field>
              <Field label={t('installments.financingModel', 'Hisoblash usuli')}>
                <Select
                  value={calcFormula}
                  onChange={(v) => { setCalcTouched(true); setCalcFormula(v as CalcFormula); }}
                  options={FORMULA_OPTIONS}
                />
              </Field>
              {calcFormula === 'markup' ? (
                <Field label={t('installments.markupPct', 'Ustama')} hint="%">
                  <input type="text" inputMode="decimal" className={inputCls}
                         value={markup}
                         onChange={(e) => {
                           setCalcTouched(true);
                           // (#526) text+decimal keypad: keep valid partials like "0."
                           // while typing; reject extra dots; clamp only the ceiling.
                           const raw = e.target.value.replace(/[^\d.]/g, '');
                           if (raw === '') { setMarkup(''); return; }
                           if (!/^\d*\.?\d*$/.test(raw)) return;
                           const n = Number(raw);
                           if (!isNaN(n)) setMarkup(n > 200 ? '200' : raw);
                         }} />
                </Field>
              ) : calcFormula === 'none' ? (
                <Field label={t('installments.startDate')}>
                  <DatePicker value={startDate} onChange={(iso) => setStartDate(iso)} />
                </Field>
              ) : (
                <Field label={t('installments.interestPct')} required hint={t('installments.monthlyPctHint', 'oylik %')}>
                  <input type="text" inputMode="decimal" className={inputCls}
                         value={interest}
                         onChange={(e) => {
                           setCalcTouched(true);
                           const raw = e.target.value.replace(/[^\d.]/g, '');
                           if (raw === '') { setInterest(''); return; }
                           if (!/^\d*\.?\d*$/.test(raw)) return;
                           const n = Number(raw);
                           if (!isNaN(n)) setInterest(n > 1000 ? '1000' : raw);
                         }} />
                </Field>
              )}
            </div>
            {calcFormula !== 'none' && (
              <div className="mt-3">
                <Field label={t('installments.startDate')}>
                  <DatePicker value={startDate} onChange={(iso) => setStartDate(iso)} />
                </Field>
              </div>
            )}
          </section>

          {/* NOTES */}
          <Field label={t('installments.notes')}>
            <Textarea value={notes} onChange={setNotes} maxLength={500} rows={3} autosize />
          </Field>

          {/* LIVE CALC */}
          <section className="rounded-control bg-ink p-4 text-white">
            <div className="grid grid-cols-3 gap-3">
              <Stat label={t('installments.total')}     value={total} />
              <Stat label={t('installments.principal')} value={principal} />
              <Stat label={t('installments.monthly')}   value={monthly} highlight
                    sub={`× ${months} ${t('installments.monthShort', 'oy')}`} />
            </div>
            {/* (#528) Surface the financing delta (markup/interest charged over the
                goods price) so the owner sees what they're charging the customer.
                computeSchedule already returns markupAmount = principal − financed. */}
            {calc.markupAmount > 0 && (
              <div className="mt-3 flex items-center justify-between border-t border-white/10 pt-3 text-caption">
                <span className="text-white/70">{t('installments.financing', 'Ustama / foiz')}</span>
                <span className="font-mono font-semibold tabular-nums text-accent">+{money(calc.markupAmount)} {currency}</span>
              </div>
            )}
          </section>
          </>)}

          {/* SUMMARY — the last thing seen before the commit. Read-only on
              purpose: it restates the agreement rather than offering another
              chance to edit it. Back returns to any step, values intact. */}
          {step === 4 && (
          <section className="space-y-4">
            <p className="text-body text-ink-soft">
              {t('installments.summaryIntro', 'Shartnoma quyidagi shartlar bilan yaratiladi. Tekshirib chiqing.')}
            </p>

            <dl className="divide-y divide-line rounded-control border border-line">
              <SummaryRow label={t('installments.customer')} value={selectedCustomerName || '—'} />
              <SummaryRow label={t('installments.items')} value={itemsSummary} />
              <SummaryRow label={t('installments.itemsTotal', 'Mahsulotlar jami')}
                          value={`${money(goodsTotal)} ${currency}`} />
              <SummaryRow label={t('installments.downPayment')}
                          value={`${money(down)} ${currency}`} />
              <SummaryRow label={t('installments.financingModel', 'Hisoblash usuli')}
                          value={FORMULA_OPTIONS.find((o) => o.value === calcFormula)?.label ?? calcFormula} />
              {calc.markupAmount > 0 && (
                <SummaryRow label={t('installments.financing', 'Ustama / foiz')}
                            value={`+${money(calc.markupAmount)} ${currency}`} />
              )}
              <SummaryRow label={t('installments.months')}
                          value={`${months} ${t('installments.monthShort', 'oy')}`} />
              {guarantorIds.length > 0 && (
                <SummaryRow label={t('installments.guarantors', 'Kafillar')} value={String(guarantorIds.length)} />
              )}
              {startDate && <SummaryRow label={t('installments.startDate')} value={startDate} />}
            </dl>

            {/* The two figures the customer will actually be told. */}
            <div className="rounded-control bg-ink p-4 text-white">
              <div className="grid grid-cols-2 gap-3">
                <Stat label={t('installments.monthly')} value={monthly} highlight
                      sub={`× ${months} ${t('installments.monthShort', 'oy')}`} />
                <Stat label={t('installments.total')} value={total} />
              </div>
            </div>
          </section>
          )}

          {err && (
            <div role="alert" aria-live="assertive"
                 className="rounded-control border border-danger/30 bg-[color:var(--c-danger-soft)] px-4 py-3 text-body text-danger">
              {err}
            </div>
          )}
        </StepForm>
      </Modal>

      <Modal
        open={!!imeiModal}
        onClose={() => setImeiModal(null)}
        title={t('installments.imeiWarnTitle', 'IMEI ogohlantirishi')}
        size="sm"
        footer={
          <Button variant="primary" size="md" onClick={() => setImeiModal(null)}>
            {t('common.understood', 'Tushundim')}
          </Button>
        }
      >
        {imeiModal && (
          <div className="space-y-3 text-body">
            <div className="flex items-center gap-2 text-danger">
              <AlertTriangle size={18} />
              <span className="font-mono">{imeiModal.serial}</span>
            </div>
            {imeiModal.in_tenant && (
              <p>{t('installments.imeiDupHereLong',
                'Bu IMEI sizning do\'koningizda allaqachon sotilgan. Takror sotishdan oldin tekshiring.')}</p>
            )}
            {imeiModal.cross_tenant_count > 0 && (
              <p>{t('installments.imeiDupCrossLong', {
                count: imeiModal.cross_tenant_count,
                defaultValue: 'Bu IMEI boshqa do\'kon(lar)da sotilgan. Qurilma qayta sotilayotgan bo\'lishi mumkin.',
              })}</p>
            )}
          </div>
        )}
      </Modal>
    </>
  );
}

function SummaryRow({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex items-start justify-between gap-4 px-4 py-2.5 text-body">
      <dt className="shrink-0 text-muted">{label}</dt>
      <dd className="min-w-0 text-right font-medium tabular-nums">{value}</dd>
    </div>
  );
}

function Stat({ label, value, highlight, sub }: { label: string; value: number; highlight?: boolean; sub?: string }) {
  const currency = useCurrency();
  return (
    <div>
      <div className="text-micro uppercase tracking-wider text-white/55">{label}</div>
      <div className={`mt-1 tabular-nums ${highlight ? 'text-section font-semibold text-accent' : 'text-subsection font-medium'}`}>
        {money(value)} <span className="text-micro font-normal opacity-70">{currency}</span>
      </div>
      {sub && <div className="mt-0.5 text-micro text-white/55">{sub}</div>}
    </div>
  );
}
