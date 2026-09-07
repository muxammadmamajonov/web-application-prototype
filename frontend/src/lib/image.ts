/**
 * Client-side image preprocessing for uploads (KYC / receipt / product photos).
 *
 * Phone cameras routinely produce 8–12 MB JPEGs. On a phone-first cash-receipt
 * flow that means customers hit the size wall constantly. We downscale + re-encode
 * on the client (canvas) so a 10 MB photo becomes a ~0.5–2 MB JPEG before it ever
 * leaves the device, then enforce one shared cap and a MIME allowlist on every
 * upload component. The backend still re-validates by magic bytes — this is a UX
 * + bandwidth optimisation, not a security boundary.
 */

/** Single source of truth for the upload size cap — mirrors the backend's 8 MB
 *  image limit. Used by every upload component instead of ad-hoc 5/6 MB values. */
export const IMAGE_MAX_BYTES = 8 * 1024 * 1024;
export const IMAGE_MAX_MB = 8;

/** Raster image types we accept and can downscale. */
const IMAGE_MIME_ALLOWLIST = [
  'image/jpeg',
  'image/png',
  'image/webp',
  'image/heic',
  'image/heif',
] as const;

/** True when the file's MIME type is an accepted raster image. */
export function isAllowedImage(file: File): boolean {
  return (IMAGE_MIME_ALLOWLIST as readonly string[]).includes(file.type);
}

interface DownscaleOptions {
  /** Longest-edge cap in px (default 1600 — plenty for a legible receipt). */
  maxEdge?: number;
  /** JPEG quality 0–1 (default 0.82). */
  quality?: number;
}

/**
 * Downscale + re-encode an image File to a JPEG no larger than `maxEdge` on its
 * longest side. Returns a NEW File (`*.jpg`). On any failure (decode error,
 * canvas blocked, non-image) the original file is returned unchanged so the
 * upload still proceeds — the server caps + validates regardless.
 *
 * HEIC/HEIF can't be decoded by `<img>`/`createImageBitmap` in most browsers;
 * those fall through to the original file and are sent as-is (the backend
 * transcodes/validates), so we never reject a valid phone photo here.
 */
export async function downscaleImage(file: File, opts: DownscaleOptions = {}): Promise<File> {
  const { maxEdge = 1600, quality = 0.82 } = opts;

  // Only attempt to downscale decodable raster images. PDFs and HEIC/HEIF are
  // passed through untouched.
  if (!file.type.startsWith('image/') || file.type === 'image/heic' || file.type === 'image/heif') {
    return file;
  }

  try {
    const bitmap = await loadBitmap(file);
    const { width, height } = bitmap;
    const longest = Math.max(width, height);
    const scale = longest > maxEdge ? maxEdge / longest : 1;

    // Already small enough AND already a JPEG → nothing to gain, keep original.
    if (scale === 1 && file.type === 'image/jpeg') {
      closeBitmap(bitmap);
      return file;
    }

    const targetW = Math.max(1, Math.round(width * scale));
    const targetH = Math.max(1, Math.round(height * scale));

    const canvas = document.createElement('canvas');
    canvas.width = targetW;
    canvas.height = targetH;
    const ctx = canvas.getContext('2d');
    if (!ctx) { closeBitmap(bitmap); return file; }
    // White matte so transparent PNGs don't go black when flattened to JPEG.
    ctx.fillStyle = '#ffffff';
    ctx.fillRect(0, 0, targetW, targetH);
    ctx.drawImage(bitmap, 0, 0, targetW, targetH);
    closeBitmap(bitmap);

    const blob = await canvasToBlob(canvas, 'image/jpeg', quality);
    if (!blob) return file;

    // If re-encoding somehow produced a bigger file (tiny source), keep original.
    if (blob.size >= file.size) return file;

    const name = file.name.replace(/\.[^.]+$/, '') + '.jpg';
    return new File([blob], name, { type: 'image/jpeg', lastModified: Date.now() });
  } catch {
    return file;
  }
}

// ── internals ────────────────────────────────────────────────────────────────

type Decoded = ImageBitmap | HTMLImageElement;

async function loadBitmap(file: File): Promise<Decoded> {
  if (typeof createImageBitmap === 'function') {
    return createImageBitmap(file);
  }
  // Fallback for browsers without createImageBitmap (older Safari).
  const url = URL.createObjectURL(file);
  try {
    const img = await new Promise<HTMLImageElement>((resolve, reject) => {
      const el = new Image();
      el.onload = () => resolve(el);
      el.onerror = () => reject(new Error('decode_failed'));
      el.src = url;
    });
    return img;
  } finally {
    URL.revokeObjectURL(url);
  }
}

function closeBitmap(b: Decoded): void {
  if (typeof ImageBitmap !== 'undefined' && b instanceof ImageBitmap) b.close();
}

function canvasToBlob(canvas: HTMLCanvasElement, type: string, quality: number): Promise<Blob | null> {
  return new Promise((resolve) => {
    canvas.toBlob((b) => resolve(b), type, quality);
  });
}
