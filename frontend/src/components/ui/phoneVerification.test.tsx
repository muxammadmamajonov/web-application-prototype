// @vitest-environment jsdom
/**
 * FB-012 — the Mini App half of proving a phone number.
 *
 * The one thing this component must never do is decide. Telegram's consent
 * callback reports a tap; the number travels to the bot as an authenticated
 * update and is checked there. So every path here ends in "ask the server what
 * it now believes", and a client-side success is not a success.
 */
import { describe, expect, it, beforeEach, afterEach, vi } from 'vitest';
import { cleanup, render, screen, act, fireEvent, waitFor } from '@testing-library/react';
import i18n from '@/i18n';

const requestContact = vi.fn<() => Promise<'sent' | 'cancelled' | 'unsupported'>>();
vi.mock('@/lib/telegram/permissions', () => ({
  requestContact: () => requestContact(),
  requestWriteAccessOnce: () => {},
}));

const { PhoneVerificationCard } = await import('@/components/ui/PhoneVerificationCard');
const { WAIT_TIMEOUT_MS } = await import('@/lib/identityStates');

const UNVERIFIED = { state: 'VERIFICATION_REQUIRED', verified: false, phoneMasked: null };
const VERIFIED = { state: 'VERIFIED', verified: true, phoneMasked: '+998•••••••33' };

const card = () => document.querySelector('[data-testid="identity-card"]') as HTMLElement;
const action = () => screen.queryByRole('button');

beforeEach(async () => {
  await i18n.changeLanguage('uz');
  vi.clearAllMocks();
  requestContact.mockResolvedValue('sent');
});
afterEach(() => { cleanup(); vi.useRealTimers(); });

describe('already verified', () => {
  it('says so and does not ask again', () => {
    render(<PhoneVerificationCard snapshot={VERIFIED} onRefetch={async () => VERIFIED} />);
    expect(card().dataset.state).toBe('VERIFIED');
    // The whole point of the bot-side reconciliation: a person who verified in
    // chat is not prompted a second time in the Mini App.
    expect(action()).toBeNull();
    expect(requestContact).not.toHaveBeenCalled();
  });

  it('shows the number masked, never in full', () => {
    render(<PhoneVerificationCard snapshot={VERIFIED} onRefetch={async () => VERIFIED} />);
    expect(screen.getByText('+998•••••••33')).toBeTruthy();
    expect(document.body.textContent).not.toContain('998901112233');
  });
});

describe('asking for consent', () => {
  it('calls Telegram straight from the tap', async () => {
    render(<PhoneVerificationCard snapshot={UNVERIFIED} onRefetch={async () => UNVERIFIED} />);
    await act(async () => { fireEvent.click(action()!); });
    expect(requestContact).toHaveBeenCalledTimes(1);
  });

  it('a cancelled prompt is a state, not a failure', async () => {
    requestContact.mockResolvedValue('cancelled');
    render(<PhoneVerificationCard snapshot={UNVERIFIED} onRefetch={async () => UNVERIFIED} />);
    await act(async () => { fireEvent.click(action()!); });
    expect(card().dataset.state).toBe('cancelled');
    expect(action()).toBeTruthy();            // retry is offered
  });

  it('an old Telegram is told what is wrong and not offered a dead button', async () => {
    requestContact.mockResolvedValue('unsupported');
    render(<PhoneVerificationCard snapshot={UNVERIFIED} onRefetch={async () => UNVERIFIED} />);
    await act(async () => { fireEvent.click(action()!); });
    expect(card().dataset.state).toBe('unsupported');
    expect(action()).toBeNull();
    expect(card().textContent).toContain(i18n.t('identity.state.unsupported'));
  });

  it('sharing does NOT by itself mean verified', async () => {
    const onVerified = vi.fn();
    render(<PhoneVerificationCard snapshot={UNVERIFIED} onRefetch={async () => UNVERIFIED}
                                  onVerified={onVerified} />);
    await act(async () => { fireEvent.click(action()!); });
    // Telegram said "sent". The server has not said anything yet.
    expect(card().dataset.state).toBe('waiting');
    expect(onVerified).not.toHaveBeenCalled();
  });
});

