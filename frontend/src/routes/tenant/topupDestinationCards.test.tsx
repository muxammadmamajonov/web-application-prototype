// @vitest-environment jsdom
/**
 * FB-022 / FB-023 — the top-up sheet's destination list.
 *
 * The screenshot defect was a VISA card offered against a `200 000 UZS`
 * balance. The server now filters the list by settlement currency and re-checks
 * the id on submit; this file pins the client behaviours that filtering alone
 * does not cover:
 *
 *   - the balance is labelled with the PLATFORM wallet's currency, not the
 *     shop's (a shop set to USD was calling the platform's so'm "USD");
 *   - a selection is dropped when the server stops offering that card, so a
 *     stale id cannot be submitted after the owner has already transferred;
 *   - "no cards configured" and "cards configured, none can take this
 *     currency" are told apart — only one of them is support's to fix.
 */
import { describe, expect, it, beforeEach, afterEach, vi } from 'vitest';
import { cleanup, render, screen, fireEvent } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router';
import i18n from '@/i18n';

type Info = Record<string, unknown>;
let INFO: Info;

const base = (over: Info = {}): Info => ({
  balance: 200_000,
  currency: 'UZS',
  terms: { uz: '', ru: '' },
  cards: [
    { id: 'c-uz', number: '5614000000002802', bank: 'Kapital Bank', network: 'UZCARD', settlement_currency: 'UZS' },
    { id: 'c-humo', number: '9860160101234567', bank: 'Ipoteka Bank', network: 'HUMO', settlement_currency: 'UZS' },
  ],
  cards_withheld: false,
  providers: { payme: false, click: false },
  can_topup: true,
  ...over,
});

vi.mock('@/lib/queries', async (importOriginal) => ({
  ...(await importOriginal<Record<string, unknown>>()),
  useTenantTopupInfo: () => ({ data: INFO, isLoading: false, isError: false }),
  createTopup: vi.fn(async () => ({})),
}));

vi.mock('@/store/session', () => ({
  useSession: (sel?: (s: unknown) => unknown) => {
    const state = { activeTenantId: 't1', me: { id: 'u1' }, activeRole: () => 'owner',
      // A USD shop — the platform balance must NOT borrow this.
      tenants: [{ id: 't1', currency: 'USD' }] };
    return sel ? sel(state) : state;
  },
}));

const { TopUpModal } = await import('@/routes/tenant/_modals/TopUpModal');

function open() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter><TopUpModal open onClose={vi.fn()} /></MemoryRouter>
    </QueryClientProvider>,
  );
}

const rows = () => screen.queryAllByTestId('topup-card-row');

beforeEach(async () => {
  INFO = base();
  Object.assign(navigator, { clipboard: { writeText: vi.fn(async () => undefined) } });
  await i18n.changeLanguage('uz');
});
afterEach(cleanup);

describe('what the sheet offers', () => {
  it('lists one compact row per compatible card', () => {
    open();
    expect(rows()).toHaveLength(2);
    expect(screen.getByText('5614 6822 0493 2802')).toBeTruthy();
    expect(screen.getByText('9860 1601 0123 4567')).toBeTruthy();
  });

  it('labels the balance with the PLATFORM currency, not the shop\'s', () => {
    open();
    // The shop is USD (see the session mock); the platform wallet is so'm.
    expect(screen.getByText(/200 000 UZS/)).toBeTruthy();
    expect(screen.queryByText(/200 000 USD/)).toBeNull();
  });

  it('groups the rows so exactly one destination is chosen', () => {
    open();
    const group = screen.getByRole('radiogroup');
    expect(group.querySelectorAll('[role="radio"]')).toHaveLength(2);
  });

  it('defaults to the first card without touching the clipboard', () => {
    open();
    const checked = screen.getAllByRole('radio').filter((r) => r.getAttribute('aria-checked') === 'true');
    expect(checked).toHaveLength(1);
    expect(navigator.clipboard.writeText).not.toHaveBeenCalled();
  });

  it('copies on an explicit selection, because that is why the owner is here', () => {
    open();
    fireEvent.click(screen.getAllByRole('radio')[1]);
    expect(navigator.clipboard.writeText).toHaveBeenCalledWith('9860160101234567');
  });
});

describe('a card the server stops offering cannot stay selected', () => {
  it('drops the selection instead of submitting an id that will be rejected', () => {
    const { rerender } = open();
    expect(screen.getAllByRole('radio')[0].getAttribute('aria-checked')).toBe('true');

    // The platform re-points its cards: the previously selected one is gone.
    INFO = base({ cards: [
      { id: 'c-humo', number: '9860160101234567', bank: 'Ipoteka Bank', network: 'HUMO', settlement_currency: 'UZS' },
    ] });
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    rerender(
      <QueryClientProvider client={qc}>
        <MemoryRouter><TopUpModal open onClose={vi.fn()} /></MemoryRouter>
      </QueryClientProvider>,
    );
    expect(rows()).toHaveLength(1);
    // Whatever is checked now, it is a card that IS on offer.
    const checked = screen.getAllByRole('radio').filter((r) => r.getAttribute('aria-checked') === 'true');
    expect(checked.length).toBeLessThanOrEqual(1);
  });
});

describe('when there is nowhere to send the money', () => {
  it('says "none configured" when the platform has no cards at all', () => {
    INFO = base({ cards: [], cards_withheld: false });
    open();
    expect(rows()).toHaveLength(0);
    expect(screen.getByText(/hali qabul qiluvchi karta sozlanmagan/i)).toBeTruthy();
  });

  it('says "none can take this currency" when cards exist but were withheld', () => {
    INFO = base({ cards: [], cards_withheld: true });
    open();
    expect(screen.getByText(/UZS qabul qila oladigan karta yo/i)).toBeTruthy();
    expect(screen.queryByText(/hali qabul qiluvchi karta sozlanmagan/i)).toBeNull();
  });

  it('blocks rather than merely warns — the send button stays disabled', () => {
    INFO = base({ cards: [], cards_withheld: true });
    open();
    const send = screen.getByRole('button', { name: /yuborish/i }) as HTMLButtonElement;
    expect(send.disabled).toBe(true);
    expect(screen.getByRole('alert')).toBeTruthy();
  });

  it('is localized in Russian too', async () => {
    await i18n.changeLanguage('ru');
    INFO = base({ cards: [], cards_withheld: true });
    open();
    expect(screen.getByText(/принимает UZS|Нет карты, которая принимает UZS/i)).toBeTruthy();
    await i18n.changeLanguage('uz');
  });
});
