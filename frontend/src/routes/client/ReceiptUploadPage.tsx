import { useEffect, useState, useRef } from 'react';
import { useTranslation } from 'react-i18next';
import { useNavigate, useParams, useSearchParams } from 'react-router';
import {
  AlertTriangle, ArrowLeft, Camera, CheckCircle2, FileText, Loader2, Upload, X,
} from 'lucide-react';
import { BackChip } from '@/components/mobile/primitives';
import { Button } from '@/components/ui/Button';
import { Skeleton } from '@/components/ui/Skeleton';
import { ReceiptImageFrame } from '@/components/ui/ReceiptImageFrame';
import { useClosingConfirmation } from '@/lib/telegram/closing';
import { useMyInstallment } from '@/lib/queries';
import { api } from '@/lib/api';
import { CardInstructions } from '@/components/ui/CardInstructions';
import { apiErrorMessage } from '@/lib/errors';
import { money } from '@/lib/format';
import { formatMoney, MONEY_MAX } from '@/components/ui/FormFields';
import { useSubmitGuard } from '@/hooks/useSubmitGuard';
import { useMainButton } from '@/lib/telegram/buttons';
import { downscaleImage, IMAGE_MAX_BYTES, IMAGE_MAX_MB } from '@/lib/image';
import { RECEIPT_FILE, fileRejectionMessage, validateFile } from '@/lib/fileValidation';

/** /me/installments/:id/receipt
 *
 *  Cash flow: customer pays at the shop in cash, snaps a photo of the
 *  receipt the tenant prints, uploads it here. We POST multipart to
 *  /me/payments/upload-receipt; the tenant gets a Telegram notification
 *  with an "Approve" link.
 */
