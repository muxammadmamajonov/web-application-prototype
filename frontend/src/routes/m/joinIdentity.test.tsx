// @vitest-environment jsdom
/**
 * The join form must not ask for what the server already knows, and must never
 * accept a typed number in place of proved ownership.
 *
 * The endpoint and the card existed before this file; JoinPage used neither, so
 * the flow was still "type your name and phone" for everybody. These tests fail
 * if the preflight is dropped or an editable phone comes back.
 */
import { describe, expect, it, beforeEach, afterEach, vi } from 'vitest';
import { cleanup, render, screen, act, fireEvent, waitFor } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router';

// (MUDD-71 H3) These components read the support handle through
// `useSupportUsername`, which resolves it from platform settings via TanStack
// Query — the handle is operator-configurable, so a build-time constant could
// not be the source. That makes a QueryClientProvider part of the environment
// they need, exactly as it is in the real tree (`main.tsx`). `retry: false` so a
// test never waits on a retry schedule for a request nothing here asserts on.
function withQuery(ui: ReactNode): ReactNode {
  return (
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      {ui}
    </QueryClientProvider>
  );
}

import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import type { ReactNode } from 'react';
import i18n from '@/i18n';

const get = vi.fn();
const post = vi.fn();
vi.mock('@/lib/api', () => ({
  api: { get: (...a: unknown[]) => get(...a), post: (...a: unknown[]) => post(...a) },
  apiErrorMessage: () => 'error',
  setSessionToken: () => {},
}));

const requestContact = vi.fn<() => Promise<'sent' | 'cancelled' | 'unsupported'>>();
vi.mock('@/lib/telegram/permissions', () => ({
  requestContact: () => requestContact(),
  requestWriteAccessOnce: () => {},
}));

vi.mock('@/lib/telegram', async (orig) => ({
  ...(await orig<Record<string, unknown>>()),
  isInsideTelegramClient: () => true,
  getTelegramWebApp: () => ({
    ready: () => {}, expand: () => {},
    initData: 'user=%7B%22id%22%3A7%7D&hash=abc',
    initDataUnsafe: { user: { id: 7, first_name: 'Jasur' } },
  }),
}));

const JoinPage = (await import('@/routes/m/JoinPage')).default;

const CTX = { kind: 'customer', tenant_name: 'Shop', tenant_slug: 's', tenant_logo: null,
              role: null, preset: {}, custom_fields: [] };
const UNVERIFIED = { state: 'VERIFICATION_REQUIRED', verified: false, phoneMasked: null,
                     matchesInvitation: null, knownName: null, telegramName: 'Jasur' };
const VERIFIED = { state: 'VERIFIED', verified: true, phoneMasked: '+998•••••••33',
                   matchesInvitation: true, knownName: 'Jasur T', telegramName: 'Jasur' };
const MISMATCH = { state: 'PHONE_DOES_NOT_MATCH_INVITATION', verified: true,
                   phoneMasked: '+998•••••••44', matchesInvitation: false,
                   knownName: null, telegramName: 'Jasur' };
const FOREIGN = { state: 'UNSUPPORTED_REGION', verified: true, phoneMasked: '+1••••••71',
                  matchesInvitation: null, knownName: null, telegramName: 'Jasur' };

function mount() {
  return render(withQuery(
    <MemoryRouter initialEntries={['/i/ABC123']}>
      <Routes><Route path="/i/:token" element={<JoinPage />} /></Routes>
    </MemoryRouter>,
  ));
}

const card = () => document.querySelector('[data-testid="identity-card"]') as HTMLElement | null;
const submit = () => document.querySelector('[data-testid="join-submit"]') as HTMLButtonElement;
const phoneInputs = () => [...document.querySelectorAll('input')]
  .filter((i) => i.type === 'tel' || (i.inputMode === 'numeric' && /\+998/.test(i.placeholder || '')));

beforeEach(async () => {
  await i18n.changeLanguage('uz');
  vi.clearAllMocks();
  get.mockResolvedValue({ data: CTX });
  post.mockResolvedValue({ data: UNVERIFIED });
  requestContact.mockResolvedValue('sent');
});
afterEach(cleanup);

