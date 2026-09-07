// @vitest-environment jsdom
/**
 * Phase 3 UX-092 — draft recovery must not become a PII leak.
 *
 * The wizard draft exists so a killed app does not cost a shop its half-entered
 * sale. The constraint that shapes it is that browser storage must never hold
 * passport or JSHSHIR values, customer or guarantor identity, identity photos,
 * receipt images, tokens or provider credentials.
 *
 * These tests pin the storage properties that keep that true: session scope, a
 * hard expiry, a size cap, and clearing at the points where the draft has done
 * its job. The PII rule itself is enforced at the call site — the wizard passes
 * a projection containing only step, item lines and terms — and asserted in
 * `InstallmentModal`'s own coverage of what it hands this hook.
 */
import { describe, expect, it, beforeEach, vi, afterEach } from 'vitest';
import { readFileSync } from 'node:fs';
import { byteLength, clearDraft, MAX_DRAFT_BYTES, prepareDraftWrite, readDraft, useWizardDraft } from './useWizardDraft';

const KEY = 'test.draft';

beforeEach(() => { sessionStorage.clear(); vi.useRealTimers(); });
afterEach(() => { vi.useRealTimers(); });

/** Mirror of the hook's envelope, so a test can seed storage directly. */
const write = (data: unknown, savedAt = Date.now()) =>
  sessionStorage.setItem(KEY, JSON.stringify({ v: 1, savedAt, data }));

