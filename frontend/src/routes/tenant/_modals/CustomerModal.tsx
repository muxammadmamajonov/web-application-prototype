import { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { useQueryClient } from '@tanstack/react-query';
import { Check, ChevronDown, ChevronUp, Copy, Loader2, Send } from 'lucide-react';
import { shareViaTelegram } from '@/lib/telegram';
import { Modal, Field, inputCls } from '@/components/ui/Modal';
import { Switch } from '@/components/ui/Switch';
import { Button } from '@/components/ui/Button';
import { PhoneInput, PassportInput, MoneyInput, Textarea, joinPassport } from '@/components/ui/FormFields';
import { DatePicker } from '@/components/ui/DatePicker';
import { localToday } from '@/lib/format';
import { phoneOrEmpty, isValidUzPhone } from '@/lib/phone';
import { focusFirstInvalid, validate, hasErrors } from '@/lib/forms';
import { RiskBadge } from '@/components/RiskBadge';
import { api } from '@/lib/api';
import { apiErrorMessage } from '@/lib/errors';
import {
  useCreateCustomer, useUpdateCustomer, type CustomerWrite, type CustomerInvite,
} from '@/lib/mutations';
import {
  useCustomerChannels, type CustomerRow, type ChannelType, type CustomerChannel,
} from '@/lib/queries';
import { queryKeys } from '@/lib/queryKeys';
import { useUnsavedGuard } from '@/hooks/useUnsavedGuard';
import { toast } from '@/lib/feedback';

interface Props {
  open: boolean;
  onClose: () => void;
  /** Pass an existing customer to switch into "edit" mode. */
  existing?: CustomerRow | null;
}

const EMPTY: CustomerWrite = {
  full_name: '',
  phone: '',
  secondary_phone: '',
  passport_series: '',
  passport_number: '',
  jshshir: '',
  birthdate: '',
  address: '',
  notes: '',
  employment_place: '',
  monthly_income: null,
};

/** Create or edit a customer — passport split fields + JSHSHIR (14-digit). */
export function CustomerModal({ open, onClose, existing }: Props) {
  const { t } = useTranslation();
  const create = useCreateCustomer();
  const update = useUpdateCustomer();
  const [form, setForm] = useState<CustomerWrite>(EMPTY);
  const [err, setErr] = useState<string | null>(null);
  // (§4) Client-side validation errors surface inline under each field; only
  // server/network failures fall through to the `err` banner below.
  const [errors, setErrors] = useState<Record<string, string>>({});
  // One-time join link surfaced after a successful CREATE (not edit).
  const [invite, setInvite] = useState<CustomerInvite | null>(null);
  // Optional detail is collapsed by default, and expanded for a customer who
  // already carries some of it so editing never hides real data.
  const [showMore, setShowMore] = useState(false);

  useEffect(() => {
    if (!open) return;
    if (existing) {
      setForm({
        full_name:       existing.full_name,
        // Same as ProfilePage: canonical storage must go through the mask on
        // the way IN, or editing a customer shows their number unformatted.
        phone:           phoneOrEmpty(existing.phone),
        secondary_phone: phoneOrEmpty(existing.secondary_phone ?? ''),
        passport_series: existing.passport_series ?? '',
        passport_number: existing.passport_number ?? '',
        jshshir:         existing.jshshir ?? '',
        birthdate:       existing.birthdate ?? '',
        address:         existing.address ?? '',
        notes:           existing.notes ?? '',
        telegram_id:     existing.telegram_id ?? null,
        employment_place: existing.employment_place ?? '',
        monthly_income:  existing.monthly_income ?? null,
      });
    } else {
      setForm(EMPTY);
    }
    setErr(null);
    setErrors({});
    setInvite(null);
    // Open the optional section when the customer already has any of that
    // detail — collapsing it would hide real data from whoever maintains it.
    setShowMore(!!existing && !!(
      existing.secondary_phone || existing.passport_series || existing.passport_number
      || existing.jshshir || existing.birthdate || existing.address
      || existing.employment_place || existing.monthly_income || existing.notes
    ));
  }, [open, existing]);

  const setF = <K extends keyof CustomerWrite>(k: K, v: CustomerWrite[K]) => {
    setForm((p) => ({ ...p, [k]: v }));
    // Clear a field's inline error as soon as the user edits it.
    setErrors((p) => (p[k as string] ? { ...p, [k as string]: '' } : p));
  };

  const onSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setErr(null);
    // Normalize phones: a prefix-only "+998 " counts as empty.
    const normalized: CustomerWrite = {
      ...form,
      phone: phoneOrEmpty(form.phone),
      secondary_phone: phoneOrEmpty(form.secondary_phone ?? ''),
    };
    // (§4) Field-level validation — block the mutation and surface errors inline,
    // keeping every entered value. Required: name + a complete phone. The
    // secondary phone is optional but must be complete when present.
    const errs: Record<string, string> = {};
    errs.full_name = validate.required(form.full_name, "Ism kiritilishi shart");
    if (!normalized.phone) errs.phone = "Telefon raqam kiritilishi shart";
    else if (!isValidUzPhone(normalized.phone)) errs.phone = "Telefon raqam to'liq emas. 9 ta raqam kiriting.";
    if (normalized.secondary_phone && !isValidUzPhone(normalized.secondary_phone))
      errs.secondary_phone = "Telefon raqam to'liq emas. 9 ta raqam kiriting.";
    setErrors(errs);
    if (hasErrors(errs)) { focusFirstInvalid(); return; }
    // strip empties so backend Pydantic doesn't reject ""
    const payload: CustomerWrite = Object.fromEntries(
      Object.entries(normalized).filter(([_, v]) => v !== '' && v !== null && v !== undefined),
    ) as CustomerWrite;
    try {
      if (existing) {
        await update.mutateAsync({ id: existing.id, body: payload });
        // (#533) Explicit success feedback — the modal closing alone doesn't
        // confirm the save (an Esc/cancel close looks identical).
        toast.success(t('customers.toastUpdated', 'Mijoz yangilandi'));
        onClose();
      } else {
        const created = await create.mutateAsync(payload);
        toast.success(t('customers.toastCreated', 'Mijoz yaratildi'));
        // Keep the modal open showing the one-time join link until the user closes it.
        if (created?.invite) setInvite(created.invite);
        else onClose();
      }
    } catch (ex) {
      setErr(apiErrorMessage(ex));   // localized; never raw backend text
    }
  };

  const busy = create.isPending || update.isPending;

  // (#532) Unsaved-changes guard — prompt before discarding edited fields on
  // Esc/backdrop/Cancel. After a successful create the invite link is shown and
  // the work is already saved, so that close bypasses the guard.
  const requestClose = useUnsavedGuard(open, JSON.stringify(form), onClose);
  const guardedClose = invite ? onClose : requestClose;

  return (
    <Modal
      open={open}
      onClose={guardedClose}
      title={existing ? t('customers.edit') : t('customers.create')}
      subtitle={existing ? existing.full_name : t('customers.modalSub')}
      size="lg"
      footer={
        invite ? (
          <Button variant="primary" size="md" onClick={onClose}>
            {t('common.close', { defaultValue: 'Yopish' })}
          </Button>
        ) : (
          <>
            <Button variant="outline" size="md" onClick={requestClose} loading={busy}>
              {t('common.cancel')}
            </Button>
            <Button variant="primary" size="md" type="submit" form="customer-form" loading={busy}>
              {t('common.save')}
            </Button>
          </>
        )
      }
    >
              {/* `minmax(0,1fr)`, not `1fr`: a `1fr` column floors at its content's
            min-content width, so one unbreakable string (a join URL) widens the
            column past the sheet and the whole modal scrolls sideways. */}
        <form id="customer-form" onSubmit={onSubmit}
              className="grid gap-4 [grid-template-columns:minmax(0,1fr)] md:[grid-template-columns:repeat(2,minmax(0,1fr))]">
        {invite && (
          <div className="md:col-span-2">
            <InviteLinkPanel url={invite.url} />
          </div>
        )}
        <div className="md:col-span-2">
          <Field label={t('customers.fullName')} required error={errors.full_name}>
            <input className={inputCls} value={form.full_name} required
                   onChange={(e) => setF('full_name', e.target.value)} />
          </Field>
        </div>

        <Field label={t('customers.phone')} required error={errors.phone}>
          <PhoneInput value={form.phone} onChange={(v) => setF('phone', v)} />
        </Field>

        {/* A customer needs a name and a phone number. The seven identity,
            employment and income fields are underwriting detail that can be
            added later, so they must not stand between a shop and a sale. */}
        <div className="md:col-span-2">
          <button
            type="button"
            onClick={() => setShowMore((v) => !v)}
            aria-expanded={showMore}
            aria-controls="customer-optional-fields"
            className="tap-target inline-flex min-h-11 items-center gap-1.5 text-caption font-medium text-accent hover:underline"
          >
            {showMore ? <ChevronUp size={14} /> : <ChevronDown size={14} />}
            {showMore
              ? t('customers.hideOptional', 'Qo‘shimcha maʼlumotlarni yashirish')
              : t('customers.showOptional', 'Qo‘shimcha maʼlumot qo‘shish (ixtiyoriy)')}
          </button>
        </div>

        <div id="customer-optional-fields"
             hidden={!showMore}
             className={showMore ? 'contents' : undefined}>
        <Field label={t('customers.secondaryPhone')} error={errors.secondary_phone}>
          {/* (QA L-4) An explicit id: PhoneInput defaults to id="phone", so
              both fields rendered the SAME DOM id — invalid HTML, and the
              label/autofill association pointed at whichever came first. */}
          <PhoneInput id="secondary_phone" value={form.secondary_phone ?? ''}
                      onChange={(v) => setF('secondary_phone', v)} />
        </Field>

        <Field label={t('customers.passport', 'Pasport')} hint="AA1234567">
          <PassportInput
            value={joinPassport(form.passport_series, form.passport_number)}
            onChange={(v) => setForm((f) => ({ ...f, passport_series: v.slice(0, 2), passport_number: v.slice(2) }))}
          />
        </Field>

        <Field label={t('customers.jshshir')} hint="14 digit">
          <input className={inputCls} maxLength={14} value={form.jshshir ?? ''}
                 aria-label={t('customers.jshshir')}
                 placeholder="12345678901234" inputMode="numeric"
                 onChange={(e) => setF('jshshir', e.target.value.replace(/\D/g, '').slice(0, 14))} />
          {existing && (form.jshshir ?? '').length === 14 && (
            <div className="mt-2">
              <RiskBadge customerId={existing.id} />
            </div>
          )}
        </Field>
        <Field label={t('customers.birthdate')}>
          <DatePicker value={form.birthdate ?? ''} max={localToday()}
                      onChange={(iso) => setF('birthdate', iso)} />
        </Field>

        <Field label={t('customers.employmentPlace', 'Ish joyi')}>
          <input className={inputCls} value={form.employment_place ?? ''}
                 placeholder={t('customers.employmentPlaceHint', 'Tashkilot nomi') ?? ''}
                 onChange={(e) => setF('employment_place', e.target.value)} />
        </Field>
        <Field label={t('customers.monthlyIncome', 'Oylik daromad (so\'m)')}>
          <MoneyInput value={form.monthly_income ?? 0}
                      onChange={(n) => setF('monthly_income', n === 0 ? null : n)} />
        </Field>

        <div className="md:col-span-2">
          <Field label={t('customers.address')}>
            <input className={inputCls} value={form.address ?? ''}
                   onChange={(e) => setF('address', e.target.value)} />
          </Field>
        </div>

        <div className="md:col-span-2">
          <Field label={t('customers.notes')}>
            <Textarea
              value={form.notes ?? ''}
              onChange={(v) => setF('notes', v)}
              maxLength={500}
              rows={3}
              autosize
            />
          </Field>
        </div>
        </div>

        {existing && (
          <div className="md:col-span-2">
            <ChannelPrefs customerId={existing.id} />
          </div>
        )}

        {err && (
          <div className="md:col-span-2 rounded-control border border-danger/30 bg-[color:var(--c-danger-soft)] px-4 py-3 text-body text-danger">
            {err}
          </div>
        )}
      </form>
    </Modal>
  );
}

