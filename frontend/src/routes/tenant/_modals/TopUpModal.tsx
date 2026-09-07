import { useState, useEffect, useRef } from 'react';
import { useTranslation } from 'react-i18next';
import { useParams } from 'react-router';
import { useQueryClient } from '@tanstack/react-query';
import { Upload } from 'lucide-react';
import { Modal, Field } from '@/components/ui/Modal';
import { Button } from '@/components/ui/Button';
import { DestinationCardRow } from '@/components/ui/DestinationCardRow';
import { MoneyInput } from '@/components/ui/FormFields';
import { useTenantTopupInfo, createTopup } from '@/lib/queries';
import { queryKeys } from '@/lib/queryKeys';
import { apiErrorMessage } from '@/lib/errors';
import { V2_SURFACES_ENABLED } from '@/routes/_scope';
import { money } from '@/lib/format';
import { confirmDialog, toast } from '@/lib/feedback';
import { focusFirstInvalid, validate, hasErrors } from '@/lib/forms';
import { openExternalLink } from '@/lib/telegram';
import { useSubmitGuard } from '@/hooks/useSubmitGuard';
import { useUnsavedGuard } from '@/hooks/useUnsavedGuard';
import { downscaleImage } from '@/lib/image';
import { RECEIPT_FILE, validateFile, fileRejectionMessage } from '@/lib/fileValidation';

interface Props { open: boolean; onClose: () => void }

type Method = 'card' | 'payme' | 'click';

/** Tenant balance top-up: pay online (Payme/Click) or transfer to a platform
 *  card and upload the receipt for manual approval. */