describe('waiting for the bot update', () => {
  it('polls the server and settles when the proof lands', async () => {
    vi.useFakeTimers();
    const onVerified = vi.fn();
    let calls = 0;
    const refetch = vi.fn(async () => { calls += 1; return calls < 3 ? UNVERIFIED : VERIFIED; });
    render(<PhoneVerificationCard snapshot={UNVERIFIED} onRefetch={refetch} onVerified={onVerified} />);
    await act(async () => { fireEvent.click(action()!); });

    for (let i = 0; i < 3; i += 1) {
      await act(async () => { await vi.advanceTimersByTimeAsync(1_600); });
    }
    expect(refetch).toHaveBeenCalled();
    expect(onVerified).toHaveBeenCalledWith(VERIFIED);
  });

  it('gives up after a bounded wait rather than spinning forever', async () => {
    vi.useFakeTimers();
    render(<PhoneVerificationCard snapshot={UNVERIFIED} onRefetch={async () => UNVERIFIED} />);
    await act(async () => { fireEvent.click(action()!); });
    await act(async () => { await vi.advanceTimersByTimeAsync(WAIT_TIMEOUT_MS + 2_000); });
    expect(card().dataset.state).toBe('timeout');
    expect(action()).toBeTruthy();
  });

  it('a dropped connection mid-wait is not a failed verification', async () => {
    vi.useFakeTimers();
    let n = 0;
    const refetch = vi.fn(async () => {
      n += 1;
      if (n <= 2) throw new Error('offline');
      return VERIFIED;
    });
    const onVerified = vi.fn();
    render(<PhoneVerificationCard snapshot={UNVERIFIED} onRefetch={refetch} onVerified={onVerified} />);
    await act(async () => { fireEvent.click(action()!); });
    for (let i = 0; i < 4; i += 1) {
      await act(async () => { await vi.advanceTimersByTimeAsync(1_600); });
    }
    expect(onVerified).toHaveBeenCalledWith(VERIFIED);
  });

  it('stops polling when the card leaves the screen', async () => {
    vi.useFakeTimers();
    const refetch = vi.fn(async () => UNVERIFIED);
    const { unmount } = render(
      <PhoneVerificationCard snapshot={UNVERIFIED} onRefetch={refetch} />,
    );
    await act(async () => { fireEvent.click(action()!); });
    await act(async () => { await vi.advanceTimersByTimeAsync(3_200); });
    const before = refetch.mock.calls.length;
    unmount();
    await act(async () => { await vi.advanceTimersByTimeAsync(10_000); });
    expect(refetch.mock.calls.length).toBe(before);
  });

  it('a second tap does not start a second wait', async () => {
    render(<PhoneVerificationCard snapshot={UNVERIFIED} onRefetch={async () => UNVERIFIED} />);
    await act(async () => { fireEvent.click(action()!); });
    expect(action()!.hasAttribute('disabled')).toBe(true);
  });
});

describe('states only the server can produce', () => {
  it.each([
    ['UNSUPPORTED_REGION', true],
    ['PHONE_DOES_NOT_MATCH_INVITATION', true],
    ['RECONCILIATION_REQUIRED', false],
    ['CUSTOMER_LINKED_TO_OTHER_IDENTITY', false],
    ['IDENTITY_ALREADY_LINKED_HERE', false],
  ])('%s renders its own sentence', async (state, actionable) => {
    render(<PhoneVerificationCard
      snapshot={{ state, verified: false, phoneMasked: null }}
      onRefetch={async () => null} />);
    expect(card().dataset.state).toBe(state);
    expect(card().textContent).toContain(i18n.t(`identity.state.${state}`));
    // A case a person cannot fix must not offer them a button that pretends
    // they can.
    expect(action() !== null).toBe(actionable);
  });

  it('announces the ones that need a human as an alert', () => {
    render(<PhoneVerificationCard
      snapshot={{ state: 'RECONCILIATION_REQUIRED', verified: false, phoneMasked: null }}
      onRefetch={async () => null} />);
    expect(card().querySelector('[role="alert"]')).toBeTruthy();
  });

  it('renders no raw state code to the person', async () => {
    for (const state of ['VERIFICATION_REQUIRED', 'UNSUPPORTED_REGION', 'NO_MATCH']) {
      cleanup();
      render(<PhoneVerificationCard snapshot={{ state, verified: false, phoneMasked: null }}
                                    onRefetch={async () => null} />);
      expect(card().textContent).not.toContain(state);
      expect(card().textContent).not.toContain('identity.state');
    }
  });
});

describe('localization', () => {
  it.each(['uz', 'ru'])('every state has copy in %s', async (lng) => {
    await i18n.changeLanguage(lng);
    const states = ['VERIFIED', 'VERIFICATION_REQUIRED', 'UNSUPPORTED_REGION', 'MALFORMED',
      'PHONE_DOES_NOT_MATCH_INVITATION', 'RECONCILIATION_REQUIRED',
      'CUSTOMER_LINKED_TO_OTHER_IDENTITY', 'IDENTITY_ALREADY_LINKED_HERE',
      'PHONE_NOT_VERIFIED', 'NO_MATCH', 'prompting', 'waiting', 'cancelled',
      'unsupported', 'timeout', 'error', 'idle'];
    for (const s of states) {
      const copy = i18n.t(`identity.state.${s}`);
      expect(copy).not.toBe(`identity.state.${s}`);
      expect(copy.length).toBeGreaterThan(4);
    }
    expect(i18n.t('identity.verifyAction')).not.toBe('identity.verifyAction');
  });

  it('the action is a label, not an icon alone', async () => {
    await i18n.changeLanguage('uz');
    render(<PhoneVerificationCard snapshot={UNVERIFIED} onRefetch={async () => UNVERIFIED} />);
    await waitFor(() => expect(action()!.textContent!.trim().length).toBeGreaterThan(3));
  });
});