// ── InviteLinkPanel — one-time join link shown right after creating a customer ──
/**
 * What the shop does here is SEND the link, so Share and Copy are the content
 * and the URL itself is a receipt: one truncated line confirming which link was
 * made. Rendering the whole thing large gives an unbreakable ~70-character
 * string the widest say in the layout, which is what pushed this sheet past the
 * edge of a 320 px phone.
 */
function InviteLinkPanel({ url }: { url: string }) {
  const { t } = useTranslation();
  const [copied, setCopied] = useState(false);
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(url);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    } catch { /* clipboard unavailable — the URL below stays select-all */ }
  };
  return (
    <div data-testid="invite-panel"
         className="min-w-0 rounded-control border border-success/30 bg-[color:var(--c-success-soft)] p-3 sm:p-4">
      <div className="text-body font-semibold text-ink">
        {t('customers.inviteLinkTitle', { defaultValue: 'Qo‘shilish havolasi' })}
      </div>
      <p className="mt-1 text-caption text-muted">
        {t('customers.inviteLinkNote', { defaultValue: 'Bu havolani mijozga yuboring — u ochgach Telegram orqali ulanadi.' })}
      </p>

      {/* One line, truncated, still select-all for a WebView with no clipboard
          permission. `min-w-0` on the shrinking child AND `truncate` on the text
          are both required: without the former the flex item refuses to go below
          its content width and the ellipsis never engages. */}
      <p className="mt-2 min-w-0 select-all truncate font-mono text-micro text-ink-soft" title={url}>
        {url}
      </p>

      {/* Both actions keep a 44 px target and stay side by side at 320 px. */}
      <div className="mt-2.5 flex min-w-0 gap-2">
        <Button variant="outline" size="md" type="button"
                className="tap-target min-h-11 min-w-0 flex-1"
                onClick={() => void copy()}>
          {copied
            ? <><Check size={16} /> <span className="truncate">{t('common.copied', { defaultValue: 'Nusxalandi' })}</span></>
            : <><Copy size={16} /> <span className="truncate">{t('common.copy', { defaultValue: 'Nusxalash' })}</span></>}
        </Button>
        <Button variant="primary" size="md" type="button"
                className="tap-target min-h-11 min-w-0 flex-1"
                onClick={() => shareViaTelegram(url, t('customers.inviteShareText', { defaultValue: 'Muddato — bo‘lib to‘lashga qo‘shiling' }))}>
          <Send size={16} /> <span className="truncate">{t('common.share', { defaultValue: 'Ulashish' })}</span>
        </Button>
      </div>
      {/* Announced, not only shown. */}
      <span role="status" aria-live="polite" className="sr-only">
        {copied ? t('common.copied', { defaultValue: 'Nusxalandi' }) : ''}
      </span>
    </div>
  );
}

