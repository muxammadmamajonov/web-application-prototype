/** Session store — current user, active tenant, persona resolution. */
import { create } from 'zustand';
import { createJSONStorage, persist } from 'zustand/middleware';
import { personaFor } from '@/store/persona';
import {
  readImpersonation, stripLegacyImpersonation, writeImpersonation,
} from '@/store/tenantContext';
import { queryKeys } from '@/lib/queryKeys';
import { queryClient } from '@/lib/queryClient';
import type { MeResponse, Persona, TenantRole } from '@/types';

interface SessionState {
  me: MeResponse | null;
  activeTenantId: string | null;
  /** A platform super-admin is viewing a tenant's dashboard for support. */
  impersonating: boolean;
  impersonatingName: string | null;
  setMe: (me: MeResponse | null) => void;
  setActiveTenant: (id: string | null) => void;
  enterTenant: (id: string, name: string) => void;
  exitTenant: () => void;
  /** Resolve the persona we should render the UI for. */
  resolvePersona: () => Persona;
  /** Role inside the currently active tenant, if any. */
  activeRole: () => TenantRole | null;
  /** True only for a platform super-admin. Support is read-only: the backend
   *  enforces this (403 on mutations); the UI hides destructive controls. */
  canManagePlatform: () => boolean;
  reset: () => void;
}

type TenantTransitionPatch = Partial<Pick<
  SessionState,
  'me' | 'impersonating' | 'impersonatingName'
>>;


/**
 * Drop every tenant-scoped query before the active tenant changes.
 *
 * THE BUG THIS FIXES (audit, 2026-08-09). No key in the `tenant.*` namespace
 * carries a tenant id — `queryKeys.tenant.overview()` is literally
 * `['tenant','overview']` for every shop. Scoping happens only in the
 * `X-Tenant-Id` request header, so two different shops share one cache entry.
 *
 * A platform operator enters Shop A, exits, enters Shop B — and Shop B's
 * dashboard renders SHOP A's debt total, overdue amount, collections queue,
 * customer list and payment ledger, under Shop B's name. The server is not at
 * fault; it scopes correctly. This is purely the client serving one tenant's
 * financial data as another's, and an operator can act on it.
 *
 * It persists for the whole session rather than a moment, because the client
 * sets `refetchOnMount: false` globally (lib/queryClient.ts): a mounted query
 * holding cached data never refetches on its own.
 *
 * Removing (not invalidating) is deliberate — invalidation leaves the stale
 * data readable while the refetch is in flight, which is exactly the window
 * where the wrong shop's numbers are on screen.
 *
 * Placed in the store rather than at the two call sites (the tenant drawer's
 * "enter" and the impersonation banner's "exit") so a third entry point cannot
 * be added later that forgets it.
 */
function dropTenantScopedCache(): void {
  // One prefix covers the staff surfaces too — they key under
  // `['tenant','staff-today']` and siblings, not a namespace of their own.
  // `me` is deliberately kept: it is the OPERATOR's own identity and membership
  // list, unchanged by entering a shop, and dropping it would force a needless
  // /auth/me round trip on every switch.
  queryClient.removeQueries({ queryKey: queryKeys.tenant.root() });
}

