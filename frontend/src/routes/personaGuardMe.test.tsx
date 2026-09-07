// @vitest-environment jsdom
/**
 * M-22 (MUDD-68) — every privileged shell is wrapped in `PersonaGuard` except
 * `/:locale/me/*`: platform, tenant and staff all redirect a mismatched
 * persona home, but a tenant owner (or anyone else) hitting `/me/*` used to
 * render the customer shell regardless of their real persona.
 */
import { describe, expect, it, afterEach, beforeEach, vi } from 'vitest';
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

let persona: 'platform' | 'tenant' | 'staff' | 'client' = 'client';
vi.mock('@/store/session', () => ({
  useSession: (sel?: (s: unknown) => unknown) => {
    const state = {
      me: ME, activeTenantId: null, impersonating: false,
      resolvePersona: () => persona,
    };
    return sel ? sel(state) : state;
  },
}));

const { AppRouter } = await import('./router');

beforeEach(() => i18n.changeLanguage('uz'));
afterEach(cleanup);

function Trace() {
  const { pathname } = useLocation();
  return <div data-testid="here">{pathname}</div>;
}

function visit(path: string) {
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

describe('/:locale/me/* is guarded like every other persona shell', () => {
  it('a client persona reaches the customer shell', async () => {
    persona = 'client';
    visit('/uz/me/my');
    await waitFor(() => expect(screen.getByTestId('page')).toBeTruthy());
    expect(screen.getByTestId('here').textContent).toBe('/uz/me/my');
  });

  it('a tenant owner is bounced to their own home, never sees the customer shell', async () => {
    persona = 'tenant';
    visit('/uz/me/my');
    await waitFor(() => expect(screen.getByTestId('here').textContent).toBe('/uz/tenant/overview'));
  });

  it('a platform admin is bounced to their own home too', async () => {
    persona = 'platform';
    visit('/uz/me/profile');
    await waitFor(() => expect(screen.getByTestId('here').textContent).toBe('/uz/platform/overview'));
  });

  it('a staff member is bounced to their own home too', async () => {
    persona = 'staff';
    visit('/uz/me/privacy');
    await waitFor(() => expect(screen.getByTestId('here').textContent).toBe('/uz/staff/today'));
  });
});
