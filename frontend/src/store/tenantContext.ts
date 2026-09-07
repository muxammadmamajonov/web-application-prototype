/**
 * The tenant scope every request is made in — and how long each kind of it lives.
 *
 * THE BUG THIS EXISTS TO FIX (production, 2026-08-11: "Topilmadi — o‘chirilgan
 * yoki ko‘chirilgan bo‘lishi mumkin" on every page of the management panel).
 *
 * There is exactly one ambient input to every API call: the `X-Tenant-Id` header.
 * `api.ts` read it straight out of `localStorage['muddato.persist']`, and the
 * session store persisted `impersonating` into that same slot — deliberately
 * EXEMPTING it from the "does this tenant still belong to you" check that guards
 * the ordinary case:
 *
 *     const keep = s.impersonating ? current : (…validate…)
 *
 * So a platform operator who entered a shop for support and closed the Mini App
 * without pressing "Chiqish" came back still inside it, days later, with the
 * shop's id pinned to every request. If that shop had since been deleted —
 * `DELETE /platform/tenants/{id}` is a HARD delete — `resolve_tenant` answered
 * 404 to everything, and a 404 with no mapped code renders as exactly the
 * reported sentence. Not on one page: on every page, because the cause was a
 * request header rather than any page's own data.
 *
 * TWO SCOPES, TWO LIFETIMES. They were conflated; separating them is the fix.
 *
 *   • The shop an OWNER selected is a preference. It belongs to the device and
 *     should survive closing the app — localStorage, validated against the
 *     membership list on every `/auth/me`.
 *
 *   • The shop an OPERATOR entered is an act, audited server-side, of looking at
 *     another business's real data. It belongs to the sitting, not the device.
 *     sessionStorage: it survives a reload (Mini Apps reload often) and ends when
 *     the app does. Nothing an operator has to remember to switch off can be left
 *     switched on.
 *
 * Reading it lives here rather than in `api.ts` so there is one answer to "which
 * tenant is this request for?", and it cannot drift from the store that decides.
 */

const IMPERSONATION_KEY = 'muddato.impersonation';

export interface Impersonation {
  tenantId: string;
  name: string | null;
}

/**
 * IN-MEMORY IS THE SOURCE OF TRUTH. Storage is a best-effort copy.
 *
 * THE OUTAGE THIS FIXES (production, 2026-08-12). The first version of this
 * module kept the impersonation ONLY in sessionStorage, and read it back on
 * every request to build `X-Tenant-Id`. That made an operator's ability to open
 * any shop's panel depend on a storage API succeeding — and it does not always
 * succeed. The Mini App runs inside a `web.telegram.org` iframe (see the CSP's
 * `frame-ancestors`), where partitioned or blocked storage makes
 * `sessionStorage` throw; Safari Private Browsing does the same. Both were
 * caught and swallowed here, so the write silently did nothing, every
 * `/tenant/*` request went out with NO tenant scope, and `resolve_tenant`
 * answered 404 `no_tenant_membership` to all of them.
 *
 * A module-level variable cannot fail. Storage is now only what survives a
 * RELOAD, which is a nice-to-have; the scope of the requests this tab is making
 * comes from memory, which is the thing that has to be right.
 */
let current: Impersonation | null = null;

/** Best-effort persistence, preferring the session-scoped store. */
function stores(): Storage[] {
  const out: Storage[] = [];
  for (const get of [() => sessionStorage, () => localStorage]) {
    try {
      const s = get();
      if (s) out.push(s);
    } catch { /* blocked in this context — try the next */ }
  }
  return out;
}

/** The shop this app session is impersonating, if any. */
export function readImpersonation(): Impersonation | null {
  if (current) return current;
  for (const s of stores()) {
    try {
      const raw = s.getItem(IMPERSONATION_KEY);
      if (!raw) continue;
      const v = JSON.parse(raw) as Partial<Impersonation>;
      if (typeof v?.tenantId === 'string' && v.tenantId) {
        current = { tenantId: v.tenantId, name: typeof v.name === 'string' ? v.name : null };
        return current;
      }
    } catch { /* unreadable — treat as absent */ }
  }
  return null;
}

export function writeImpersonation(v: Impersonation | null): void {
  // Memory first and unconditionally: this is what the request interceptor
  // reads, and it must not depend on storage being writable.
  current = v;
  for (const s of stores()) {
    try {
      if (v) s.setItem(IMPERSONATION_KEY, JSON.stringify(v));
      else s.removeItem(IMPERSONATION_KEY);
    } catch { /* best effort */ }
  }
}

/**
 * Drop the impersonation fields an older build wrote into the PERSISTED slot.
 *
 * Not cosmetic: a browser that ran the previous build still holds
 * `{"impersonating":true,"activeTenantId":"<maybe-deleted>"}` in localStorage, and
 * zustand would merge it back on rehydrate — so the users currently affected
 * would stay affected after shipping the fix. This is the repair for them, and it
 * runs once, on their next launch. There is no server-side record to migrate:
 * the stale reference only ever lived in the browser.
 */
export function stripLegacyImpersonation(persisted: unknown): Record<string, unknown> {
  const s = (persisted && typeof persisted === 'object' ? persisted : {}) as Record<string, unknown>;
  if (!s.impersonating) return s;
  const { impersonating: _i, impersonatingName: _n, activeTenantId: _t, ...rest } = s;
  return rest;
}

/**
 * The tenant id to send as `X-Tenant-Id`, or null for "no tenant scope".
 *
 * Impersonation wins while it is live, because that IS the current scope. The
 * owner's persisted selection is the fallback. A request with no scope is a
 * normal thing — a platform operator working in the HQ console has none — and
 * sending a leftover one is what turned a stale value into a global failure.
 */
export function activeTenantIdForRequest(): string | null {
  const imp = readImpersonation();
  if (imp) return imp.tenantId;
  try {
    const raw = typeof localStorage === 'undefined' ? null : localStorage.getItem('muddato.persist');
    const tid = raw ? (JSON.parse(raw)?.state?.activeTenantId as unknown) : undefined;
    return typeof tid === 'string' && tid ? tid : null;
  } catch {
    return null;
  }
}