describe('the preflight runs before the identity fields render', () => {
  it('asks the server what it already knows', async () => {
    mount();
    await waitFor(() => expect(post).toHaveBeenCalled());
    const [url, body] = post.mock.calls[0]!;
    expect(url).toBe('/inv/ABC123/identity');
    // Server-side validation of the caller, not a client claim about them.
    expect(body).toHaveProperty('init_data');
  });

  it('runs the preflight for a staff invitation too', async () => {
    // This asserted the opposite until the phone-identity normalisation. It
    // skipped the preflight for staff, and the staff branch of the form then
    // asked for a phone number in a text box which the server wrote onto the
    // joining account — so an invitation link was a way to TYPE an account's
    // identity, beside whatever Telegram had already proved for it.
    //
    // The rationale the old test gave still holds and is still honoured: staff
    // are authorised by whoever invited them, and the customer REGION rule does
    // not leak onto that path (see `ownershipOnly` in JoinPage, and the server's
    // matching bar in `invitations.py`). What does apply to every kind is
    // ownership, because the account's number is now its identity — and the
    // preflight is how ownership is established.
    get.mockResolvedValue({ data: { ...CTX, kind: 'staff' } });
    mount();
    await waitFor(() => expect(post).toHaveBeenCalled());
    expect(post.mock.calls[0]![0]).toBe('/inv/ABC123/identity');
  });
});

describe('a proved number is shown, never asked for', () => {
  it('renders a masked read-only value and no phone input', async () => {
    post.mockResolvedValue({ data: VERIFIED });
    mount();
    await waitFor(() => expect(screen.queryByTestId('verified-phone')).toBeTruthy());
    expect(screen.getByTestId('verified-phone').textContent).toContain('+998•••••••33');
    expect(phoneInputs()).toHaveLength(0);
    expect(card()).toBeNull();
  });

  it('never renders the full number', async () => {
    post.mockResolvedValue({ data: VERIFIED });
    mount();
    await waitFor(() => expect(screen.queryByTestId('verified-phone')).toBeTruthy());
    expect(document.body.textContent).not.toMatch(/\+?998\d{9}/);
  });

  it('allows submission, and sends no phone at all', async () => {
    post.mockResolvedValue({ data: VERIFIED });
    mount();
    await waitFor(() => expect(submit()).toBeTruthy());
    expect(submit().disabled).toBe(false);
    fireEvent.change(screen.getByDisplayValue('Jasur'), { target: { value: 'Jasur Toshmatov' } });
    post.mockResolvedValueOnce({ data: { ok: true, token: 't' } });
    await act(async () => { fireEvent.click(submit()); });
    const claim = post.mock.calls.find(([u]) => String(u).endsWith('/claim'));
    expect(claim).toBeTruthy();
    // The payload cannot contradict the proof, because it carries no phone.
    expect(claim![1]).not.toHaveProperty('phone');
  });
});

