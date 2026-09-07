import { useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { ImagePlus, Loader2, X } from 'lucide-react';
import { cn } from '@/lib/cn';
import { inputCls } from './Modal';
import { api } from '@/lib/api';
import { apiErrorMessage } from '@/lib/errors';
import { toast } from '@/lib/feedback';
import { downscaleImage, isAllowedImage, IMAGE_MAX_BYTES, IMAGE_MAX_MB } from '@/lib/image';

/** URL input + "Upload" button. Upload posts the file to the backend, which
 *  stores it on archive.org and returns a permanent public URL. You can also
 *  just paste any URL directly. */
export function ImageUploadField({
  value, onChange, placeholder = 'https://…', round, endpoint = '/catalog/upload-image',
}: {
  value: string;
  onChange: (url: string) => void;
  placeholder?: string;
  /** Round preview (brand logos) vs square (product images). */
  round?: boolean;
  /** Upload endpoint — '/catalog/upload-image' (platform) or '/tenant/upload-image'. */
  endpoint?: string;
}) {
  const { t } = useTranslation();
  const fileRef = useRef<HTMLInputElement>(null);
  const [busy, setBusy] = useState(false);

  const pick = () => fileRef.current?.click();

  // MIME allowlist mirrors the `accept` attribute below — SVG/GIF are valid for
  // brand/product art (passed through), raster photos are downscaled first.
  const ALLOWED = ['image/png', 'image/jpeg', 'image/webp', 'image/svg+xml', 'image/gif'];

  const onFile = async (f: File | undefined) => {
    if (!f) return;
    // Validate by MIME type — not just the picker's `accept` (a renamed
    // non-image otherwise slips through). The backend re-validates by magic bytes.
    if (!ALLOWED.includes(f.type)) {
      toast.error(t('catalogUpload.notImage', 'Faqat rasm fayli (PNG/JPEG/WebP/SVG/GIF)'));
      if (fileRef.current) fileRef.current.value = '';
      return;
    }
    setBusy(true);
    try {
      // Downscale large photos client-side; SVG/GIF/small files pass through.
      const prepared = isAllowedImage(f) ? await downscaleImage(f) : f;
      if (prepared.size > IMAGE_MAX_BYTES) {
        toast.error(t('catalogUpload.tooLarge', { mb: IMAGE_MAX_MB, defaultValue: `${IMAGE_MAX_MB}MB dan katta` }));
        return;
      }
      const fd = new FormData();
      fd.append('file', prepared);
      const res = await api.post<{ url: string }>(endpoint, fd, {
        headers: { 'Content-Type': 'multipart/form-data' },
      });
      onChange(res.data.url);
    } catch (ex) {
      const d = (ex as { response?: { data?: { detail?: string } } }).response?.data?.detail;
      toast.error(
        d === 'media_upload_not_configured'
          ? t('catalogUpload.notConfigured', 'Yuklash sozlanmagan — havolani qo‘ying')
          : apiErrorMessage(ex),
      );
    } finally {
      setBusy(false);
      if (fileRef.current) fileRef.current.value = '';
    }
  };

  return (
    <div className="flex items-center gap-2">
      {value.trim() ? (
        <span className={cn('relative grid h-11 w-11 shrink-0 place-items-center overflow-hidden border border-line bg-cream', round ? 'rounded-pill' : 'rounded-control')}>
          <img src={value} alt="" className="h-full w-full object-contain" onError={(e) => { e.currentTarget.style.visibility = 'hidden'; }} />
          {/* Destroys the attached image, so it must say so — and be a real
              coarse-pointer target, not a 16px unnamed dot. */}
          <button type="button" onClick={() => onChange('')}
                  aria-label={t('imageUpload.remove', 'Rasmni olib tashlash')}
                  className="tap-target absolute -right-2 -top-2 grid h-11 w-11 place-items-center rounded-pill">
            <span className="grid h-5 w-5 place-items-center rounded-pill bg-ink text-white">
              <X size={12} aria-hidden="true" />
            </span>
          </button>
        </span>
      ) : (
        <span className={cn('grid h-11 w-11 shrink-0 place-items-center border border-dashed border-line text-muted', round ? 'rounded-pill' : 'rounded-control')}>
          <ImagePlus size={16} />
        </span>
      )}
      <input className={inputCls} value={value} inputMode="url"
             onChange={(e) => onChange(e.target.value)} placeholder={placeholder} />
      <button type="button" onClick={pick} disabled={busy}
              className="inline-flex h-11 shrink-0 items-center gap-1.5 rounded-control border border-line px-3 text-small font-medium text-ink-soft hover:bg-ink/5 disabled:opacity-50">
        {busy ? <Loader2 size={14} className="animate-spin" /> : <ImagePlus size={14} />}
        {t('catalogUpload.upload', 'Yuklash')}
      </button>
      <input ref={fileRef} type="file" accept="image/png,image/jpeg,image/webp,image/gif"
             className="hidden" onChange={(e) => onFile(e.target.files?.[0])} />
    </div>
  );
}