export function TopUpModal({ open, onClose }: Props) {
  const { t, i18n } = useTranslation();
  const { locale } = useParams();
  const lng = (locale ?? i18n.language ?? 'uz') as 'uz' | 'ru';
  const qc = useQueryClient();
  const info = useTenantTopupInfo();
  const d = info.data;

  const [amount, setAmount] = useState(0);
  const [method, setMethod] = useState<Method>('card');
  const [receipt, setReceipt] = useState<File | null>(null);
  const [cardId, setCardId] = useState<string>('');
  // (§4) Client-side validation errors shown inline under each field.
  const [errors, setErrors] = useState<Record<string, string>>({});
  // (LX-04) The downscale is main-thread work on a large file and blocks the
  // modal. Without feedback the modal appears frozen. Separate from `busy`
  // (which covers the submit) so the user sees when the receipt is preparing
  // vs. when the top-up request is in flight.
  const [preparing, setPreparing] = useState(false);
  const { pending: busy, run: runSubmit } = useSubmitGuard();
  // (MUDD-133) The route this modal lives on outlives any single open/close of
  // the modal, so a real unmount (navigating away mid-request) is possible even
  // though open/close alone never unmounts this component — only `<Modal>`'s
  // rendered children toggle. Guards state writes that resolve after that.
  const mountedRef = useRef(true);
  useEffect(() => {
    mountedRef.current = true;
    return () => { mountedRef.current = false; };
  }, []);
  // (#547) Prompt before discarding an entered amount/receipt/method on
  // Esc/backdrop/Cancel. cardId is excluded — it auto-defaults to the first card
  // and isn't user-entered work. Successful submit closes via onClose directly.
  const requestClose = useUnsavedGuard(open, JSON.stringify({ amount, method, receipt: receipt?.name ?? null }), onClose);
  // (MUDD-142) This component never unmounts when the modal closes — only
  // `<Modal>`'s rendered children do (`open` just toggles a prop) — so without
  // this the draft survives a close and a reopened "fresh" top-up shows the
  // previous amount/receipt/method/cardId/errors. Ties the reset to every path
  // that can make `open` false (confirmed discard, backdrop, Esc, Cancel, and
  // successful submit alike), rather than duplicating a reset call at each one.
  useEffect(() => {
    if (!open) { setAmount(0); setReceipt(null); setMethod('card'); setCardId(''); setErrors({}); }
  }, [open]);

  const amt = amount;
  const terms = lng === 'ru' ? d?.terms?.ru : d?.terms?.uz;
  // The PLATFORM wallet's currency, stated by the endpoint — not
  // `useCurrency()`, which is the shop's. The destination cards are filtered
  // server-side against this same value.
  const currency = d?.currency ?? 'UZS';
  // (MUDD-133) `disabled` must always carry a stated reason — never a silent
  // stand-in for "loading". Distinct from `busy`/`preparing`, which drive the
  // button's `loading` prop (spinner + aria-busy) and must reflect an actual
  // in-flight request, never "the form isn't filled in yet".
  const sendDisabledReason = !amt
    ? t('topup.enterAmount', 'Summani kiriting')
    : method === 'card' && !cardId
      ? t('topup.cardRequired', 'Kartani tanlang')
      : method === 'card' && !receipt
        ? t('topup.receiptRequired', 'Chek rasmini yuklang')
        : undefined;

  /**
   * Validate, then downscale only if it is an image (#9).
   *
   * A bank transfer receipt is as often a PDF from a banking app as a photo, and
   * this used to accept images only — so the most common artefact a person has
   * to hand was rejected with "images only" and no way forward. The allowed set
   * now comes from the shared `RECEIPT_FILE`, which is the same definition the
   * picker's `accept` and the backend's allowlist are built from.
   *
   * Downscaling is guarded on the type: running the image pipeline over a PDF
   * would either corrupt it or silently produce a blank raster.
   *
   * (LX-04) The downscale blocks the main thread on large files. An indicator
   * is now shown so the owner knows the modal is not frozen.
   */
  const onReceipt = async (f: File | null) => {
    if (!f) { setReceipt(null); return; }
    const rejection = validateFile(f, RECEIPT_FILE);
    if (rejection) {
      toast.error(fileRejectionMessage(rejection));
      // (MUDD-142) A rejected replacement must not leave the previous receipt
      // armed — the owner just picked a new file and believes it replaced the
      // old one; silently submitting the stale receipt would be a wrong-receipt
      // top-up against real money.
      setReceipt(null);
      return;
    }

    setPreparing(true);
    let prepared = f;
    try {
      prepared = f.type.startsWith('image/') ? await downscaleImage(f) : f;
    } catch {
      // (MUDD-142) downscaleImage's rejection used to be unhandled: no user
      // feedback, and the owner was left believing their receipt was attached.
      if (mountedRef.current) {
        toast.error(t('topup.receiptPrepFailed', "Chekni tayyorlab bo'lmadi. Boshqa fayl tanlang."));
        setReceipt(null);
      }
      return;
    } finally {
      if (mountedRef.current) setPreparing(false);
    }
    if (!mountedRef.current) return;
    if (prepared.size > RECEIPT_FILE.maxBytes) {
      toast.error(fileRejectionMessage({ key: 'file.errTooLarge', params: { max: RECEIPT_FILE.maxLabel } }));
      setReceipt(null);
      return;
    }
    setReceipt(prepared);
    setErrors((p) => (p.receipt ? { ...p, receipt: '' } : p));
  };

  /**
   * Select a destination card and put its number on the clipboard (#9).
   *
   * Copying is the whole reason this screen exists: the next thing the owner
   * does is leave for their banking app and paste the number. Making them
   * select and then find a separate copy control is a step that only exists
   * because we didn't do it for them.
   *
   * The copy is best-effort and never blocks selection. Clipboard access is
   * denied outright in some Telegram WebViews, and a card that could not be
   * copied is still a card they chose — the explicit copy button on the tile
   * remains for exactly that case, so a silent failure is recoverable.
   */
  const selectCard = async (c: { id: string; number?: string }) => {
    setCardId(c.id);
    setErrors((p) => (p.cardId ? { ...p, cardId: '' } : p));
    const digits = (c.number ?? '').replace(/\D/g, '');
    if (!digits) return;
    try {
      await navigator.clipboard.writeText(digits);
      toast.success(t('topup.cardCopied', 'Karta raqami nusxalandi'));
    } catch { /* clipboard blocked — the tile's copy button still works */ }
  };

  // Default the destination card to the first available one once cards load.
  // Deliberately NOT via `selectCard`: an automatic default is not a choice the
  // owner made, and silently overwriting their clipboard on open would destroy
  // whatever they had copied to bring here.
  useEffect(() => {
    const cards = d?.cards ?? [];
    // A selection the server no longer offers is dropped: the list is
    // filtered by settlement currency, so a stale id is a card that cannot take
    // this transfer, and submit would reject it after the owner had paid.
    if (cardId && !cards.some((c) => c.id === cardId)) { setCardId(''); return; }
    if (!cardId && cards.length > 0) setCardId(cards[0].id);
  }, [d?.cards, cardId]);

  // Online rails are deferred — a shop must not be offered a top-up route it
  // cannot complete. Manual card transfer is the retained path.
  const methods: { key: Method; label: string; show: boolean }[] = [
    { key: 'card', label: t('topup.card', 'Karta o‘tkazmasi'), show: true },
    { key: 'payme', label: 'Payme', show: V2_SURFACES_ENABLED && !!d?.providers?.payme },
    { key: 'click', label: 'Click', show: V2_SURFACES_ENABLED && !!d?.providers?.click },
  ];

  const submit = async () => {
    // (§4) Validate inline — block submit, keep entered values, surface each
    // error under its own field. Amount must be > 0; a card transfer needs a
    // destination card + receipt photo.
    const errs: Record<string, string> = {
      amount: validate.positive(amt, '0 dan katta summa kiriting'),
    };
    if (method === 'card' && !cardId) errs.cardId = t('topup.cardRequired', 'Kartani tanlang');
    if (method === 'card' && !receipt) errs.receipt = t('topup.receiptRequired', 'Chek rasmini yuklang');
    setErrors(errs);
    if (hasErrors(errs)) { focusFirstInvalid(); return; }
    const card = (d?.cards ?? []).find((c) => c.id === cardId);
    if (!(await confirmDialog({
      title: t('topup.confirmTitle', 'Balansni to‘ldirishni tasdiqlang'),
      message: t('topup.confirmBody', {
        amount: `${money(amt)} ${currency}`,
        card: card ? `${card.bank || ''} ••${(card.number || '').replace(/\D/g, '').slice(-4)}`.trim() : '—',
        defaultValue: '{{amount}} · {{card}} — so‘rov yuboriladi va operator tasdiqlagach balansga qo‘shiladi.',
      }),
      confirmLabel: t('topup.send', 'Yuborish'),
    }))) return;
    // Synchronous one-shot guard — a double-tap can't submit two top-ups.
    void runSubmit(async () => {
      try {
        const r = await createTopup({ amount: amt, method, receipt: receipt ?? undefined, card_id: method === 'card' ? cardId : undefined });
        await qc.invalidateQueries({ queryKey: queryKeys.tenant.topups() });
        // Also invalidate topup-info in case any related data changed (e.g., balance, providers).
        await qc.invalidateQueries({ queryKey: queryKeys.tenant.topupInfo() });
        if (!mountedRef.current) return;
        if (r.checkout_url) {
          openExternalLink(r.checkout_url);
          toast.success(t('topup.redirecting', 'To‘lov sahifasi ochildi'));
        } else {
          toast.success(t('topup.submitted', 'So‘rov yuborildi — tasdiqlanishini kuting'));
        }
        // Draft reset happens via the `open`-close effect above — one path
        // for every way this modal closes, not a copy kept in sync here.
        onClose();
      } catch (ex) {
        if (!mountedRef.current) return;
        const det = (ex as { response?: { data?: { detail?: string } } }).response?.data?.detail;
        toast.error(det === 'provider_not_configured'
          ? t('topup.providerOff', 'Bu to‘lov usuli hali sozlanmagan.')
          : apiErrorMessage(ex));
      }
    });
  };

  return (
    <Modal open={open} onClose={requestClose}
           title={t('topup.title', 'Balansni to‘ldirish')}
           subtitle={d ? `${t('topup.current', 'Joriy balans')}: ${money(d.balance)} ${currency}` : undefined}
           size="md"
           footer={
             <>
               <Button variant="outline" size="md" type="button" onClick={requestClose}>{t('common.cancel', 'Bekor qilish')}</Button>
               <Button variant="primary" size="md" type="button"
                       loading={busy || preparing}
                       disabled={!!sendDisabledReason}
                       title={sendDisabledReason}
                       onClick={() => void submit()}>
                 {method === 'card' ? t('topup.send', 'Yuborish') : t('topup.pay', 'To‘lash')}
                 {/* (#545) Echo the amount at the point of commitment so the owner
                     confirms the value before paying (reduces wrong-amount top-ups). */}
                 {amt > 0 && ` · ${money(amt)} ${currency}`}
               </Button>
             </>
           }>
      <div className="space-y-4">
        <Field label={t('topup.amount', 'Summa')} hint={currency} error={errors.amount}>
          <MoneyInput value={amount} onChange={(n) => { setAmount(n); setErrors((p) => (p.amount ? { ...p, amount: '' } : p)); }} />
        </Field>

        <div>
          <div className="mb-1.5 text-micro uppercase tracking-wider text-muted">{t('topup.method', 'To‘lov usuli')}</div>
          <div className="grid grid-cols-3 gap-2">
            {methods.filter((m) => m.show).map((m) => (
              <button key={m.key} type="button"
                      onClick={() => { setMethod(m.key); setErrors((p) => ({ ...p, cardId: '', receipt: '' })); }}
                      className={`h-10 rounded-control border text-body font-medium transition ${
                        method === m.key ? 'border-ink bg-ink text-white' : 'border-line bg-cream text-ink-soft hover:border-ink/30'
                      }`}>{m.label}</button>
            ))}
          </div>
        </div>

        {method === 'card' && (
          <div className="space-y-3">
            {(d?.cards?.length ?? 0) > 0 ? (
              <div>
                <div className="mb-2 text-micro uppercase tracking-wider text-muted">{t('topup.transferTo', 'Quyidagi kartaga o‘tkazing')}</div>
                {/* Compact rows, not card mockups. Each row is its own
                    radio and carries its own copy control — never nested
                    interactive elements, which would make the number
                    uncopyable on the one screen where it has to be copied. */}
                <div role="radiogroup"
                     aria-label={t('topup.transferTo', 'Quyidagi kartaga o‘tkazing')}
                     className="grid gap-2">
                  {d!.cards.map((c) => (
                    <DestinationCardRow
                      key={c.id}
                      card={c}
                      currency={c.settlement_currency ?? currency}
                      selected={cardId === c.id}
                      onSelect={() => { void selectCard(c); }}
                    />
                  ))}
                </div>
                {errors.cardId && <span role="alert" className="mt-1.5 block text-micro font-medium text-danger">{errors.cardId}</span>}
              </div>
            ) : (
              // "configured but none can take this currency" and "none
              // configured" are different problems with different owners. Both
              // block: a top-up with nowhere to send the money is not offerable.
              <p role="alert" className="rounded-control border border-line bg-cream px-3 py-2 text-caption text-muted">
                {d?.cards_withheld
                  ? t('topup.noCompatibleCards', {
                      currency,
                      defaultValue: '{{currency}} qabul qila oladigan karta yo‘q. @MuddatoSupport bilan bog‘laning.',
                    })
                  : t('topup.noCards', 'Hali qabul qiluvchi karta sozlanmagan. @MuddatoSupport bilan bog‘laning.')}
              </p>
            )}

            <Field label={t('topup.receipt', 'Chek rasmi')} hint={t('topup.receiptHint', 'O‘tkazma tasdig‘i')} error={errors.receipt}>
              <label className="flex cursor-pointer items-center gap-2 rounded-control border border-dashed border-line px-3 py-2.5 text-body text-ink-soft hover:border-ink/30">
                <Upload size={16} />
                <span className="truncate">{receipt ? receipt.name : t('topup.choose', 'Rasm tanlash')}</span>
                <input type="file" accept={RECEIPT_FILE.accept} className="hidden" disabled={preparing}
                       onChange={(e) => void onReceipt(e.target.files?.[0] ?? null)} />
              </label>
            </Field>
          </div>
        )}

        {method !== 'card' && (
          <p className="rounded-control border border-line bg-cream px-3 py-2 text-caption text-muted">
            {t('topup.onlineNote', 'Tugmani bossangiz to‘lov sahifasi ochiladi. To‘lov tasdiqlangach balans avtomatik to‘ldiriladi.')}
          </p>
        )}

        {terms && (
          <details className="rounded-control border border-line px-3 py-2 text-caption">
            <summary className="cursor-pointer text-muted">{t('topup.terms', 'To‘lov shartlari')}</summary>
            <p className="mt-2 whitespace-pre-wrap text-ink-soft">{terms}</p>
          </details>
        )}
      </div>
    </Modal>
  );
}
