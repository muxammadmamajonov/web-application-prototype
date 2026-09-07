// @vitest-environment jsdom
/**
 * A tenant deleted mid-session must end the scope, not paint every page.
 *
 * The lifetime fix (`store/tenantContext.ts`) stops a stale impersonation from
 * OUTLIVING an app session. It cannot help while one is live: an operator can be
 * inside a shop on one device at the moment it is deleted from another, and
 * `DELETE /platform/tenants/{id}` is a hard delete. From then on `resolve_tenant`
 * answers 404 to every tenant-scoped request, and — because the scope is a
 * header, not any page's own data — the panel fills with
 * "Topilmadi — o‘chirilgan yoki ko‘chirilgan bo‘lishi mumkin", one card per page.
 *
 * So the scope is dropped at the first such 404 and the app restarts from the
 * operator's own console. What matters as much is what this must NOT catch: an
 * ordinary deleted customer or contract is a fact about a resource, and has to
 * keep reaching the page that asked for it.
 */
import { describe, expect, it, beforeEach, afterEach, vi } from 'vitest';
import type { AxiosAdapter } from 'axios';
import { api } from './api';
import { readImpersonation, writeImpersonation } from '@/store/tenantContext';

const savedAdapter = api.defaults.adapter;
let replaced: string[];

/** Fail every request with a given status + backend detail code. */
function failWith(status: number, detail: unknown): AxiosAdapter {
  return async (config) =>
    Promise.reject(Object.assign(new Error('boom'), {
      isAxiosError: true,
      config,
      response: { status, data: { detail }, headers: {}, config, statusText: '' },
    }));
}

beforeEach(() => {
  replaced = [];
  sessionStorage.clear();
  // jsdom refuses a real navigation; record the intent instead.
  Object.defineProperty(window, 'location', {
    configurable: true,
    value: { replace: (u: string) => replaced.push(u), href: 'http://localhost/' },
  });
  writeImpersonation({ tenantId: 'dead-shop', name: 'Deleted Shop' });
});

afterEach(() => {
  api.defaults.adapter = savedAdapter;
  sessionStorage.clear();
  vi.restoreAllMocks();
});

describe('a dead tenant scope', () => {
  it('is dropped when the impersonated tenant no longer exists', async () => {
    api.defaults.adapter = failWith(404, 'tenant_not_found');
    await expect(api.get('/tenant/overview')).rejects.toBeTruthy();

    expect(readImpersonation()).toBeNull();
    expect(replaced).toEqual(['/']);
  });

  it('is NOT dropped on no_tenant_membership — that is the ambiguous one', async () => {
    // THE OUTAGE (production, 2026-08-12). This used to expect the opposite.
    // `no_tenant_membership` is `resolve_tenant`'s catch-all: it is what you get
    // when a request carried NO `X-Tenant-Id` at all, not a statement that the
    // shop is gone. Treating it as a dead scope meant one scope-less request
    // threw the operator out of a healthy shop — every time they tried to open
    // any shop's panel.
    api.defaults.adapter = failWith(404, 'no_tenant_membership');
    await expect(api.get('/tenant/customers')).rejects.toBeTruthy();

    expect(readImpersonation()).not.toBeNull();
    expect(replaced).toEqual([]);
  });

  it('is NOT dropped when the request never carried the scope', async () => {
    // If we did not ask about a tenant, a 404 cannot be an answer about it.
    api.defaults.adapter = async (config) => {
      (config.headers as { delete?: (k: string) => void }).delete?.('X-Tenant-Id');
      return Promise.reject(Object.assign(new Error('boom'), {
        isAxiosError: true, config,
        response: { status: 404, data: { detail: 'tenant_not_found' }, headers: {}, config, statusText: '' },
      }));
    };
    await expect(api.get('/tenant/overview')).rejects.toBeTruthy();

    expect(readImpersonation()).not.toBeNull();
    expect(replaced).toEqual([]);
  });

  it('also covers the staff surfaces, which share the same scope', async () => {
    api.defaults.adapter = failWith(404, 'tenant_not_found');
    await expect(api.get('/staff/today')).rejects.toBeTruthy();

    expect(readImpersonation()).toBeNull();
  });
});

describe('an ordinary 404 is left alone', () => {
  it('does not treat a deleted customer as a dead scope', async () => {
    // The narrow condition is the whole point: widening it would turn every
    // "this contract is gone" into a full app restart.
    api.defaults.adapter = failWith(404, 'customer_not_found');
    await expect(api.get('/tenant/customers/abc')).rejects.toBeTruthy();

    expect(readImpersonation()).not.toBeNull();
    expect(replaced).toEqual([]);
  });

  it('does not fire on a platform endpoint', async () => {
    api.defaults.adapter = failWith(404, 'tenant_not_found');
    await expect(api.get('/platform/tenants/abc/detail')).rejects.toBeTruthy();

    expect(readImpersonation()).not.toBeNull();
    expect(replaced).toEqual([]);
  });

  it('does not fire when nobody is impersonating', async () => {
    // An ordinary owner's own shop 404ing is a server-side problem to surface,
    // not a client-side scope to discard.
    writeImpersonation(null);
    api.defaults.adapter = failWith(404, 'tenant_not_found');
    await expect(api.get('/tenant/overview')).rejects.toBeTruthy();

    expect(replaced).toEqual([]);
  });

  it('does not fire on a non-404 failure', async () => {
    api.defaults.adapter = failWith(500, 'boom');
    await expect(api.get('/tenant/overview')).rejects.toBeTruthy();

    expect(readImpersonation()).not.toBeNull();
    expect(replaced).toEqual([]);
  });
});
