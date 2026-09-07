// @vitest-environment jsdom
/**
 * MUDD-148 — item 4 of the ticket: what happens when a customer-role session
 * receives a `/tenant/*` deep link (e.g. a forwarded `tinst_<id>` message)?
 *
 * `useStartParamRedirect` (deepLinks.ts) no longer gates navigation by a
 * hardcoded prefix allow-list — it trusts `startParamToPath` and navigates to
 * whatever it decodes, including `/tenant/*` targets. That does NOT create an
 * authorization hole: this test proves the round trip through the REAL
 * `AppRouter` — the hook navigates to the tenant screen, `PersonaGuard` on
 * `/:locale/tenant/*` (allow: ['tenant']) immediately rejects a 'client'
 * persona, and the customer lands on their own persona home
 * (`personaHome('client', locale)` = `/uz/me/my`) instead of ever rendering
 * the tenant shell.
 */
import { describe, it, expect, afterEach, beforeEach, vi } from 'vitest';
import { render, screen, cleanup, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter, useLocation } from 'react-router';
import i18n from '@/i18n';

vi.mock('@/lib/lazyWithRetry', () => ({
  lazyWithRetry: () => function StubPage() { return <div data-testid="page" />; },
}));

vi.mock('@/lib/api', async (orig) => ({
  ...(await orig<Record<string, unknown>>()),
  hasAuthFlag: () => true,
}));

const ME = { id: 'u1' };
vi.mock('@/hooks/useMe', () => ({ useMe: () => ({ data: ME, isLoading: false, isError: false }) }));

vi.mock('@/store/session', () => ({
  useSession: (sel?: (s: unknown) => unknown) => {
    const state = {
      me: ME, activeTenantId: null, impersonating: false,
      resolvePersona: () => 'client' as const,
    };
    return sel ? sel(state) : state;
  },
}));

const { AppRouter } = await import('./router');

function setStartParam(param: string | undefined) {
  (window as unknown as { Telegram?: unknown }).Telegram = param
    ? { WebApp: { initDataUnsafe: { start_param: param } } }
    : undefined;
}

function Trace() {
  const { pathname } = useLocation();
  return <div data-testid="here">{pathname}</div>;
}

function visit(path: string, startParam?: string) {
  setStartParam(startParam);
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={[path]}>
        <AppRouter />
        <Trace />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  i18n.changeLanguage('uz');
  sessionStorage.clear();
});

afterEach(() => {
  cleanup();
  setStartParam(undefined);
});

describe('a customer session tapping a tenant deep link never reaches the tenant shell', () => {
  it('bounces to the client persona home instead of rendering /tenant/installments', async () => {
    const id = '33333333-3333-3333-3333-333333333333';
    visit('/uz/somewhere-else', `tinst_${id}`);
    await waitFor(() => expect(screen.getByTestId('here').textContent).toBe('/uz/me/my'));
    expect(screen.queryByTestId('here')?.textContent).not.toContain('/tenant/');
  });

  it('bounces away from a billing_deeplink too', async () => {
    visit('/uz/somewhere-else', 'billing');
    await waitFor(() => expect(screen.getByTestId('here').textContent).toBe('/uz/me/my'));
  });
});
