// @vitest-environment jsdom
/**
 * MUDD-148 — `useStartParamRedirect` decodes every kind `startParamToPath`
 * can produce, but used to navigate only when the target matched a hardcoded
 * prefix allow-list (`/${lng}/me`, `/i/`, `/r/`). The decoder later gained
 * `tinst_<id>` (tenant contract) and `billing` (tenant billing), both of
 * which resolve to `/tenant/*` — outside that list — so tapping either link
 * computed the right destination and silently never navigated.
 *
 * These tests assert on the NAVIGATION, not the decode: `deepLinks.test.ts`
 * already proves `startParamToPath` maps these params correctly, and stayed
 * green throughout this bug because the gap was between decode and navigate.
 */
import { describe, it, expect, beforeEach, afterEach } from 'vitest';
import { render, screen, cleanup, waitFor } from '@testing-library/react';
import { MemoryRouter, Routes, Route, useLocation } from 'react-router';
import i18n from '@/i18n';
import { useStartParamRedirect } from './deepLinks';

function setStartParam(param: string | undefined) {
  (window as unknown as { Telegram?: unknown }).Telegram = param
    ? { WebApp: { initDataUnsafe: { start_param: param } } }
    : undefined;
}

function Harness() {
  useStartParamRedirect();
  const { pathname, search } = useLocation();
  return <div data-testid="here">{pathname}{search}</div>;
}

function visit(path: string, startParam?: string) {
  setStartParam(startParam);
  return render(
    <MemoryRouter initialEntries={[path]}>
      <Routes>
        <Route path="/:locale/*" element={<Harness />} />
      </Routes>
    </MemoryRouter>,
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

describe('useStartParamRedirect navigates every kind the decoder can produce', () => {
  it('navigates a tenant_installment_deeplink (tinst_<id>) to the tenant contract screen', async () => {
    const id = '11111111-1111-1111-1111-111111111111';
    visit('/uz/somewhere-else', `tinst_${id}`);
    await waitFor(() =>
      expect(screen.getByTestId('here').textContent).toBe(
        `/uz/tenant/installments?installment=${id}`,
      ),
    );
  });

  it('navigates a billing_deeplink (billing) to the tenant billing screen', async () => {
    visit('/uz/somewhere-else', 'billing');
    await waitFor(() =>
      expect(screen.getByTestId('here').textContent).toBe('/uz/tenant/billing'),
    );
  });

  it('still navigates the pre-existing /me shortcuts (no regression from loosening the gate)', async () => {
    visit('/uz/somewhere-else', 'home');
    await waitFor(() =>
      expect(screen.getByTestId('here').textContent).toBe('/uz/me/home'),
    );
  });

  it('still navigates invitation and review-intent params outside /me', async () => {
    visit('/uz/somewhere-else', 'A7K3QX');
    await waitFor(() =>
      expect(screen.getByTestId('here').textContent).toBe('/i/A7K3QX'),
    );
  });

  it('keeps the loop guard: never navigates when already on the target path', async () => {
    const id = '22222222-2222-2222-2222-222222222222';
    // Same pathname as the decoded target, different query — the guard
    // compares `target.split('?')[0]` against `pathname`, so this must be
    // left untouched rather than overwritten.
    visit(`/uz/tenant/installments?other=1`, `tinst_${id}`);
    await waitFor(() =>
      expect(screen.getByTestId('here').textContent).toBe('/uz/tenant/installments?other=1'),
    );
  });

  it('does nothing when there is no start param', async () => {
    visit('/uz/somewhere-else', undefined);
    await waitFor(() =>
      expect(screen.getByTestId('here').textContent).toBe('/uz/somewhere-else'),
    );
  });
});
