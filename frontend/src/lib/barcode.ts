/**
 * Reading a code off a phone box, on the device.
 *
 * The identifiers shops actually scan are LINEAR: an IMEI is printed as Code
 * 128 on the box, retail items carry EAN-13/EAN-8/UPC-A. A QR-only scanner
 * cannot read any of them, so the field had to be typed by hand.
 *
 * PRIVACY. Every frame is decoded in this tab. Nothing is uploaded, no image
 * leaves the device, and there is no network call on any path here — asserted
 * by a test that greps this module.
 *
 * TWO DECODERS, one interface:
 *
 *   1. the platform's own `BarcodeDetector`, when it exists AND advertises the
 *      formats we need (Chrome and Android WebView do);
 *   2. otherwise ZXing, imported dynamically so browsers that never need it do
 *      not download it.
 *
 * Which one ran is reported back, because "the scanner found nothing" means
 * something different on a device with no decoder at all.
 */

/** What a shop scans. QR stays: some suppliers label boxes with it. */
export const BARCODE_FORMATS = [
  'code_128', 'ean_13', 'ean_8', 'upc_a', 'qr_code',
] as const;
export type BarcodeFormat = (typeof BARCODE_FORMATS)[number];

/** The linear formats this exists to add. Native support is only useful if it
 *  covers these — a detector that reads QR alone changes nothing. */
export const REQUIRED_LINEAR_FORMATS: BarcodeFormat[] = ['code_128', 'ean_13', 'ean_8', 'upc_a'];

export type DecoderKind = 'native' | 'zxing' | 'none';

export interface Decoder {
  kind: DecoderKind;
  /** The first code found in this frame, or null. Never throws. */
  detect(source: CanvasImageSource): Promise<string | null>;
  close(): void;
}

interface NativeDetector {
  detect(source: CanvasImageSource): Promise<{ rawValue?: string }[]>;
}
interface NativeDetectorCtor {
  new (init?: { formats?: string[] }): NativeDetector;
  getSupportedFormats?: () => Promise<string[]>;
}

function nativeCtor(): NativeDetectorCtor | null {
  const g = globalThis as unknown as { BarcodeDetector?: NativeDetectorCtor };
  return typeof g.BarcodeDetector === 'function' ? g.BarcodeDetector : null;
}

/**
 * Whether the platform decoder covers the LINEAR formats.
 *
 * A `BarcodeDetector` that only advertises `qr_code` is not sufficient here,
 * and treating its presence as support is how a scanner silently keeps failing
 * on exactly the codes it was added for.
 */
export async function nativeCoversLinearFormats(): Promise<boolean> {
  const Ctor = nativeCtor();
  if (!Ctor?.getSupportedFormats) return false;
  try {
    const supported = await Ctor.getSupportedFormats();
    return REQUIRED_LINEAR_FORMATS.every((f) => supported.includes(f));
  } catch {
    return false;
  }
}

/** True when this browser can open a camera at all. */
export function cameraAvailable(): boolean {
  return typeof navigator !== 'undefined' && !!navigator.mediaDevices?.getUserMedia;
}

async function zxingDecoder(): Promise<Decoder | null> {
  try {
    const [{ BrowserMultiFormatReader }, lib] = await Promise.all([
      import('@zxing/browser'),
      import('@zxing/library'),
    ]);
    const hints = new Map();
    hints.set(lib.DecodeHintType.POSSIBLE_FORMATS, [
      lib.BarcodeFormat.CODE_128,
      lib.BarcodeFormat.EAN_13,
      lib.BarcodeFormat.EAN_8,
      lib.BarcodeFormat.UPC_A,
      lib.BarcodeFormat.QR_CODE,
    ]);
    const reader = new BrowserMultiFormatReader(hints);
    // One reusable canvas: allocating per frame churns memory on a phone.
    const canvas = document.createElement('canvas');
    const ctx = canvas.getContext('2d', { willReadFrequently: true });
    return {
      kind: 'zxing',
      async detect(source) {
        if (!ctx) return null;
        const w = (source as HTMLVideoElement).videoWidth || 640;
        const h = (source as HTMLVideoElement).videoHeight || 480;
        if (!w || !h) return null;
        canvas.width = w; canvas.height = h;
        ctx.drawImage(source, 0, 0, w, h);
        try {
          return reader.decodeFromCanvas(canvas)?.getText() ?? null;
        } catch {
          return null;      // "no code in this frame" is the common case
        }
      },
      close() { /* the reader holds no stream; the caller owns the camera */ },
    };
  } catch {
    return null;
  }
}

/** The best decoder this browser can give us. */
export async function createDecoder(): Promise<Decoder> {
  const Ctor = nativeCtor();
  if (Ctor && await nativeCoversLinearFormats()) {
    const detector = new Ctor({ formats: [...BARCODE_FORMATS] });
    return {
      kind: 'native',
      async detect(source) {
        try {
          const found = await detector.detect(source);
          return found.find((r) => r.rawValue)?.rawValue ?? null;
        } catch {
          return null;
        }
      },
      close() { /* nothing to release */ },
    };
  }
  return (await zxingDecoder()) ?? {
    kind: 'none',
    async detect() { return null; },
    close() { /* nothing to release */ },
  };
}

/** Why the scanner cannot run. Mapped to localized copy by the caller. */
export type ScanBlocker =
  | 'camera_unsupported'      // no getUserMedia at all
  | 'camera_denied'           // the person said no, or policy said no
  | 'camera_unavailable'      // no device, or another app holds it
  | 'decoder_unsupported';    // neither decoder could be created

export function blockerFromError(err: unknown): ScanBlocker {
  const name = (err as { name?: string } | null)?.name ?? '';
  if (name === 'NotAllowedError' || name === 'SecurityError') return 'camera_denied';
  if (name === 'NotFoundError' || name === 'OverconstrainedError') return 'camera_unavailable';
  if (name === 'NotReadableError' || name === 'AbortError') return 'camera_unavailable';
  return 'camera_unsupported';
}