// ── ChannelPrefs — per-customer notification channel opt-in (Round 25) ──
// SMS fallback is allowed when a customer has no working Telegram link OR
// has explicitly opted in here. Opting OUT of SMS suppresses the fallback.

const CHANNELS: ChannelType[] = ['telegram', 'sms', 'email'];

function ChannelPrefs({ customerId }: { customerId: string }) {
  const { t } = useTranslation();
  const qc = useQueryClient();
  const q = useCustomerChannels(customerId);
  const [rows, setRows] = useState<CustomerChannel[]>([]);
  const [busy, setBusy] = useState(false);
  const [ok, setOk] = useState(false);
  const [saveErr, setSaveErr] = useState<string | null>(null);

  useEffect(() => {
    if (!q.data) return;
    const byChannel = new Map(q.data.map((r) => [r.channel, r]));
    setRows(CHANNELS.map((ch) => byChannel.get(ch) ?? {
      channel: ch, is_primary: false, opted_in: false, opted_in_at: null,
    }));
  }, [q.data]);

  const toggleOptIn = (ch: ChannelType) =>
    setRows((p) => p.map((r) => r.channel === ch ? { ...r, opted_in: !r.opted_in } : r));
  const setPrimary = (ch: ChannelType) =>
    setRows((p) => p.map((r) => ({ ...r, is_primary: r.channel === ch })));

  const save = async () => {
    setBusy(true); setOk(false);
    try {
      await api.put(`/tenant/customers/${customerId}/channels`, {
        channels: rows.map((r) => ({
          channel: r.channel, is_primary: r.is_primary, opted_in: r.opted_in,
        })),
      });
      qc.invalidateQueries({ queryKey: queryKeys.tenant.customerChannels(customerId) });
      setOk(true); setTimeout(() => setOk(false), 2500);
    } catch (ex) {
      // MUST catch: without it a rejected save skips the success flag and
      // surfaces nothing, so turning SMS off could silently fail to persist.
      setSaveErr(apiErrorMessage(ex));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="rounded-control border border-line bg-cream p-4">
      <div className="mb-1 text-body font-semibold">
        {t('customers.channelsTitle', 'Aloqa kanallari')}
      </div>
      <p className="mb-3 text-caption text-muted">
        {t('customers.channelsHint', 'Eslatmalar avval Telegram\'ga yuboriladi. Telegram\'i bo\'lmasa SMS zaxira sifatida ishlaydi. SMS\'ni bu yerdan o\'chirib qo\'yish mumkin.')}
      </p>
      {saveErr && (
        <div role="alert" aria-live="assertive"
             className="mb-3 rounded-icon border border-danger/30 bg-[color:var(--c-danger-soft)] px-3 py-2 text-caption text-danger">
          {saveErr}
        </div>
      )}
      {q.isLoading ? (
        <div className="text-caption text-muted">{t('common.loading')}</div>
      ) : (
        <ul className="space-y-2">
          {rows.map((r) => (
            <li key={r.channel} className="flex items-center justify-between gap-3 rounded-icon border border-line bg-surface px-3 py-2">
              <span className="text-small font-medium capitalize">
                {t(`customers.channel_${r.channel}`, r.channel)}
              </span>
              <div className="flex items-center gap-4 text-caption">
                <label className="inline-flex cursor-pointer items-center gap-1.5">
                  <input type="radio" name={`primary-${customerId}`} checked={r.is_primary}
                         onChange={() => setPrimary(r.channel)} />
                  {t('customers.channelPrimary', 'Asosiy')}
                </label>
                <label className="inline-flex items-center gap-1.5">
                  <Switch size="sm" checked={r.opted_in} onChange={() => toggleOptIn(r.channel)} />
                  {t('customers.channelOptIn', 'Yoqilgan')}
                </label>
              </div>
            </li>
          ))}
        </ul>
      )}
      <div className="mt-3 flex items-center justify-end gap-2">
        {ok && <span className="text-caption text-success">{t('tenantSettings.saved', 'Saqlandi')}</span>}
        <Button variant="outline" size="sm" onClick={save} loading={busy || q.isLoading}>
          {busy && <Loader2 size={14} className="animate-spin" />}
          {t('customers.channelsSave', 'Kanallarni saqlash')}
        </Button>
      </div>
    </div>
  );
}
