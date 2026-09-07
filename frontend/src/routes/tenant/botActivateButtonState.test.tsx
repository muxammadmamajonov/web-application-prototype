// @vitest-environment jsdom
/**
 * MUDD-147 — the own-bot activation button fed `Button`'s `loading` prop
 * `activate.isPending || !token`. The second half is pure form validation
 * (no token typed yet), not a request in flight, so an empty token field
 * announced the button as `aria-busy="true"` forever — and once a real
 * activation WAS pending, a manual `{activate.isPending && <Loader2/>}` in
 * the children stacked a second spinner on top of `Button`'s own.
 *
 * This surface sits behind the `V2_SURFACES_ENABLED` scope flag (currently
 * `false` — see `routes/_scope.ts`), so it is unreachable in the shipped
 * bundle today; the flag is overridden here to exercise the real component
 * rather than skip it, since the code (and the fix) still ships and would
 * regress silently otherwise.
 *
 * `SettingsShell` renders its selected tab's content twice in the DOM (a
 * desktop master-detail pane and a mobile drill-in pane — CSS, not React,
 * decides which is visible), so both queries below pick the first match and
 * stay on that same instance throughout.
 */
import { describe, expect, it, vi, beforeEach, afterEach } from 'vitest';
import { cleanup, render, screen } from '@testing-library/react';
import '@testing-library/jest-dom/vitest';
import { fireEvent } from '@testing-library/react';
import { MemoryRouter } from 'react-router';
import i18n from '@/i18n';
import type { TenantSettingsBundle } from '@/lib/queries';

vi.mock('@/routes/_scope', async (orig) => ({
  ...(await orig<Record<string, unknown>>()),
  V2_SURFACES_ENABLED: true,
}));

const DATA = {
  bot: { bot_mode: 'shared', bot_username: null, bot_activated_at: null, plan_allows_bot: true },
} as unknown as TenantSettingsBundle;

vi.mock('@/lib/queries', async (orig) => ({
  ...(await orig<Record<string, unknown>>()),
  useTenantSettings: () => ({ data: DATA, isLoading: false, error: null, refetch: vi.fn() }),
}));

let activateIsPending = false;
vi.mock('@/lib/mutations', async (orig) => ({
  ...(await orig<Record<string, unknown>>()),
  useActivateTenantBot: () => ({ mutateAsync: vi.fn(), isPending: activateIsPending }),
  useDeactivateTenantBot: () => ({ mutateAsync: vi.fn(), isPending: false }),
}));

const { default: TenantSettingsPage } = await import('./SettingsPage');

function open() {
  return render(
    <MemoryRouter initialEntries={['/uz/tenant/settings?tab=bot']}>
      <TenantSettingsPage />
    </MemoryRouter>,
  );
}

const tokenInput = () => screen.getAllByPlaceholderText(/AAEhBOweik6ad6PsXxa0F39mLn/)[0] as HTMLInputElement;
const activateBtn = () => screen.getAllByRole('button', { name: /aktivlashtirish/i })[0] as HTMLButtonElement;

beforeEach(async () => { activateIsPending = false; await i18n.changeLanguage('uz'); });
afterEach(cleanup);

describe('bot activate button', () => {
  it('is disabled with an empty token but never reads as busy', () => {
    open();
    expect(activateBtn().disabled).toBe(true);
    expect(activateBtn()).not.toHaveAttribute('aria-busy', 'true');
  });

  it('enables once a token is typed, still without a spurious busy state', () => {
    open();
    fireEvent.change(tokenInput(), { target: { value: '123456789:AAEtest' } });
    expect(activateBtn().disabled).toBe(false);
    expect(activateBtn()).not.toHaveAttribute('aria-busy', 'true');
  });

  it('shows exactly one spinner while the activation is actually pending', () => {
    activateIsPending = true;
    open();
    const btn = activateBtn();
    expect(btn).toHaveAttribute('aria-busy', 'true');
    expect(btn.querySelectorAll('.animate-spin').length).toBe(1);
  });
});