export default function ClientReceiptUploadPage() {
  const { id } = useParams<{ id: string }>();
  const [params] = useSearchParams();
  const { t, i18n } = useTranslation();
  const locale = (i18n.language?.startsWith('ru') ? 'ru' : 'uz') as 'uz' | 'ru';
  const nav = useNavigate();
  const inst = useMyInstallment(id);

  // (#369) The tenant's card/transfer details — fetched here too (not passed
  // via URL) so a direct Telegram deep link to this page still shows where to
  // pay before asking for the receipt photo. Non-blocking enhancement.
  const [cardInstructions, setCardInstructions] = useState<string | null>(null);
  useEffect(() => {
    if (!id) return;
    api.get<{ card_instructions: string | null }>(`/me/installments/${id}/payment-methods`)
      .then((r) => setCardInstructions(r.data.card_instructions))
      .catch(() => { /* panel is optional — upload still works without it */ });
  }, [id]);

  const [amount, setAmount] = useState<string>(params.get('amount') ?? '');
  // Cash is paid at the counter, so the photo is optional on that rail only.
  // Every other manual method paid somewhere we cannot see, and the image is
  // the reviewer's only evidence — still required.
  const method = params.get('method') ?? 'cash';
  const proofOptional = method === 'cash';
  const [file, setFile] = useState<File | null>(null);
  const [previewUrl, setPreviewUrl] = useState<string | null>(null);
  // Confirm before a swipe-close drops a chosen-but-unsent receipt photo.
  useClosingConfirmation(!!file);
  const { pending: uploading, run: runUpload } = useSubmitGuard();
  const [preparing, setPreparing] = useState(false);
  const [uploadProgress, setUploadProgress] = useState(0);
  const [done, setDone] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  // Revoke the last object URL when it's replaced or on unmount — the preview
  // blob otherwise leaks for the page's lifetime.
  useEffect(() => () => { if (previewUrl) URL.revokeObjectURL(previewUrl); }, [previewUrl]);

  // PDFs are a valid receipt here in addition to raster images.
  const onFile = async (f: File | null) => {
    setErr(null);
    if (!f) { setFile(null); setPreviewUrl(null); return; }
    // The SHARED rule, not a local one. This page carried its own list that
    // included HEIC — which the server cannot decode — so an iPhone photo was
    // accepted here, uploaded over mobile data, and refused at the end.
    //
    // `accept` on the input narrows the dialog; every desktop picker has an
    // "All files" escape and Telegram's in-app picker ignores the attribute
    // entirely. This is the gate that actually runs, and the server repeats it.
    const rejection = validateFile(f, RECEIPT_FILE);
    if (rejection) {
      setErr(fileRejectionMessage(rejection));
      // Nothing half-selected is left behind: a previously picked file must not
      // survive a rejected second choice.
      setFile(null);
      setPreviewUrl(null);
      return;
    }
    // Downscale large phone photos client-side before enforcing the cap, so an
    // 8–12 MB camera JPEG sails through; PDFs/HEIC pass through untouched.
    setPreparing(true);
    let prepared = f;
    try {
      prepared = await downscaleImage(f);
    } finally {
      setPreparing(false);
    }
    if (prepared.size > IMAGE_MAX_BYTES) {
      setErr(t('client.receiptTooLarge', { mb: IMAGE_MAX_MB, defaultValue: `Rasm ${IMAGE_MAX_MB} MB dan kichik bo'lishi kerak` }));
      return;
    }
    setFile(prepared);
    setPreviewUrl(URL.createObjectURL(prepared));
  };

  const submit = () => {
    if (!id || !amount) return;
    if (!file && !proofOptional) return;
    setErr(null);
    // Synchronous one-shot guard — a double-tap can't upload two receipts.
    void runUpload(async () => {
      try {
        const fd = new FormData();
        fd.append('installment_id', id);
        fd.append('amount', amount);
        if (file) fd.append('file', file);
        fd.append('method', method);
        await api.post('/me/payments/upload-receipt', fd, {
          headers: { 'Content-Type': 'multipart/form-data' },
          onUploadProgress: (progressEvent) => {
            const percentComplete = Math.round(
              (progressEvent.loaded / (progressEvent.total || 1)) * 100
            );
            setUploadProgress(percentComplete);
          },
        });
        setDone(true);
      } catch (e) {
        setErr(apiErrorMessage(e));
      }
    });
  };

  // Inside Telegram the native MainButton is the primary action on every
  // customer screen, so pay → receipt keeps one CTA in one place. Declared
  // before the early-returns below because hooks must run on every render; in a
  // browser it is inert and the in-page button is shown instead.
  const canSubmit = (!!file || proofOptional)
    && !!amount && Number(amount) > 0 && !uploading && !preparing;
  const { isNative: nativeSubmit } = useMainButton({
    text: t('client.sendReceipt', 'Chekni yuborish'),
    visible: !done,
    enabled: canSubmit,
    loading: uploading || preparing,
    onClick: submit,
  });

  if (done) {
    return (
      <div className="mx-auto w-full max-w-panel">
        <div className="rounded-card border border-success/30 bg-success/10 p-6 text-center">
          <CheckCircle2 className="mx-auto text-success" size={40} />
          <div className="mt-3 text-subsection font-semibold">
            {t('client.receiptSent', 'Chek yuborildi')}
          </div>
          <p className="mt-1 text-caption text-ink-soft">
            {t('client.receiptPendingApproval',
               'Do\'kon egasi chekni tekshirib tasdiqlaydi. Tasdiqlangach, qarz qoldiq yangilanadi.')}
          </p>
          <Button variant="inverse" size="sm" className="mt-4" onClick={() => nav(`/${locale}/me/installments/${id}`)}>
            {t('client.back', 'Orqaga')}
          </Button>
        </div>
      </div>
    );
  }

  if (inst.isLoading) {
    // (#365 pattern, matched to PayPage/PayoffPage) Page-shaped skeleton: back
    // chip, header, amount card, file-picker dropzone — not a bare spinner.
    return (
      <div className="mx-auto w-full max-w-panel" role="status" aria-busy="true">
        <Skeleton className="h-9 w-24 rounded-icon" />
        <Skeleton className="mt-3 h-12 w-2/3" />
        <Skeleton className="mt-4 h-24 w-full rounded-card" />
        <Skeleton className="mt-4 h-44 w-full rounded-card" />
      </div>
    );
  }
  const i = inst.data;
  // (#410) A bad/stale :id or a failed fetch used to `return null` — a blank
  // white trap state inside the Mini-App with no way back. Match the sibling
  // InstallmentDetailPage error card instead.
  if (inst.isError || !i) {
    return (
      <div className="mx-auto w-full max-w-panel py-12 text-center">
        <AlertTriangle className="mx-auto text-danger" size={28} />
        <div className="mt-2 text-body">{t('client.notFound', 'Kredit topilmadi')}</div>
        <button
          onClick={() => nav(`/${locale}/me`)}
          className="mt-4 inline-flex items-center gap-1 text-small text-accent hover:underline"
        >
          <ArrowLeft size={14} /> {t('client.back', 'Orqaga')}
        </button>
      </div>
    );
  }

  return (
    <div className="mx-auto w-full max-w-panel">
      <BackChip onClick={() => nav(-1)} label={t('client.back', 'Orqaga')} />

      <header className="mb-4">
        <div className="text-micro uppercase tracking-wider text-muted">{i.tenant.name}</div>
        {/* Cash is reported, not evidenced: the shop being asked to approve it
            is the shop that took the money, so the screen asks for the amount
            and offers the photo rather than demanding it. */}
        <h1 className="text-section font-semibold">
          {proofOptional
            ? t('client.uploadReceiptCashTitle', "To'lov haqida xabar berish")
            : t('client.uploadReceiptTitle', 'Chek foto yuborish')}
        </h1>
        <p className="mt-1 text-caption text-muted">
          {proofOptional
            ? t('client.uploadReceiptCashHint',
                "Do'konda naqd to'lagan bo'lsangiz, summani yozing. Chek foto ixtiyoriy.")
            : t('client.uploadReceiptHint',
                'Do\'konda naqd to\'laganingizda olingan chek rasmini yuboring. Do\'kon egasi tasdiqlagach, qarz qoldiq yangilanadi.')}
        </p>
      </header>

      {/* (#369) Where/how to pay — before the proof-of-payment ask. */}
      {cardInstructions && (
        <div className="mb-4">
          <CardInstructions text={cardInstructions} />
        </div>
      )}

      {/* Amount */}
      <div className="mb-4 rounded-card border border-line bg-surface p-4">
        <label htmlFor="receipt-amount" className="block text-micro uppercase tracking-wider text-muted">{t('client.amount', 'Summa')}</label>
        <div className="mt-1 flex items-baseline gap-2">
          <input
            id="receipt-amount"
            aria-label={t('client.amount', 'Summa')}
            inputMode="numeric"
            value={amount ? formatMoney(Number(amount)) : ''}
            onChange={(e) => {
              const d = e.target.value.replace(/\D/g, '').slice(0, 12);
              setAmount(d ? String(Math.min(Number(d), (Number(i.remaining) || MONEY_MAX), MONEY_MAX)) : d);
            }}
            placeholder={money(i.monthly_amount)}
            // `leading-none` with no padding collapses the hit box to the text
            // box (20 px tall). `min-h-11` restores a 44 px target on every
            // pointer type — `tap-target` alone only applies to coarse ones.
            className="tap-target min-h-11 w-full bg-transparent text-title font-semibold tabular-nums leading-none outline-none"
          />
          <span className="text-body">{i.currency}</span>
        </div>
        <button type="button" onClick={() => setAmount(String(i.monthly_amount))}
                className="mt-2 inline-flex items-center justify-center tap-target min-h-11 rounded-pill bg-cream px-3 py-1 text-caption font-medium text-ink-soft hover:bg-ink/5">
          {t('client.amountMonthly', 'Oylik')}: {money(i.monthly_amount)}
        </button>
      </div>

      {/* File picker */}
      {!previewUrl ? (
        <div>
          <input
            ref={inputRef}
            type="file"
            accept={RECEIPT_FILE.accept}
            onChange={(e) => onFile(e.target.files?.[0] ?? null)}
            className="hidden"
          />
          <button
            onClick={() => inputRef.current?.click()}
            disabled={preparing}
            className="flex w-full flex-col items-center justify-center gap-2 rounded-card border-2 border-dashed border-line bg-surface px-4 py-12 text-body disabled:opacity-60"
          >
            {preparing ? <Loader2 size={28} className="animate-spin text-accent" /> : <Camera size={28} className="text-accent" />}
            <span className="font-medium">
              {preparing
                ? t('client.receiptPreparing', 'Tayyorlanmoqda…')
                : proofOptional
                  ? t('client.receiptOptional', 'Chek foto (ixtiyoriy)')
                  : t('client.tapToCapture', 'Chek rasmini olish')}
            </span>
            <span className="text-micro text-muted">
              {t('client.receiptFormatHint', { mb: RECEIPT_FILE.maxLabel, defaultValue: `JPEG / PNG / WebP / PDF · ${RECEIPT_FILE.maxLabel} gacha` })}
            </span>
          </button>
        </div>
      ) : (
        <div className="relative">
          {file?.type === 'application/pdf' ? (
            // Nothing to decode: a filename row is its own fixed height, so it
            // never moved anything and does not need a reserved box.
            <div className="flex items-center gap-3 overflow-hidden rounded-card border border-line bg-cream px-4 py-6">
              <FileText size={28} className="text-accent" />
              <span className="truncate text-small font-medium">{file.name}</span>
            </div>
          ) : (
            // (LX-07) The chosen photo used to be an uncapped `<img>`: the Send
            // button below jumped the moment it decoded. Same reserved box as
            // the reviewer's surfaces.
            <ReceiptImageFrame
              src={previewUrl}
              // Nothing beside an image preview names the file — the PDF branch
              // shows `file.name`, this one shows only the picture — so `alt=""`
              // would leave a screen-reader user with no confirmation of what is
              // attached.
              alt={t('client.receiptPreviewAlt', 'Tanlangan chek rasmi')}
              // A local object URL decodes in a frame or two. Announcing that is
              // noise; a failure is still announced.
              announceLoading={false}
            />
          )}
          <button
            onClick={() => onFile(null)}
            aria-label={t('client.removePhoto', 'Rasmni olib tashlash')}
            className="tap-target absolute right-2 top-2 grid h-11 w-11 place-items-center rounded-pill bg-white/90 text-ink shadow"
          >
            <X size={16} />
          </button>
        </div>
      )}

      {err && (
        <div className="mt-3 flex items-center gap-2 rounded-control border border-danger/30 bg-[color:var(--c-danger-soft)] px-3 py-2 text-caption text-danger">
          <AlertTriangle size={14} /> {err}
        </div>
      )}

      {uploading && uploadProgress > 0 && uploadProgress < 100 && (
        <div className="mt-3 rounded-control bg-accent/10 px-3 py-2 text-caption text-accent">
          {t('receipt.uploading', `Yuklanyapti: ${uploadProgress}%`, { percent: uploadProgress })}
        </div>
      )}

      {!nativeSubmit && (
        <Button
          variant="inverse"
          size="lg"
          block
          className="mt-6"
          disabled={!canSubmit}
          onClick={submit}
        >
          {uploading ? <Loader2 size={14} className="animate-spin" /> : <Upload size={14} />}
          {t('client.sendReceipt', 'Chekni yuborish')}
        </Button>
      )}
    </div>
  );
}
