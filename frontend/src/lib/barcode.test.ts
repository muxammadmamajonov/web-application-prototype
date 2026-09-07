// @vitest-environment jsdom
/**
 * FB-005 — the scanner must read the codes shops actually scan.
 *
 * An IMEI is printed on the box as Code 128; retail goods carry EAN-13/EAN-8/
 * UPC-A. The previous scanner was Telegram's QR popup, which reads none of
 * them, so the field was always typed by hand.
 *
 * PRIVACY is a property of the implementation, not a promise: every frame is
 * decoded in this tab. A test below greps the module for any outbound call.
 *
 * Real camera behaviour on a physical iPhone/Android and inside the Telegram
 * WebView is RUNTIME-ONLY and is recorded as unverified in the pass report.
 * What is verifiable here is decoder selection, format coverage, the failure
 * taxonomy, and that nothing leaves the device.
 */
import { describe, expect, it, afterEach, vi } from 'vitest';
import {
  BARCODE_FORMATS, REQUIRED_LINEAR_FORMATS, blockerFromError, cameraAvailable,
  createDecoder, nativeCoversLinearFormats,
} from '@/lib/barcode';

const g = globalThis as unknown as Record<string, unknown>;

afterEach(() => {
  delete g.BarcodeDetector;
  vi.restoreAllMocks();
});

function fakeNative(formats: string[], detect = async () => [] as { rawValue?: string }[]) {
  class Fake {
    static getSupportedFormats = async () => formats;
    detect = detect;
  }
  g.BarcodeDetector = Fake;
}

describe('the formats it claims', () => {
  it('covers the linear symbologies IMEIs and retail goods are printed in', () => {
    for (const f of ['code_128', 'ean_13', 'ean_8', 'upc_a']) {
      expect(BARCODE_FORMATS).toContain(f);
      expect(REQUIRED_LINEAR_FORMATS).toContain(f);
    }
  });

  it('keeps QR, which some suppliers still use on box labels', () => {
    expect(BARCODE_FORMATS).toContain('qr_code');
    // …but QR alone is not what "supported" means here.
    expect(REQUIRED_LINEAR_FORMATS).not.toContain('qr_code');
  });
});

describe('choosing a decoder', () => {
  it('uses the platform decoder when it covers the linear formats', async () => {
    fakeNative([...BARCODE_FORMATS]);
    expect(await nativeCoversLinearFormats()).toBe(true);
    expect((await createDecoder()).kind).toBe('native');
  });

  it('does NOT trust a platform decoder that only reads QR', async () => {
    // The exact trap: `BarcodeDetector` exists, so a naive check calls it
    // supported, and the scanner keeps failing on the codes it was added for.
    fakeNative(['qr_code']);
    expect(await nativeCoversLinearFormats()).toBe(false);
    expect((await createDecoder()).kind).not.toBe('native');
  });

  it('treats a partial linear set as unsupported', async () => {
    fakeNative(['qr_code', 'code_128']);       // no EAN/UPC
    expect(await nativeCoversLinearFormats()).toBe(false);
  });

  it('falls back to the bundled decoder when the platform has none', async () => {
    expect(await nativeCoversLinearFormats()).toBe(false);
    const decoder = await createDecoder();
    expect(decoder.kind).toBe('zxing');
    decoder.close();
  });

  it('survives a platform decoder that throws while enumerating', async () => {
    class Broken {
      static getSupportedFormats = async () => { throw new Error('nope'); };
      detect = async () => [];
    }
    g.BarcodeDetector = Broken;
    expect(await nativeCoversLinearFormats()).toBe(false);
  });

  it('never throws out of detect — a frame with no code is the common case', async () => {
    fakeNative([...BARCODE_FORMATS], async () => { throw new Error('decode failed'); });
    const decoder = await createDecoder();
    await expect(decoder.detect({} as CanvasImageSource)).resolves.toBeNull();
  });

  it('returns the first decoded value in a frame', async () => {
    fakeNative([...BARCODE_FORMATS], async () => [{ rawValue: '' }, { rawValue: '352099001761481' }]);
    const decoder = await createDecoder();
    await expect(decoder.detect({} as CanvasImageSource)).resolves.toBe('352099001761481');
  });
});

describe('why it cannot run', () => {
  it('reports no camera API at all', () => {
    const original = navigator.mediaDevices;
    Object.defineProperty(navigator, 'mediaDevices', { value: undefined, configurable: true });
    expect(cameraAvailable()).toBe(false);
    Object.defineProperty(navigator, 'mediaDevices', { value: original, configurable: true });
  });

  it.each([
    ['NotAllowedError', 'camera_denied'],
    ['SecurityError', 'camera_denied'],
    ['NotFoundError', 'camera_unavailable'],
    ['OverconstrainedError', 'camera_unavailable'],
    ['NotReadableError', 'camera_unavailable'],
    ['AbortError', 'camera_unavailable'],
    ['SomethingElse', 'camera_unsupported'],
  ])('maps %s to %s, so the copy can be specific', (name, expected) => {
    expect(blockerFromError(Object.assign(new Error('x'), { name }))).toBe(expected);
  });

  it('maps a non-error to a stated blocker rather than crashing', () => {
    expect(blockerFromError(null)).toBe('camera_unsupported');
    expect(blockerFromError(undefined)).toBe('camera_unsupported');
  });
});

describe('privacy is structural', () => {
  it('the module makes no outbound call on any path', async () => {
    const { readFileSync } = await import('node:fs');
    const { resolve } = await import('node:path');
    const src = readFileSync(resolve(__dirname, 'barcode.ts'), 'utf8');
    for (const forbidden of ['fetch(', 'XMLHttpRequest', 'WebSocket', 'navigator.sendBeacon',
                             'http://', 'https://', 'axios', 'api.']) {
      expect(src).not.toContain(forbidden);
    }
  });

  it('the scanner component uploads no frame either', async () => {
    const { readFileSync } = await import('node:fs');
    const { resolve } = await import('node:path');
    const src = readFileSync(resolve(__dirname, '../components/ui/BarcodeScanner.tsx'), 'utf8');
    for (const forbidden of ['fetch(', 'FormData', 'toDataURL', 'toBlob', 'axios', 'api.']) {
      expect(src).not.toContain(forbidden);
    }
  });

  it('the decoder is imported dynamically, so browsers that never need it do not pay for it', async () => {
    const { readFileSync } = await import('node:fs');
    const { resolve } = await import('node:path');
    const src = readFileSync(resolve(__dirname, 'barcode.ts'), 'utf8');
    // `import(...)` inside the lazy path, never a top-level static import.
    expect(src).toMatch(/import\('@zxing\/browser'\)/);
    expect(src).toMatch(/import\('@zxing\/library'\)/);
    expect(src).not.toMatch(/^import .*@zxing/m);
  });
});
