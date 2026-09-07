// @vitest-environment jsdom
/**
 * MUDD-149 item 2 — the same defect class `startParamPersonaGuard.test.tsx`
 * covers for a customer session survives one persona over: a STAFF session
 * tapping a `tinst_<id>` / `billing` deep link (e.g. forwarded from a shop
 * channel overdue alert, which staff routinely also read).
 *
 * Before this fix, `PersonaGuard` on `/:locale/tenant/*` (allow: ['tenant'])
 * bounced a 'staff' persona to `personaHome('staff', locale)` = `/staff/today`
 * exactly like it would a customer — but unlike `billing` (genuinely
 * owner-only), the contract screen HAS a staff-visible equivalent at
 * `/staff/installments` (`SalesPage`, wired to the same `?installment=`
 * query param). Losing that destination to the generic bounce is the bug.
 *
 * These tests assert on the staff-SESSION NAVIGATION (through the real
 * `AppRouter` + `PersonaGuard`), not the decode — `deepLinks.test.ts` already
 * proves `startParamToPath` maps `tinst_<id>` / `billing` correctly.
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
      resolvePersona: () => 'staff' as const,
    };
    return sel ? sel(state) : state;
  },
}));

const toastInfo = vi.fn();
vi.mock('@/lib/feedback', () => ({
  toast: { info: toastInfo, success: vi.fn(), error: vi.fn(), warning: vi.fn() },
}));

const { AppRouter } = await import('./router');

function setStartParam(param: string | undefined) {
  (window as unknown as { Telegram?: unknown }).Telegram = param
    ? { WebApp: { initDataUnsafe: { start_param: param } } }
    : undefined;
}

function Trace() {
  const { pathname, search } = useLocation();
  return <div data-testid="here">{pathname}{search}</div>;
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
  toastInfo.mockClear();
});

afterEach(() => {
  cleanup();
  setStartParam(undefined);
});

describe('a staff session tapping a shop-channel deep link lands on a real screen, not a dead bounce', () => {
  it('reaches /staff/installments (with the id preserved) instead of the owner-only tenant screen', async () => {
    const id = '66666666-6666-6666-6666-666666666666';
    visit('/uz/somewhere-else', `tinst_${id}`);
    await waitFor(() =>
      expect(screen.getByTestId('here').textContent).toBe(`/uz/staff/installments?installment=${id}`),
    );
  });

  it('bounces a billing deep link to the staff home — with a notice, not a silent no-op', async () => {
    visit('/uz/somewhere-else', 'billing');
    await waitFor(() => expect(screen.getByTestId('here').textContent).toBe('/uz/staff/today'));
    expect(toastInfo).toHaveBeenCalledTimes(1);
  });
});
