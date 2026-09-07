/** Pure persona resolution — the single source of truth for "which persona's UI
 *  does this user get, and where do they land?". Kept dependency-free (no zustand,
 *  no localStorage) so it's trivially unit-testable and shared by BOTH the session
 *  store (`resolvePersona`) and the router's `RequireAuth`/`PersonaGuard`, so the
 *  landing redirect and the per-route guard can never disagree (#58).
 */
import type { MeResponse, Persona, TenantRole } from '@/types';

/** One membership from `/auth/me`. */
type TenantMembership = NonNullable<MeResponse['tenants']>[number];

/**
 * The membership the UI is currently acting on.
 *
 * Falls back to the user's FIRST tenant when `activeTenantId` is missing or stale
 * (a foreign id from another account on the same device, or a not-yet-set id right
 * after a fresh login/demo). Without that fallback a real owner whose
 * `activeTenantId` is null/foreign would resolve to `'client'` and get bounced to
 * the non-client page — the #58 bug.
 *
 * Everything that asks "which shop is this user in right now?" MUST go through
 * here. Reading `tenants[0]` directly is the bug this exists to prevent: for a
 * user with more than one membership it answers about a shop they are not
 * looking at.
 */
export function activeTenantFor(
  me: MeResponse | null,
  activeTenantId: string | null,
): TenantMembership | null {
  const tenants = me?.tenants ?? [];
  if (tenants.length === 0) return null;
  const selected = activeTenantId
    ? tenants.find((t) => t.id === activeTenantId) ?? null
    : null;
  return selected ?? tenants[0] ?? null;
}

/**
 * May this role manage the shop's records — create, edit, approve?
 *
 * THE BACKEND IS THE AUTHORITY. `_require_owner_or_manager` in
 * `backend/app/api/tenant/_shared.py` decides what actually happens; this is the
 * UI affordance that mirrors it, so a clerk is not shown a button that will
 * answer 403. The two are pinned together by `rolePermissions.contract.test.ts`.
 *
 * It lives here, beside `personaFor`, because the rule was previously restated
 * inline wherever it was needed — `activeRole === 'owner' || activeRole ===
 * 'manager'`, three times, in two files. Three copies of one permission rule is
 * three places to forget when the rule changes, and a permission rule that
 * drifts silently is the kind that grants rather than denies.
 */
export function canManageTenant(role: TenantRole | null): boolean {
  return role === 'owner' || role === 'manager';
}

/**
 * Resolve the persona for a user.
 *
 * Shares `activeTenantFor`'s selection and fallback with every other
 * active-tenant question, so the persona, the landing route and the blocked gate
 * can never disagree about which shop is current.
 */
export function personaFor(
  me: MeResponse | null,
  activeTenantId: string | null,
  impersonating: boolean,
): Persona {
  if (!me) return 'client';
  // A platform admin who explicitly entered a tenant renders the tenant UI.
  if (impersonating && activeTenantId) return 'tenant';
  if (me.platformRole) return 'platform';
  const role = activeTenantFor(me, activeTenantId)?.role ?? null;
  if (canManageTenant(role)) return 'tenant';
  if (role === 'sales') return 'staff';
  return 'client';
}

/**
 * True when the shop the user is currently acting on is blocked.
 *
 * Blocking is per-tenant. A user who belongs to a healthy shop and a blocked one
 * must be able to work in the healthy one, and selecting the blocked one must
 * gate it — which is only correct if the question is asked about the ACTIVE
 * membership. This gate is a UX affordance; the backend remains the authority on
 * what a blocked tenant may actually do.
 */
export function isActiveTenantBlocked(
  me: MeResponse | null,
  activeTenantId: string | null,
): boolean {
  return activeTenantFor(me, activeTenantId)?.status === 'blocked';
}

/**
 * The home route for a persona, locale-prefixed.
 *
 * Each persona lands on a surface it actually works in and that is retained:
 * the operator on the tenant list (not the deferred platform overview), the
 * customer on /me/my (not the deferred welcome hub at /me/home).
 */
export function personaHome(persona: Persona, locale: string): string {
  switch (persona) {
    // The operator's landing is the Dashboard, matching every other
    // persona (tenant → overview, staff → today) and the nav order,
    // which has listed Dashboard first all along. It pointed at the
    // tenant list only because the Dashboard used to be feature-gated.
    case 'platform': return `/${locale}/platform/overview`;
    case 'tenant':   return `/${locale}/tenant/overview`;
    case 'staff':    return `/${locale}/staff/today`;
    default:         return `/${locale}/me/my`;
  }
}
