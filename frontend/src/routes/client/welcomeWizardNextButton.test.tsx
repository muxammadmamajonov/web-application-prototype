// @vitest-environment jsdom
/**
 * MUDD-147 — the business-onboarding wizard's footer button
 * (`ClientWelcomePage` → `BusinessWizard`) fed `Button`'s `loading` prop
 * `!canNext || busy`. `canNext` is pure step validation (e.g. step 0 needs a
 * business name), not a request in flight, so an incomplete step announced
 * the button as `aria-busy="true"` forever — this is the customer-visible
 * screen named first in the review: every incomplete onboarding step showed
 * a permanent spinner and told screen readers the page was busy.
 */
import { describe, expect, it, vi, beforeEach, afterEach } from 'vitest';
import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import '@testing-library/jest-dom/vitest';
import { MemoryRouter } from 'react-router';
import i18n from '@/i18n';

vi.mock('@/lib/queries', async (orig) => ({
  ...(await orig<Record<string, unknown>>()),
  useMySummary: () => ({ data: { installments_count: 0 }, isLoading: false }),
  useOnboardingOptions: () => ({ data: { business_types: [], how_found_options: [] }, isLoading: false }),
  usePublicPlans: () => ({ data: [], isLoading: false }),
  useSupportChannels: () => ({ data: { support: 'muddatosupport' }, isLoading: false }),
}));

vi.mock('@/store/session', () => ({
  useSession: (sel?: (s: unknown) => unknown) => {
    const state = { me: { id: 'u1', tenants: [] } };
    return sel ? sel(state) : state;
  },
}));

const { default: ClientWelcomePage } = await import('./WelcomePage');

function open() {
  return render(
    <MemoryRouter initialEntries={['/uz/welcome']}>
      <ClientWelcomePage />
    </MemoryRouter>,
  );
}

const nextBtn = () => screen.getByRole('button', { name: /keyingisi|demoni boshlash/i }) as HTMLButtonElement;

beforeEach(async () => { await i18n.changeLanguage('uz'); });
afterEach(cleanup);

describe('business onboarding wizard — Next button', () => {
  it('step 0: is disabled with an empty business name but never reads as busy', () => {
    open();
    fireEvent.click(screen.getByText(i18n.t('welcome.business.cta') as string));
    expect(nextBtn().disabled).toBe(true);
    expect(nextBtn()).not.toHaveAttribute('aria-busy', 'true');
  });

  it('step 0: enables once a name is typed, still without a spurious busy state', () => {
    open();
    fireEvent.click(screen.getByText(i18n.t('welcome.business.cta') as string));
    fireEvent.change(screen.getByPlaceholderText(/Demo Electronics/), { target: { value: 'Mening doʼkonim' } });
    expect(nextBtn().disabled).toBe(false);
    expect(nextBtn()).not.toHaveAttribute('aria-busy', 'true');
  });
});