describe('draft storage', () => {
  it('round-trips a draft', () => {
    write({ step: 2, months: 6 });
    expect(readDraft<{ step: number }>(KEY)?.step).toBe(2);
  });

  it('uses sessionStorage, never localStorage', async () => {
    write({ step: 1 });
    expect(sessionStorage.getItem(KEY)).toBeTruthy();
    // Asserted at the source level rather than by probing `localStorage`, which
    // this runtime does not expose. It is also the stronger check: the module
    // must not reference persistent storage at all, in any code path.
    const src = await import('fs/promises')
      .then((fs) => fs.readFile('src/hooks/useWizardDraft.ts', 'utf-8'));
    // Strip comments first — the doc block names localStorage precisely to say
    // it is NOT used, and the assertion is about executable code.
    const code = src.replace(/\/\*[\s\S]*?\*\//g, '').replace(/\/\/.*$/gm, '');
    expect(code).not.toContain('localStorage');
    expect(code).toContain('sessionStorage');
  });

  it('expires a stale draft instead of resurrecting it', () => {
    const threeHoursAgo = Date.now() - 3 * 60 * 60 * 1000;
    write({ step: 3 }, threeHoursAgo);
    expect(readDraft(KEY)).toBeNull();
    // …and the expired entry is removed, not merely ignored.
    expect(sessionStorage.getItem(KEY)).toBeNull();
  });

  it('keeps a draft that is still inside its window', () => {
    write({ step: 3 }, Date.now() - 60 * 1000);
    expect(readDraft(KEY)).not.toBeNull();
  });

  it('discards an unreadable or foreign-version entry', () => {
    sessionStorage.setItem(KEY, 'not json');
    expect(readDraft(KEY)).toBeNull();
    sessionStorage.setItem(KEY, JSON.stringify({ v: 99, savedAt: Date.now(), data: {} }));
    expect(readDraft(KEY)).toBeNull();
    expect(sessionStorage.getItem(KEY)).toBeNull();
  });

  it('clears on demand', () => {
    write({ step: 1 });
    clearDraft(KEY);
    expect(readDraft(KEY)).toBeNull();
  });

  it('returns null rather than throwing when storage is unavailable', () => {
    const spy = vi.spyOn(Storage.prototype, 'getItem').mockImplementation(() => {
      throw new Error('blocked');
    });
    expect(() => readDraft(KEY)).not.toThrow();
    expect(readDraft(KEY)).toBeNull();
    spy.mockRestore();
  });
});

/*
 * The PII guarantee and the hydrate-before-persist ordering are proven at the
 * render level in `src/routes/tenant/_modals/wizardDraft.test.tsx`, which drives
 * the real wizard. A source-string assertion alone would not have caught the
 * ordering bug this remediation fixed, because the source read correctly — it
 * was the effect ORDER that was wrong.
 */

// ── P2-04: the cap and the de-duplication, tested directly ───────────────────

describe('the byte cap is a real UTF-8 cap', () => {
  it('measures multi-byte text in bytes, not characters', () => {
    // Cyrillic and Uzbek Latin diacritics are 2 bytes each in UTF-8. A
    // `String.length` check would have accepted roughly twice the real payload.
    const cyrillic = 'ш'.repeat(1000);
    expect(cyrillic.length).toBe(1000);
    expect(byteLength(cyrillic)).toBe(2000);
  });

  it('rejects an oversized multibyte payload that fits under a character count', () => {
    // ~20k Cyrillic characters ≈ 40 KB of UTF-8 — over the 32 KB cap while its
    // character length is comfortably under it.
    const chars = 20_000;
    const data = { note: 'ш'.repeat(chars) };
    expect(JSON.stringify(data).length).toBeLessThan(MAX_DRAFT_BYTES);
    expect(byteLength(JSON.stringify(data))).toBeGreaterThan(MAX_DRAFT_BYTES);

    const decision = prepareDraftWrite(data, null, 1_700_000_000_000);
    expect(decision.skip).toBe(true);
    expect(decision.skip && decision.reason).toBe('too-large');
  });

  it('accepts a payload that genuinely fits', () => {
    const decision = prepareDraftWrite({ step: 1, items: [] }, null, 1_700_000_000_000);
    expect(decision.skip).toBe(false);
  });

  it('an oversized draft is skipped, never written truncated', () => {
    sessionStorage.clear();
    const decision = prepareDraftWrite({ note: 'ш'.repeat(20_000) }, null, Date.now());
    expect(decision.skip).toBe(true);
    expect(sessionStorage.getItem(KEY)).toBeNull();
  });
});

describe('de-duplication is real', () => {
  it('skips an unchanged payload even though the timestamp moved on', () => {
    // The old implementation compared the envelope including `savedAt`, so no
    // two comparisons were ever equal and every render wrote to storage.
    const data = { step: 1, months: 6 };
    const first = prepareDraftWrite(data, null, 1_700_000_000_000);
    expect(first.skip).toBe(false);

    const second = prepareDraftWrite(data, first.skip ? null : first.payload, 1_700_000_999_999);
    expect(second.skip, 'a later clock must not defeat de-duplication').toBe(true);
    expect(second.skip && second.reason).toBe('unchanged');
  });

  it('writes again once the data actually changes', () => {
    const first = prepareDraftWrite({ step: 1 }, null, 1_700_000_000_000);
    const previous = first.skip ? null : first.payload;
    const second = prepareDraftWrite({ step: 2 }, previous, 1_700_000_000_001);
    expect(second.skip).toBe(false);
  });

  it('stamps the write time in the stored envelope', () => {
    const at = 1_700_000_123_456;
    const d = prepareDraftWrite({ step: 3 }, null, at);
    expect(d.skip).toBe(false);
    if (!d.skip) expect(JSON.parse(d.body).savedAt).toBe(at);
  });
});

describe('documented PII rule matches the code', () => {
  it('claims no exemption for an opaque customer id', () => {
    // The doc previously said a customer id "may" be stored. On a shared shop
    // device that still lets the next person resume someone else's sale, and
    // the wizard never passes one — so the doc must not sanction it.
    const src = readFileSync('src/hooks/useWizardDraft.ts', 'utf-8');
    expect(src).not.toMatch(/opaque tenant-scoped id/i);
    expect(src).toMatch(/customer id/i);          // named among the forbidden fields
  });
});

// ── C3-07: de-duplication is a claim about ONE storage slot ─────────────────

import { render, cleanup } from '@testing-library/react';

/** Mounts the hook so key changes go through the real effect. */
function Harness({ storageKey, data }: { storageKey: string; data: unknown }) {
  useWizardDraft(storageKey, true, data);
  return null;
}
const stored = (k: string) => {
  const raw = sessionStorage.getItem(k);
  return raw ? JSON.parse(raw).data : null;
};

describe('de-duplication is scoped to the storage key', () => {
  afterEach(cleanup);

  it('writes an identical payload again when the key changes', () => {
    const payload = { step: 2, items: [{ product_name: 'Same Phone' }] };
    const { rerender } = render(<Harness storageKey="k.user-1" data={payload} />);
    expect(stored('k.user-1')).toEqual(payload);

    // Same data, different slot — e.g. the user switched tenant mid-wizard.
    rerender(<Harness storageKey="k.user-2" data={payload} />);
    expect(stored('k.user-2'),
      'an unchanged payload must still be written under a NEW key').toEqual(payload);
  });

  it('still skips a genuinely unchanged rewrite under the same key', () => {
    const payload = { step: 2 };
    const { rerender } = render(<Harness storageKey="k.same" data={payload} />);
    const first = sessionStorage.getItem('k.same');

    rerender(<Harness storageKey="k.same" data={{ ...payload }} />);
    expect(sessionStorage.getItem('k.same'),
      'an unchanged payload should not be rewritten').toBe(first);
  });

  it('writes again when the data changes under the same key', () => {
    const { rerender } = render(<Harness storageKey="k.edit" data={{ step: 1 }} />);
    rerender(<Harness storageKey="k.edit" data={{ step: 4 }} />);
    expect(stored('k.edit')).toEqual({ step: 4 });
  });

  it('leaves the previous key\'s draft intact — switching is not deleting', () => {
    const payload = { step: 3 };
    const { rerender } = render(<Harness storageKey="k.a" data={payload} />);
    rerender(<Harness storageKey="k.b" data={payload} />);
    expect(stored('k.a'), 'the old slot is not the new slot\'s business').toEqual(payload);
    expect(stored('k.b')).toEqual(payload);
  });

  it('keeps the envelope shape and TTL readable after a key switch', () => {
    const payload = { step: 5 };
    const { rerender } = render(<Harness storageKey="k.one" data={payload} />);
    rerender(<Harness storageKey="k.two" data={payload} />);
    expect(readDraft('k.two')).toEqual(payload);
    const env = JSON.parse(sessionStorage.getItem('k.two')!);
    expect(env.v).toBe(1);
    expect(typeof env.savedAt).toBe('number');
  });
});