export const useSession = create<SessionState>()(
  persist(
    (set, get) => {
      const transitionTenant = (id: string | null, patch: TenantTransitionPatch = {}) => {
        if (get().activeTenantId !== id) dropTenantScopedCache();
        set({ activeTenantId: id, ...patch });
      };
      return ({
      me: null,
      // Seeded from the APP SESSION, not the device. An operator who closed the
      // Mini App while inside a shop comes back to their own console — see
      // `store/tenantContext.ts` for why that lifetime is the fix.
      activeTenantId: readImpersonation()?.tenantId ?? null,
      impersonating: !!readImpersonation(),
      impersonatingName: readImpersonation()?.name ?? null,
      setMe: (me) => set((s) => {
        // Keep the persisted active tenant ONLY if it still belongs to this
        // user; otherwise fall back to their first tenant. A stale/foreign id
        // (another account on the same device, or a non-clean exit) would
        // otherwise make activeRole() return null and resolvePersona() demote a
        // real owner to 'client' — landing them on the non-client page. (#58)
        // Platform-admin impersonation is exempt: its activeTenantId points to a
        // tenant the admin is intentionally not a member of.
        const tenants = me?.tenants ?? [];
        const current = s.activeTenantId;
        // Impersonation is exempt from the membership check — its whole point is
        // a tenant the operator is deliberately not a member of — but ONLY when
        // the actor is actually a platform operator. Without that second half,
        // the exemption was unconditional: any stale `impersonating` flag (a
        // different account on the same device, a non-clean exit) pinned an
        // unvalidated tenant id onto every request for as long as it survived.
        const mayImpersonate = s.impersonating && !!me?.platformRole;
        const keep = mayImpersonate
          ? current
          : (current && tenants.some((t) => t.id === current))
            ? current
            : tenants[0]?.id ?? null;
        if (s.impersonating && !mayImpersonate) writeImpersonation(null);
        if (current !== keep) dropTenantScopedCache();
        // Clear the FLAG too, not just the storage. It was seeded from
        // sessionStorage before `/auth/me` answered, so on a shared device the
        // next account booted with `impersonating: true` and kept it — the
        // storage was cleaned up underneath a flag that stayed set, which is the
        // half of the state that `personaFor` actually reads.
        return mayImpersonate
          ? { me, activeTenantId: keep }
          : { me, activeTenantId: keep, impersonating: false, impersonatingName: null };
      }),
      setActiveTenant: (id) => transitionTenant(id),
      enterTenant: (id, name) => {
        writeImpersonation({ tenantId: id, name });
        transitionTenant(id, { impersonating: true, impersonatingName: name });
      },
      exitTenant: () => {
        writeImpersonation(null);
        transitionTenant(null, { impersonating: false, impersonatingName: null });
      },
      // Delegates to the pure, unit-tested personaFor so the store and the
      // router's RequireAuth/PersonaGuard share one source of truth (#58).
      resolvePersona: () => personaFor(get().me, get().activeTenantId, get().impersonating),
      activeRole: () => {
        const me = get().me;
        const id = get().activeTenantId;
        if (!me || !id) return null;
        return me.tenants.find((t) => t.id === id)?.role ?? null;
      },
      canManagePlatform: () => get().me?.platformRole === 'super_admin',
      reset: () => {
        writeImpersonation(null);
        transitionTenant(null, { me: null, impersonating: false, impersonatingName: null });
      },
    });
    },
    {
      // IMPORTANT: a dedicated key, kept separate from the auth breadcrumb
      // (`muddato.authed`) and the legacy `muddato.session` slot (see api.ts).
      // Auth is now cookie-only — the JWT is never written to localStorage — but
      // this store still owns ONLY tenant/impersonation context, so it must not
      // share a key with the auth flag.
      name: 'muddato.persist',
      // Declared explicitly rather than left to the middleware default, which
      // changed under us in zustand 5: v4 defaulted to
      // `createJSONStorage(() => localStorage)`, v5.0.15 defaults to
      // `createJSONStorage(() => window.localStorage)`. `createJSONStorage`
      // swallows the resulting throw and returns `undefined`, so persist
      // degrades to a NO-OP that only console.warns — it never writes, and
      // nothing fails loudly.
      //
      // That is a silent data-loss shape for this store in particular:
      // `activeTenantIdForRequest()` (store/tenantContext.ts) deliberately
      // re-reads `localStorage['muddato.persist']` instead of the in-memory
      // state, so an owner's shop selection would still look correct on screen
      // while every request went out with NO `X-Tenant-Id` — the same 404
      // no_tenant_membership failure the 2026-08-11 fix exists to prevent.
      //
      // `localStorage` unqualified also resolves in any realm that provides it
      // without a `window` (the `node` test environment, a worker), which
      // `window.localStorage` does not. Blocked/partitioned storage is
      // unaffected either way: it throws on access, and both versions catch it.
      storage: createJSONStorage(() => localStorage),
      // ONLY the owner's own shop selection is a device-level preference.
      // Impersonation is not persisted here at all — it lives in sessionStorage
      // (`store/tenantContext.ts`), so an operator who closes the app inside a
      // shop is not still inside it on the next launch, pinning that shop's id
      // to every request long after it may have been deleted.
      partialize: (s) => ({ activeTenantId: s.impersonating ? null : s.activeTenantId }),
      // A browser that ran the previous build still holds the impersonation
      // fields in this slot, and a plain rehydrate would merge them straight
      // back — leaving the affected users affected. Strip them once, on their
      // next launch. This is the whole repair: the stale reference never existed
      // anywhere but the browser.
      merge: (persisted, current) => {
        const merged = { ...current, ...stripLegacyImpersonation(persisted) };
        // THE RELOAD BOUNCE (production, 2026-08-14). `partialize` deliberately
        // persists `activeTenantId: null` while an impersonation is live — the
        // entered shop is an app-session act, not a device preference. But
        // `merge` runs AFTER the initializer that seeds the scope from
        // sessionStorage, so that deliberate null overwrote the seeded id and
        // left `impersonating: true` with no tenant.
        //
        // `personaFor` requires BOTH, so it fell through to `me.platformRole`,
        // resolved 'platform', and `PersonaGuard` redirected every /tenant/*
        // URL to the platform dashboard. A Telegram Mini App reloads often, so
        // an operator was thrown out of the shop again and again.
        //
        // The tell was the desync: `activeTenantIdForRequest()` reads the same
        // sessionStorage and carried on sending the shop's `X-Tenant-Id`, so
        // the data layer was inside the shop while routing insisted it wasn't.
        // The session scope is the authority here, so it gets the last word.
        const imp = readImpersonation();
        return imp
          ? { ...merged, activeTenantId: imp.tenantId, impersonating: true, impersonatingName: imp.name }
          : merged;
      },
    },
  ),
);
