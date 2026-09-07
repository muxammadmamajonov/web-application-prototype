// @vitest-environment jsdom
/**
 * FB-005 review correction — opening the camera once must open it once.
 *
 * The lifecycle effect depended on `finish`, which depended on `onClose` and
 * `onResult`. The wizard passes those inline, so they are a fresh identity on
 * every parent render: typing a product name three fields away tore down the
 * live MediaStream and called `getUserMedia` again. On a phone that is a black
 * flash mid-scan, a re-prompt on browsers that ask per request, and a decoder
 * left behind on every cycle.
 *
 * The callbacks now live in refs — current when called, never a reason to
 * restart — and the effect is keyed to `open` alone.
 */
import { describe, expect, it, beforeEach, afterEach, vi } from 'vitest';
import { cleanup, render, act } from '@testing-library/react';
import type { Decoder } from '@/lib/barcode';
import '@/i18n';

const detect = vi.fn<() => Promise<string | null>>(async () => null);
const decoderClose = vi.fn();
const createDecoder = vi.fn<() => Promise<Decoder>>();
const cameraAvailable = vi.fn(() => true);

vi.mock('@/lib/barcode', () => ({
  cameraAvailable: () => cameraAvailable(),
  createDecoder: () => createDecoder(),
  blockerFromError: () => 'camera_denied',
  BARCODE_FORMATS: ['code_128'],
  REQUIRED_LINEAR_FORMATS: ['code_128'],
  nativeCoversLinearFormats: async () => true,
}));

const { BarcodeScanner } = await import('@/components/ui/BarcodeScanner');

/** A promise whose resolution this test controls. */
function deferred<T>() {
  let resolve!: (v: T) => void;
  const promise = new Promise<T>((r) => { resolve = r; });
  return { promise, resolve };
}

function makeDecoder(): Decoder {
  return { kind: 'native', detect: () => detect(), close: decoderClose };
}

const track = () => ({ stop: vi.fn(), kind: 'video' });
let tracks: ReturnType<typeof track>[] = [];
const getUserMedia = vi.fn(async () => {
  tracks = [track()];
  return { getTracks: () => tracks } as unknown as MediaStream;
});

/** Let queued microtasks and one animation frame run. */
async function settle() {
  await act(async () => { await Promise.resolve(); await Promise.resolve(); });
}

beforeEach(() => {
  vi.clearAllMocks();
  tracks = [];
  detect.mockImplementation(async () => null);
  createDecoder.mockImplementation(async () => makeDecoder());
  cameraAvailable.mockReturnValue(true);
  Object.defineProperty(navigator, 'mediaDevices', {
    value: { getUserMedia }, configurable: true,
  });
  // The decode loop must not spin during the assertions.
  vi.spyOn(window, 'requestAnimationFrame').mockReturnValue(1 as unknown as number);
  vi.spyOn(window, 'cancelAnimationFrame').mockImplementation(() => {});
  vi.spyOn(HTMLMediaElement.prototype, 'play').mockResolvedValue(undefined);
});
afterEach(() => { cleanup(); vi.restoreAllMocks(); });

describe('a parent rerender is not a reason to restart', () => {
  it('asks for the camera exactly once across rerenders with new callbacks', async () => {
    function Host({ tick }: { tick: number }) {
      // Exactly what the wizard does: fresh arrow functions every render.
      return (
        <BarcodeScanner
          open
          onClose={() => { void tick; }}
          onResult={() => { void tick; }}
          title={`t${tick}`}
        />
      );
    }
    const { rerender } = render(<Host tick={0} />);
    await settle();
    expect(getUserMedia).toHaveBeenCalledTimes(1);

    for (let i = 1; i <= 5; i += 1) {
      rerender(<Host tick={i} />);
      await settle();
    }
    expect(getUserMedia).toHaveBeenCalledTimes(1);
    expect(createDecoder).toHaveBeenCalledTimes(1);
    // Nothing was torn down, so nothing was released mid-scan.
    expect(decoderClose).not.toHaveBeenCalled();
    expect(tracks[0]!.stop).not.toHaveBeenCalled();
  });

  it('calls the LATEST callback, not the one captured at open', async () => {
    const first = vi.fn();
    const second = vi.fn();
    function Host({ onResult }: { onResult: (v: string) => void }) {
      return <BarcodeScanner open onClose={() => {}} onResult={onResult} />;
    }
    const { rerender } = render(<Host onResult={first} />);
    await settle();
    rerender(<Host onResult={second} />);
    await settle();

    // Drive one decode by hand.
    detect.mockImplementation(async () => '352099001761481');
    const raf = window.requestAnimationFrame as unknown as { mock: { calls: [() => void][] } };
    await act(async () => { raf.mock.calls.at(-1)![0](); await Promise.resolve(); });

    expect(first).not.toHaveBeenCalled();
    expect(second).toHaveBeenCalledWith('352099001761481');
  });
});

