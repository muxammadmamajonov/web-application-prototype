import { useCallback, useEffect, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Camera, Loader2, ScanLine } from 'lucide-react';
import { Modal } from '@/components/ui/Modal';
import { Button } from '@/components/ui/Button';
import {
  blockerFromError, cameraAvailable, createDecoder,
  type Decoder, type ScanBlocker,
} from '@/lib/barcode';

/**
 * Point the camera at a box and read the code off it.
 *
 * OPENING ONCE OPENS THE CAMERA ONCE. The lifecycle is keyed to `open` alone.
 * Callers pass inline arrow functions — `onResult={(v) => setImei(v)}` — which
 * are a new identity on every parent render, so a lifecycle that depended on
 * them tore down the live stream and re-ran `getUserMedia` whenever anything
 * else on the wizard changed. On a phone that is a visible black flash, a
 * second permission prompt on some browsers, and a leaked decoder. The
 * callbacks are therefore held in refs: always current when called, never a
 * reason to restart.
 *
 * Resolves EXACTLY ONCE. A decoder run on an animation frame will happily read
 * the same barcode again while the sheet animates away, and a second result
 * after the caller has moved on overwrites a field the person is already
 * editing — so the first hit latches and every later frame is ignored.
 *
 * Everything decodes in this tab; no frame is uploaded. Every failure path ends
 * in localized guidance plus the manual-entry escape, because a scanner that
 * cannot start must not become a dead end in front of a customer.
 */
export function BarcodeScanner({ open, onClose, onResult, title }: {
  open: boolean;
  onClose: () => void;
  /** Called at most once per open, with the raw decoded text. */
  onResult: (value: string) => void;
  title?: string;
}) {
  const { t } = useTranslation();
  const videoRef = useRef<HTMLVideoElement>(null);
  const streamRef = useRef<MediaStream | null>(null);
  const decoderRef = useRef<Decoder | null>(null);
  const rafRef = useRef<number | null>(null);
  /** Latched on the first hit — the guard against a second, stale result. */
  const settledRef = useRef(false);
  const [blocker, setBlocker] = useState<ScanBlocker | null>(null);
  const [starting, setStarting] = useState(false);

  // Always the latest callbacks, never a lifecycle dependency.
  const onResultRef = useRef(onResult);
  onResultRef.current = onResult;
  const onCloseRef = useRef(onClose);
  onCloseRef.current = onClose;

  /** Release the camera, the decoder and the frame loop. Safe to call twice. */
  const stop = useCallback(() => {
    if (rafRef.current !== null) cancelAnimationFrame(rafRef.current);
    rafRef.current = null;
    streamRef.current?.getTracks().forEach((tr) => tr.stop());
    streamRef.current = null;
    decoderRef.current?.close();
    decoderRef.current = null;
  }, []);

  useEffect(() => {
    if (!open) return;
    settledRef.current = false;
    setBlocker(null);

    if (!cameraAvailable()) { setBlocker('camera_unsupported'); return; }

    let cancelled = false;
    setStarting(true);

    const finish = (value: string) => {
      if (settledRef.current || cancelled) return;
      settledRef.current = true;
      stop();
      onResultRef.current(value);
      onCloseRef.current();
    };

    (async () => {
      const decoder = await createDecoder();
      // Closed while the decoder was being built: release it rather than
      // parking it in a ref nothing will clean up.
      if (cancelled) { decoder.close(); return; }
      if (decoder.kind === 'none') { setBlocker('decoder_unsupported'); setStarting(false); return; }
      decoderRef.current = decoder;

      try {
        // The rear camera is the one pointed at the box.
        const stream = await navigator.mediaDevices.getUserMedia({
          video: { facingMode: { ideal: 'environment' } }, audio: false,
        });
        // Closed while permission was pending: the tracks still opened, and a
        // camera nobody stops keeps the indicator light on.
        if (cancelled) { stream.getTracks().forEach((tr) => tr.stop()); return; }
        streamRef.current = stream;
        const video = videoRef.current;
        if (!video) return;
        video.srcObject = stream;
        await video.play().catch(() => undefined);
        if (cancelled) return;
        setStarting(false);

        const tick = async () => {
          if (settledRef.current || cancelled) return;
          const found = await decoderRef.current?.detect(video);
          if (settledRef.current || cancelled) return;
          if (found) { finish(found); return; }
          rafRef.current = requestAnimationFrame(() => void tick());
        };
        rafRef.current = requestAnimationFrame(() => void tick());
      } catch (err) {
        if (!cancelled) { setBlocker(blockerFromError(err)); setStarting(false); }
      }
    })();

    // Covers unmount, close, AND Strict Mode's deliberate double-invoke: the
    // first pass is fully torn down before the second one opens anything.
    return () => { cancelled = true; setStarting(false); stop(); };
  }, [open, stop]);

  const guidance: Record<ScanBlocker, string> = {
    camera_unsupported: t('scan.errUnsupported', 'Bu brauzer kamerani ochа olmaydi. Raqamni qo‘lda kiriting.'),
    camera_denied: t('scan.errDenied', 'Kameraga ruxsat berilmadi. Brauzer sozlamalaridan ruxsat bering yoki raqamni qo‘lda kiriting.'),
    camera_unavailable: t('scan.errUnavailable', 'Kamera topilmadi yoki band. Boshqa ilovani yoping yoki raqamni qo‘lda kiriting.'),
    decoder_unsupported: t('scan.errDecoder', 'Bu brauzer shtrix-kodni o‘qiy olmaydi. Raqamni qo‘lda kiriting.'),
  };

  return (
    <Modal open={open} onClose={onClose} size="sm"
           title={title ?? t('scan.title', 'Shtrix-kodni skanerlash')}
           footer={<Button variant="outline" size="md" onClick={onClose}>{t('common.cancel', 'Bekor qilish')}</Button>}>
      {blocker ? (
        <div role="alert" className="rounded-control border border-line bg-cream px-3 py-4 text-center">
          <Camera size={24} className="mx-auto mb-2 text-muted" aria-hidden="true" />
          <p className="text-body text-ink-soft">{guidance[blocker]}</p>
        </div>
      ) : (
        <div className="space-y-3">
          <div className="relative overflow-hidden rounded-card bg-ink/90"
               style={{ aspectRatio: '4 / 3' }}>
            <video ref={videoRef} playsInline muted
                   aria-label={t('scan.viewfinder', 'Kamera ko‘rinishi')}
                   className="h-full w-full object-cover" />
            {/* A framing guide: a linear barcode is read across its width. */}
            <div aria-hidden="true"
                 className="pointer-events-none absolute inset-x-6 top-1/2 h-24 -translate-y-1/2 rounded-control border-2 border-white/70" />
            {starting && (
              <div className="absolute inset-0 grid place-items-center text-white">
                <Loader2 size={20} className="animate-spin" aria-hidden="true" />
              </div>
            )}
          </div>
          <p role="status" aria-live="polite" className="flex items-center justify-center gap-1.5 text-caption text-muted">
            <ScanLine size={14} aria-hidden="true" />
            {starting
              ? t('scan.starting', 'Kamera ochilmoqda…')
              : t('scan.hint', 'Kodni ramka ichiga joylashtiring')}
          </p>
        </div>
      )}
    </Modal>
  );
}