describe('an unproved visitor is asked to prove, not to type', () => {
  it('renders the verification card instead of a phone field', async () => {
    mount();
    await waitFor(() => expect(card()).toBeTruthy());
    expect(card()!.dataset.state).toBe('VERIFICATION_REQUIRED');
    expect(phoneInputs()).toHaveLength(0);
  });

  it('blocks submission until the server says otherwise', async () => {
    mount();
    await waitFor(() => expect(submit()).toBeTruthy());
    expect(submit().disabled).toBe(true);
  });

  it('polls THIS invitation, not the account endpoint', async () => {
    vi.useFakeTimers();
    mount();
    await act(async () => { await vi.advanceTimersByTimeAsync(50); });
    await act(async () => { fireEvent.click(card()!.querySelector('button')!); });
    post.mockResolvedValue({ data: VERIFIED });
    await act(async () => { await vi.advanceTimersByTimeAsync(2_000); });
    // `/me/identity` knows the account but not this invitation, so it cannot
    // report a mismatch — the state that matters most here.
    expect(post.mock.calls.every(([u]) => !String(u).includes('/me/identity'))).toBe(true);
    expect(post.mock.calls.some(([u]) => String(u) === '/inv/ABC123/identity')).toBe(true);
    vi.useRealTimers();
  });

  it('unlocks the form when the contact update lands', async () => {
    vi.useFakeTimers();
    mount();
    await act(async () => { await vi.advanceTimersByTimeAsync(50); });
    expect(submit().disabled).toBe(true);
    await act(async () => { fireEvent.click(card()!.querySelector('button')!); });
    post.mockResolvedValue({ data: VERIFIED });
    await act(async () => { await vi.advanceTimersByTimeAsync(4_000); });
    expect(submit().disabled).toBe(false);
    vi.useRealTimers();
  });

  it('a cancelled prompt keeps the form blocked and offers a retry', async () => {
    vi.useFakeTimers();
    requestContact.mockResolvedValue('cancelled');
    mount();
    await act(async () => { await vi.advanceTimersByTimeAsync(50); });
    await act(async () => { fireEvent.click(card()!.querySelector('button')!); });
    expect(card()!.dataset.state).toBe('cancelled');
    expect(submit().disabled).toBe(true);
    expect(card()!.querySelector('button')).toBeTruthy();
    vi.useRealTimers();
  });

  it('an old Telegram is explained and still cannot submit', async () => {
    vi.useFakeTimers();
    requestContact.mockResolvedValue('unsupported');
    mount();
    await act(async () => { await vi.advanceTimersByTimeAsync(50); });
    await act(async () => { fireEvent.click(card()!.querySelector('button')!); });
    expect(card()!.dataset.state).toBe('unsupported');
    expect(submit().disabled).toBe(true);
    vi.useRealTimers();
  });

  it('a wait that never resolves times out and can be retried', async () => {
    vi.useFakeTimers();
    mount();
    await act(async () => { await vi.advanceTimersByTimeAsync(50); });
    await act(async () => { fireEvent.click(card()!.querySelector('button')!); });
    for (let i = 0; i < 25; i += 1) {
      await act(async () => { await vi.advanceTimersByTimeAsync(2_000); });
    }
    expect(card()!.dataset.state).toBe('timeout');
    expect(submit().disabled).toBe(true);
    vi.useRealTimers();
  });
});

describe('states the server alone can report', () => {
  it('a mismatched proof blocks the form and says why', async () => {
    post.mockResolvedValue({ data: MISMATCH });
    mount();
    await waitFor(() => expect(card()).toBeTruthy());
    expect(card()!.dataset.state).toBe('PHONE_DOES_NOT_MATCH_INVITATION');
    expect(card()!.textContent).toContain(i18n.t('identity.state.PHONE_DOES_NOT_MATCH_INVITATION'));
    expect(submit().disabled).toBe(true);
    expect(screen.queryByTestId('verified-phone')).toBeNull();
  });

  it('a verified foreign number cannot complete a customer invitation', async () => {
    post.mockResolvedValue({ data: FOREIGN });
    mount();
    await waitFor(() => expect(card()).toBeTruthy());
    expect(card()!.dataset.state).toBe('UNSUPPORTED_REGION');
    expect(submit().disabled).toBe(true);
  });

  it('an invitation consumed while waiting stops the form', async () => {
    post.mockResolvedValue({ data: UNVERIFIED });
    mount();
    await waitFor(() => expect(card()).toBeTruthy());
    post.mockRejectedValue({ response: { status: 410 } });
    await act(async () => { fireEvent.click(card()!.querySelector('button')!); });
    await waitFor(() => expect(submit().disabled).toBe(true));
  });
});

describe('reload resumes from the server', () => {
  it('re-reads the state rather than trusting anything local', async () => {
    post.mockResolvedValue({ data: VERIFIED });
    const first = mount();
    await waitFor(() => expect(screen.queryByTestId('verified-phone')).toBeTruthy());
    first.unmount();
    vi.clearAllMocks();
    get.mockResolvedValue({ data: CTX });
    post.mockResolvedValue({ data: UNVERIFIED });
    mount();
    // The server changed its mind; the form follows it, not a cached success.
    await waitFor(() => expect(card()).toBeTruthy());
    expect(submit().disabled).toBe(true);
  });
});

describe('localization', () => {
  it.each(['uz', 'ru'])('renders its states in %s', async (lng) => {
    await i18n.changeLanguage(lng);
    post.mockResolvedValue({ data: MISMATCH });
    mount();
    await waitFor(() => expect(card()).toBeTruthy());
    const copy = i18n.t('identity.state.PHONE_DOES_NOT_MATCH_INVITATION');
    expect(copy).not.toContain('identity.state');
    expect(card()!.textContent).toContain(copy);
  });
});
