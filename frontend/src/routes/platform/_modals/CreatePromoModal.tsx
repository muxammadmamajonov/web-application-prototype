import { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Loader2 } from 'lucide-react';
import { Modal, Field, inputCls, inputErrCls } from '@/components/ui/Modal';
import { MoneyInput, PercentInput } from '@/components/ui/FormFields';
import { Switch } from '@/components/ui/Switch';
import { Button } from '@/components/ui/Button';
import { cn } from '@/lib/cn';
import { focusFirstInvalid, hasErrors, validate } from '@/lib/forms';
import { useCreatePromo, useUpdatePromo } from '@/lib/mutations';
import type { PromoRow } from '@/lib/queries';

interface Props { open: boolean; onClose: () => void; editing?: PromoRow | null }

export function CreatePromoModal({ open, onClose, editing }: Props) {
  const { t } = useTranslation();
  const isEdit = !!editing;

  // ── Form state ────────────────────────────────────────────────────────────
  const [code, setCode] = useState('');
  const [kind, setKind] = useState<'percent' | 'fixed'>('percent');
  // Typed input (PercentInput / MoneyInput) — always a clamped, non-negative number.
  const [value, setValue] = useState(10);
  const [desc, setDesc] = useState('');
  const [maxUses, setMaxUses] = useState('');
  const [active, setActive] = useState(true);

  // ── Per-field errors ──────────────────────────────────────────────────────
  const [codeErr, setCodeErr] = useState<string | null>(null);
  const [valueErr, setValueErr] = useState<string | null>(null);
  const [maxUsesErr, setMaxUsesErr] = useState<string | null>(null);

  // ── Mutations ─────────────────────────────────────────────────────────────
  const create = useCreatePromo();
  const update = useUpdatePromo();
  const m = isEdit ? update : create;

  // ── Seed / reset when the modal opens or switches editing target ──────────
  useEffect(() => {
    if (editing) {
      setCode(editing.code);
      setKind(editing.kind);
      setValue(editing.value);
      setDesc(editing.description ?? '');
      setMaxUses(editing.max_uses != null ? String(editing.max_uses) : '');
      setActive(editing.is_active);
    } else {
      setCode(''); setKind('percent'); setValue(10); setDesc(''); setMaxUses(''); setActive(true);
    }
    setCodeErr(null); setValueErr(null); setMaxUsesErr(null);
  }, [editing, open]);

  // ── Field validators ──────────────────────────────────────────────────────
  const validateCode = (v: string): string | null => {
    const err = validate.required(v, t('platform.createPromo.codeRequired', 'Kod kiritish shart'))
      || validate.minLen(v, 2, t('platform.createPromo.codeTooShort', 'Kamida 2 ta belgi'));
    return err || null;
  };

  // PercentInput / MoneyInput already clamp to a non-negative, in-range number
  // as the user types, so the only thing left to validate here is "not zero".
  const validateValue = (n: number, k: 'percent' | 'fixed'): string | null => {
    if (n <= 0) return t('platform.createPromo.valuePositive', "0 dan katta bo'lishi shart");
    if (k === 'percent' && n > 100) return t('platform.createPromo.rangeError', "0–100 oralig'ida bo'lsin");
    return null;
  };

  const validateMaxUses = (v: string): string | null => {
    if (!v.trim()) return null; // optional field
    const n = Number(v);
    if (!Number.isFinite(n) || n < 1 || !Number.isInteger(n)) {
      return t('platform.createPromo.maxUsesInvalid', 'Musbat butun son kiriting');
    }
    return null;
  };

  // ── Submit ────────────────────────────────────────────────────────────────
  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    // Run all validators, collect errors map.
    const errs = {
      code: !isEdit ? (validateCode(code) ?? '') : '',
      value: validateValue(value, kind) ?? '',
      maxUses: validateMaxUses(maxUses) ?? '',
    };
    setCodeErr(errs.code || null);
    setValueErr(errs.value || null);
    setMaxUsesErr(errs.maxUses || null);

    if (hasErrors(errs)) { focusFirstInvalid(); return; }

    if (isEdit && editing) {
      await update.mutateAsync({
        id: editing.id,
        body: {
          kind,
          value,
          description: desc.trim() || null,
          max_uses: maxUses.trim() ? Number(maxUses) : null,
          is_active: active,
        },
      });
    } else {
      await create.mutateAsync({
        code: code.trim().toUpperCase(),
        kind,
        value,
        description: desc.trim() || undefined,
        max_uses: maxUses.trim() ? Number(maxUses) : null,
      });
    }
    onClose();
  };

  // ── Kind toggle buttons — styled segment control ──────────────────────────
  const KindToggle = ({ k, label }: { k: 'percent' | 'fixed'; label: string }) => (
    <button
      type="button"
      onClick={() => {
        setKind(k);
        // Re-validate value against the new kind immediately (e.g. a value >100
        // entered under "fixed" must be flagged the moment the user switches
        // back to "percent").
        setValueErr(validateValue(value, k));
      }}
      className={cn(
        'flex-1 h-11 rounded-xl border text-sm font-medium transition',
        kind === k
          ? 'border-ink bg-ink text-white'
          : 'border-line bg-cream text-ink-soft hover:border-ink/30 hover:text-ink',
      )}
    >
      {label}
    </button>
  );

  return (
    <Modal
      open={open}
      onClose={onClose}
      title={isEdit ? t('platform.createPromo.editTitle') : t('platform.createPromo.newTitle')}
      subtitle={t('platform.createPromo.subtitle')}
      footer={
        <>
          <Button variant="ghost" type="button" onClick={onClose}>
            {t('common.cancel')}
          </Button>
          <Button
            variant="primary"
            type="submit"
            form="create-promo-form"
            loading={m.isPending}
          >
            {m.isPending
              ? (<><Loader2 size={16} className="animate-spin" /> {t('platform.createPromo.saving')}</>)
              : (isEdit ? t('common.save') : t('platform.createPromo.create'))}
          </Button>
        </>
      }
    >
      <form id="create-promo-form" onSubmit={submit} className="flex flex-col gap-4">

        {/* Code — disabled in edit mode (codes are immutable after creation). */}
        <Field
          label={t('platform.createPromo.code')}
          required
          error={codeErr}
          hint={isEdit ? t('platform.createPromo.codeImmutable', "Kod yaratilgandan so'ng o'zgartirib bo'lmaydi") : undefined}
        >
          <input
            required
            minLength={2}
            maxLength={32}
            disabled={isEdit}
            value={code}
            onChange={(e) => {
              const v = e.target.value.toUpperCase();
              setCode(v);
              if (codeErr) setCodeErr(validateCode(v));
            }}
            onBlur={(e) => setCodeErr(validateCode(e.target.value))}
            className={cn(inputCls, 'font-mono', isEdit && 'opacity-60', codeErr && inputErrCls)}
            placeholder="LAUNCH25"
          />
        </Field>

        {/* Kind — segmented toggle (never a native <select>). */}
        <Field label={t('platform.createPromo.kind')} required>
          <div className="flex gap-2">
            <KindToggle k="percent" label={t('platform.createPromo.percent')} />
            <KindToggle k="fixed" label={t('platform.createPromo.fixed')} />
          </div>
        </Field>

        {/* Discount value — PercentInput (0–100, hard-clamped) for percent kind,
            MoneyInput (grouped thousands, capped) for a fixed UZS amount. Never a
            raw number input — label + validation message both change with kind. */}
        <Field
          label={kind === 'percent'
            ? t('platform.createPromo.discountPercent')
            : t('platform.createPromo.discountFixed')}
          required
          error={valueErr}
        >
          {kind === 'percent' ? (
            <PercentInput
              value={value}
              onChange={(n) => {
                setValue(n);
                if (valueErr) setValueErr(validateValue(n, kind));
              }}
            />
          ) : (
            <MoneyInput
              value={value}
              onChange={(n) => {
                setValue(n);
                if (valueErr) setValueErr(validateValue(n, kind));
              }}
            />
          )}
        </Field>

        {/* Max uses — optional; blank = unlimited. */}
        <Field
          label={t('platform.createPromo.maxUses')}
          hint={t('platform.createPromo.maxUsesHint')}
          error={maxUsesErr}
        >
          <input
            type="number"
            min="1"
            step="1"
            value={maxUses}
            onChange={(e) => {
              setMaxUses(e.target.value);
              if (maxUsesErr) setMaxUsesErr(validateMaxUses(e.target.value));
            }}
            onBlur={(e) => setMaxUsesErr(validateMaxUses(e.target.value))}
            className={cn(inputCls, maxUsesErr && inputErrCls)}
            placeholder="100"
          />
        </Field>

        {/* Description — optional, plain text. */}
        <Field label={t('platform.createPromo.description')}>
          <input
            value={desc}
            onChange={(e) => setDesc(e.target.value)}
            className={inputCls}
            maxLength={200}
            placeholder={t('platform.createPromo.descriptionPh', 'Birinchi 100 ta uchun chegirma')}
          />
        </Field>

        {/* Active toggle — only in edit mode (new promos start active by default). */}
        {isEdit && (
          <label className="flex cursor-pointer items-center gap-3 text-sm">
            <Switch checked={active} onChange={setActive} />
            <span className="font-medium">{t('platform.createPromo.activeLabel')}</span>
          </label>
        )}

        {/* API error banner — inline, never routes to generic error screen. */}
        {m.isError && (
          <div className="rounded-xl border border-danger/30 bg-[color:var(--c-danger-soft)] px-4 py-3 text-sm text-danger">
            {(m.error as Error)?.message ?? t('common.errorLoading', 'Xatolik yuz berdi')}
          </div>
        )}
      </form>
    </Modal>
  );
}