describe('closing releases everything, whenever it happens', () => {
  it('a decoder that arrives after close is released, not parked', async () => {
    const gate = deferred<Decoder>();
    createDecoder.mockImplementation(() => gate.promise);
    const { unmount } = render(<BarcodeScanner open onClose={() => {}} onResult={() => {}} />);
    unmount();
    await act(async () => { gate.resolve(makeDecoder()); await Promise.resolve(); });

    expect(decoderClose).toHaveBeenCalledTimes(1);
    // …and it never went on to open a camera nobody is watching.
    expect(getUserMedia).not.toHaveBeenCalled();
  });

  it('a stream that arrives after close has its tracks stopped', async () => {
    const gate = deferred<MediaStream>();
    getUserMedia.mockImplementation(() => gate.promise as Promise<MediaStream>);
    const { unmount } = render(<BarcodeScanner open onClose={() => {}} onResult={() => {}} />);
    await settle();
    unmount();

    const late = [track()];
    await act(async () => {
      gate.resolve({ getTracks: () => late } as unknown as MediaStream);
      await Promise.resolve();
    });
    // The permission was granted and the camera DID open; the indicator light
    // stays on until someone stops the tracks.
    expect(late[0]!.stop).toHaveBeenCalledTimes(1);
  });

  it('unmounting cancels the frame loop and stops the tracks', async () => {
    const { unmount } = render(<BarcodeScanner open onClose={() => {}} onResult={() => {}} />);
    await settle();
    const live = tracks[0]!;
    unmount();
    expect(live.stop).toHaveBeenCalledTimes(1);
    expect(decoderClose).toHaveBeenCalledTimes(1);
    expect(window.cancelAnimationFrame).toHaveBeenCalled();
  });

  it('closing without unmounting also releases the camera', async () => {
    function Host({ open }: { open: boolean }) {
      return <BarcodeScanner open={open} onClose={() => {}} onResult={() => {}} />;
    }
    const { rerender } = render(<Host open />);
    await settle();
    const live = tracks[0]!;
    rerender(<Host open={false} />);
    expect(live.stop).toHaveBeenCalledTimes(1);
  });

  it('a double mount/unmount leaks no stream', async () => {
    // React Strict Mode invokes the effect twice on purpose.
    const first = render(<BarcodeScanner open onClose={() => {}} onResult={() => {}} />);
    await settle();
    const a = tracks[0]!;
    first.unmount();
    const second = render(<BarcodeScanner open onClose={() => {}} onResult={() => {}} />);
    await settle();
    const b = tracks[0]!;
    second.unmount();
    expect(a.stop).toHaveBeenCalled();
    expect(b.stop).toHaveBeenCalled();
    expect(getUserMedia).toHaveBeenCalledTimes(2);
  });
});

describe('one open, one result', () => {
  it('a second decode after the first is ignored', async () => {
    const onResult = vi.fn();
    const onClose = vi.fn();
    render(<BarcodeScanner open onClose={onClose} onResult={onResult} />);
    await settle();

    detect.mockImplementation(async () => '352099001761481');
    const raf = window.requestAnimationFrame as unknown as { mock: { calls: [() => void][] } };
    const tick = raf.mock.calls.at(-1)![0];
    await act(async () => { tick(); await Promise.resolve(); });
    // The frame that was already in flight when the sheet started closing.
    await act(async () => { tick(); await Promise.resolve(); });

    expect(onResult).toHaveBeenCalledTimes(1);
    expect(onClose).toHaveBeenCalledTimes(1);
  });
});
