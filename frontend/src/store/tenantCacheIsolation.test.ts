/**
 * One shop's cached data must never survive into another shop's screens.
 *
 * THE BUG THIS PINS (audit, 2026-08-09). No key in the `tenant.*` namespace
 * carries a tenant id — `queryKeys.tenant.overview()` is `['tenant','overview']`
 * for every shop alike. Scoping lives only in the `X-Tenant-Id` request header,
 * so two shops share one cache entry.
 *
 * A platform operator enters Shop A, exits, enters Shop B, and Shop B's
 * dashboard shows SHOP A's debt total, overdue amount, collections queue,
 * customer list and payment ledger — under Shop B's name. The server scopes
 * correctly; this is the client alone serving one tenant's money as another's,
 * and an operator can act on what they see.
 *
 * It lasts the whole session, not a moment: `refetchOnMount: false` is set
 * globally, so a mounted query holding cached data never refetches by itself.
 */
import { beforeEach, describe, expect, it, vi } from 'vitest';

// The session store is wrapped in zustand's `persist`, which writes to
// localStorage at module load. The suite runs in the `node` environment (see
// vite.config.ts), so it must exist BEFORE the store is imported — hence
// `vi.hoisted`, which runs above the import block.
vi.hoisted(() => {
  const mem = new Map<string, string>();
  (globalThis as unknown as { localStorage: Storage }).localStorage = {
    getItem: (k: string) => mem.get(k) ?? null,
    setItem: (k: string, v: string) => void mem.set(k, v),
    removeItem: (k: string) => void mem.delete(k),
    clear: () => mem.clear(),
    key: (i: number) => [...mem.keys()][i] ?? null,
    get length() { return mem.size; },
  } as Storage;
});
import { queryClient } from '@/lib/queryClient';
import { queryKeys } from '@/lib/queryKeys';
import type { MeResponse } from '@/types';
import { useSession } from './session';

const SHOP_A = '11111111-1111-1111-1111-111111111111';
const SHOP_B = '22222222-2222-2222-2222-222222222222';

/** Stand-ins for the money surfaces an operator reads immediately on entry. */
function seedShopACache(): void {
  queryClient.setQueryData(queryKeys.tenant.overview(), { debt_total_uzs: 84_000_000 });
  queryClient.setQueryData(queryKeys.tenant.customersAll(), { items: [{ id: 'a-cust' }] });
  queryClient.setQueryData(queryKeys.tenant.installmentsAll(), { items: [{ id: 'a-inst' }] });
}

beforeEach(() => {
  queryClient.clear();
  useSession.getState().reset();
});

describe('entering a shop cannot inherit the previous shop', () => {
  it('drops tenant-scoped data when entering', () => {
    useSession.getState().enterTenant(SHOP_A, 'Shop A');
    seedShopACache();
    expect(queryClient.getQueryData(queryKeys.tenant.overview())).toBeTruthy();

    useSession.getState().enterTenant(SHOP_B, 'Shop B');

    expect(
      queryClient.getQueryData(queryKeys.tenant.overview()),
      "Shop B's dashboard would render Shop A's debt total",
    ).toBeUndefined();
    expect(queryClient.getQueryData(queryKeys.tenant.customersAll())).toBeUndefined();
    expect(queryClient.getQueryData(queryKeys.tenant.installmentsAll())).toBeUndefined();
  });

  it('drops tenant-scoped data when exiting back to the platform', () => {
    // The realistic path: enter A -> exit -> enter B. Exiting must clear too,
    // or the operator's own platform screens can read a tenant's cache.
    useSession.getState().enterTenant(SHOP_A, 'Shop A');
    seedShopACache();

    useSession.getState().exitTenant();

    expect(queryClient.getQueryData(queryKeys.tenant.overview())).toBeUndefined();
  });

  it('removes rather than invalidates, so nothing stale is readable meanwhile', () => {
    // Invalidation would leave the previous shop's numbers on screen while the
    // refetch is in flight — precisely the window that matters.
    useSession.getState().enterTenant(SHOP_A, 'Shop A');
    seedShopACache();
    useSession.getState().enterTenant(SHOP_B, 'Shop B');

    const remaining = queryClient.getQueryCache().findAll({ queryKey: ['tenant'] });
    expect(remaining, 'tenant cache entries must be gone, not merely marked stale').toEqual([]);
  });

  it('drops tenant data through the generic setActiveTenant transition too', () => {
    useSession.getState().setActiveTenant(SHOP_A);
    seedShopACache();

    useSession.getState().setActiveTenant(SHOP_B);

    expect(queryClient.getQueryData(queryKeys.tenant.overview())).toBeUndefined();
  });

  it('drops tenant data when a refreshed identity selects another membership', () => {
    useSession.getState().setActiveTenant(SHOP_A);
    seedShopACache();
    const me = {
      id: 'user', telegramId: 1, telegramUsername: null,
      firstName: null, lastName: null, photoUrl: null, phone: null, email: null,
      birthdate: null, gender: null, locale: 'uz', platformRole: null,
      tenants: [{
        id: SHOP_B, slug: 'shop-b', name: 'Shop B', role: 'owner',
        status: 'active', planCode: 'starter', trialEndsAt: null,
      }],
    } satisfies MeResponse;

    useSession.getState().setMe(me);

    expect(useSession.getState().activeTenantId).toBe(SHOP_B);
    expect(queryClient.getQueryData(queryKeys.tenant.overview())).toBeUndefined();
  });
});
