import { describe, expect, it } from 'vitest';
import { personaFor, personaHome } from '@/store/persona';
import type { MeResponse, TenantRole } from '@/types';

function makeMe(
  tenants: Array<{ id: string; role: TenantRole }>,
  platformRole: string | null = null,
): MeResponse {
  return {
    id: 'u1',
    platformRole,
    tenants: tenants.map((t) => ({
      id: t.id, role: t.role, slug: t.id, name: t.id,
      status: 'active', planCode: 'free', trialEndsAt: null,
    })),
  } as unknown as MeResponse;
}

describe('personaFor', () => {
  it('returns client when not logged in', () => {
    expect(personaFor(null, null, false)).toBe('client');
  });

  it('routes an owner with a valid active tenant to tenant', () => {
    expect(personaFor(makeMe([{ id: 't1', role: 'owner' }]), 't1', false)).toBe('tenant');
  });

  it('#58: owner with a NULL active tenant falls back to their first tenant (not demoted to client)', () => {
    expect(personaFor(makeMe([{ id: 't1', role: 'owner' }]), null, false)).toBe('tenant');
  });

  it('#58: owner with a FOREIGN/stale active tenant falls back, never demoted to client', () => {
    expect(personaFor(makeMe([{ id: 't1', role: 'owner' }]), 'someone-elses-tenant', false)).toBe('tenant');
  });

  it('routes a sales member to staff', () => {
    expect(personaFor(makeMe([{ id: 't1', role: 'sales' }]), 't1', false)).toBe('staff');
  });

  it('multi-membership: respects the active tenant role (consistent with PersonaGuard)', () => {
    const me = makeMe([{ id: 't1', role: 'owner' }, { id: 't2', role: 'sales' }]);
    expect(personaFor(me, 't2', false)).toBe('staff');
    expect(personaFor(me, 't1', false)).toBe('tenant');
  });

  it('returns client for a user with no tenants', () => {
    expect(personaFor(makeMe([]), null, false)).toBe('client');
  });

  it('returns platform for a platform admin', () => {
    expect(personaFor(makeMe([], 'super_admin'), null, false)).toBe('platform');
  });

  it('impersonation with an active tenant renders the tenant UI', () => {
    expect(personaFor(makeMe([], 'super_admin'), 'any-tenant', true)).toBe('tenant');
  });
});

describe('personaHome', () => {
  it('maps personas to their home routes', () => {
    // The operator lands on the Dashboard, like every other persona lands on
    // its own overview. It pointed at the tenant list only while the Dashboard
    // was feature-gated; that gate is gone (router.tsx routes `overview`
    // unconditionally), so the landing follows the sidebar, which has listed
    // Dashboard first throughout. The customer still lands on the retained
    // home, not the deferred welcome hub.
    expect(personaHome('platform', 'uz')).toBe('/uz/platform/overview');
    expect(personaHome('tenant', 'uz')).toBe('/uz/tenant/overview');
    expect(personaHome('staff', 'ru')).toBe('/ru/staff/today');
    expect(personaHome('client', 'uz')).toBe('/uz/me/my');
  });

  it('never lands a persona on a surface Phase 2 deferred', () => {
    // `/platform/overview` is NOT on this list any more: it was restored to an
    // unconditional route because, unlike the rest of the deferred set, it is a
    // finished page backed by live queries. `/me/welcome` is the client hub
    // still behind V2_SURFACES_ENABLED, and `/me/home` only ever redirects.
    const deferred = ['/me/welcome', '/me/home'];
    for (const persona of ['platform', 'tenant', 'staff', 'client'] as const) {
      const home = personaHome(persona, 'uz');
      expect(deferred.some((d) => home.endsWith(d))).toBe(false);
    }
  });
});
