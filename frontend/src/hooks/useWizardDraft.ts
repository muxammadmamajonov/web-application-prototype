import { useEffect, useRef } from 'react';

/**
 * Bounded, expiring, session-scoped draft recovery for the contract wizard.
 *
 * ## What may be persisted
 *
 * Non-sensitive commercial progress ONLY: the step reached, free-text item lines
 * (name, quantity, unit price) and the financial terms (months, down payment,
 * formula, markup, start date).
 *
 * Callers must never pass customer or guarantor identity in any form — no name,
 * phone, address, birthdate, passport, JSHSHIR, **customer id**, identity photo,
 * receipt image, auth token or provider credential. There is no "opaque id"
 * exemption: Muddato runs on shared shop-floor devices, so a stored id still
 * lets the next person at the counter resume a sale against someone else's
 * customer record. Recovery is therefore partial by design — the user returns to
 * items and terms and re-picks the customer.
 *
 * ## Storage properties
 *
 *   * `sessionStorage`, never `localStorage` — dies with the tab;
 *   * a hard TTL, so a forgotten draft cannot linger;
 *   * a real UTF-8 byte cap (uz/ru text is multi-byte; `String.length` would
 *     under-count by up to 2× and the cap would not be one);
 *   * cleared on successful commit and on explicit cancel.
 *
 * The KEY is the caller's and must carry the acting user and active tenant, so a
 * draft cannot outlive a logout or a tenant switch on a shared device.
 */

/** How long a draft may survive. A sale is finished or abandoned well inside this. */
const TTL_MS = 2 * 60 * 60 * 1000;   // 2 hours

/** Cap on the serialised draft, in real UTF-8 bytes. */
export const MAX_DRAFT_BYTES = 32 * 1024;

interface Envelope<T> { v: 1; savedAt: number; data: T }

/** Serialised size in real UTF-8 bytes, with a fallback where TextEncoder is absent. */
export function byteLength(text: string): number {
  return typeof TextEncoder !== 'undefined' ? new TextEncoder().encode(text).length : text.length;
}

/**
 * Decide what (if anything) to write for `data` at `now`.
 *
 * Pure, and exported so the cap and the de-duplication can be tested directly
 * rather than inferred from the component that uses them.
 *
 * `previous` is the last *payload* written — deliberately the serialised `data`
 * without the timestamp. Including `savedAt` in that comparison made every
 * payload unique, so the de-duplication never fired and every keystroke wrote to
 * storage.
 */
export function prepareDraftWrite<T>(
  data: T, previous: string | null, now: number,
): { skip: true; reason: 'too-large' | 'unchanged' | 'unserialisable' } | { skip: false; payload: string; body: string } {
  let payload: string;
  try {
    payload = JSON.stringify(data);
  } catch {
    return { skip: true, reason: 'unserialisable' };
  }
  const body = JSON.stringify({ v: 1, savedAt: now, data } satisfies Envelope<T>);
  if (byteLength(body) > MAX_DRAFT_BYTES) return { skip: true, reason: 'too-large' };
  if (payload === previous) return { skip: true, reason: 'unchanged' };
  return { skip: false, payload, body };
}

function storage(): Storage | null {
  try {
    return typeof sessionStorage === 'undefined' ? null : sessionStorage;
  } catch {
    // Private mode / blocked storage — recovery is a convenience, never required.
    return null;
  }
}

export function readDraft<T>(key: string): T | null {
  const store = storage();
  if (!store) return null;
  try {
    const raw = store.getItem(key);
    if (!raw) return null;
    const env = JSON.parse(raw) as Envelope<T>;
    if (env?.v !== 1 || typeof env.savedAt !== 'number') { store.removeItem(key); return null; }
    if (Date.now() - env.savedAt > TTL_MS) { store.removeItem(key); return null; }
    return env.data;
  } catch {
    store.removeItem(key);
    return null;
  }
}

export function clearDraft(key: string): void {
  storage()?.removeItem(key);
}

/**
 * Persist `data` under `key` while `active`.
 *
 * The caller is responsible for passing a PII-free projection of its state —
 * see the note above.
 */
export function useWizardDraft<T>(key: string, active: boolean, data: T): void {
  // The last payload written AND the key it was written under, so an unchanged
  // draft is not rewritten on every keystroke-driven render.
  //
  // The key is part of this on purpose. De-duplication is a statement about one
  // storage slot — "this slot already holds exactly this" — so remembering only
  // the payload makes it a claim about a slot that may no longer be the target.
  // When the acting user or the active tenant changes mid-session the key
  // changes with them, and an identical payload would then be skipped: nothing
  // would ever be written under the new key, and that draft would be silently
  // unrecoverable.
  const lastRef = useRef<{ key: string; payload: string } | null>(null);
  useEffect(() => {
    const store = storage();
    if (!store || !active) return;
    const previous = lastRef.current?.key === key ? lastRef.current.payload : null;
    const decision = prepareDraftWrite(data, previous, Date.now());
    if (decision.skip) return;
    try {
      store.setItem(key, decision.body);
      lastRef.current = { key, payload: decision.payload };
    } catch {
      // Quota failure — silently skip; the form still works.
    }
  }, [key, active, data]);
}
