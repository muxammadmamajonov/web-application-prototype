// @vitest-environment jsdom
/**
 * Two facts share a shape, a label and a keyboard.
 *
 * The profile rendered one editable box called "Telefon". That box is
 * `users.phone` — what somebody typed — and the screen gave no hint that it is
 * neither proof of anything nor the thing that decides who receives this
 * person's messages. Somebody correcting a typo there would reasonably believe
 * they had changed their identity.
 */
import { describe, expect, it, beforeEach, afterEach, vi } from 'vitest';
import { cleanup, render, screen, act, fireEvent } from '@testing-library/react';
import i18n from '@/i18n';

const requestContact = vi.fn<() => Promise<'sent' | 'cancelled' | 'unsupported'>>();
vi.mock('@/lib/telegram/permissions', () => ({
  requestContact: () => requestContact(),
  requestWriteAccessOnce: () => {},
}));

const { VerifiedPhoneRow } = await import('@/components/ui/VerifiedPhoneRow');

const VERIFIED = { state: 'VERIFIED', verified: true, phoneMasked: '+998•••••••33' };
const UNVERIFIED = { state: 'VERIFICATION_REQUIRED', verified: false, phoneMasked: null };
const FOREIGN = { state: 'UNSUPPORTED_REGION', verified: true, phoneMasked: '+1••••••71' };

beforeEach(async () => {
  await i18n.changeLanguage('uz');
  vi.clearAllMocks();
  requestContact.mockResolvedValue('sent');
});
afterEach(cleanup);

describe('a verified customer', () => {
  it('sees the number masked and read-only', () => {
    render(<VerifiedPhoneRow snapshot={VERIFIED} onRefetch={async () => VERIFIED} />);
    const row = screen.getByTestId('verified-phone');
    expect(row.textContent).toContain('+998•••••••33');
    // Not an input: changing a proved number is a support workflow.
    expect(row.querySelector('input')).toBeNull();
    expect(document.querySelectorAll('input')).toHaveLength(0);
  });

  it('is told, in words, that it is verified', () => {
    render(<VerifiedPhoneRow snapshot={VERIFIED} onRefetch={async () => VERIFIED} />);
    expect(screen.getByTestId('verified-phone').textContent)
      .toContain(i18n.t('profile.verifiedBadge'));
    // A status, not an icon on its own.
    expect(i18n.t('profile.verifiedBadge').length).toBeGreaterThan(3);
  });

  it('never renders the full number', () => {
    render(<VerifiedPhoneRow snapshot={VERIFIED} onRefetch={async () => VERIFIED} />);
    expect(document.body.textContent).not.toMatch(/\+?998\d{9}/);
  });

  it('offers no verification action, because there is nothing to do', () => {
    render(<VerifiedPhoneRow snapshot={VERIFIED} onRefetch={async () => VERIFIED} />);
    expect(screen.queryByTestId('verify-phone-slot')).toBeNull();
  });
});

describe('an unverified customer', () => {
  it('is offered the native verification action instead of a text box', () => {
    render(<VerifiedPhoneRow snapshot={UNVERIFIED} onRefetch={async () => UNVERIFIED} />);
    expect(screen.getByTestId('verify-phone-slot')).toBeTruthy();
    expect(screen.queryByTestId('verified-phone')).toBeNull();
    expect(document.querySelectorAll('input')).toHaveLength(0);
  });

  it('runs the real consent flow from the tap', async () => {
    render(<VerifiedPhoneRow snapshot={UNVERIFIED} onRefetch={async () => UNVERIFIED} />);
    await act(async () => { fireEvent.click(screen.getByRole('button')); });
    expect(requestContact).toHaveBeenCalledTimes(1);
  });

  it('a verified-but-ineligible number is not presented as settled', () => {
    render(<VerifiedPhoneRow snapshot={FOREIGN} onRefetch={async () => FOREIGN} />);
    expect(screen.queryByTestId('verified-phone')).toBeNull();
    expect(screen.getByTestId('identity-card').dataset.state).toBe('UNSUPPORTED_REGION');
  });
});

describe('localization', () => {
  it.each(['uz', 'ru'])('names the one account phone in %s', async (lng) => {
    await i18n.changeLanguage(lng);
    const verified = i18n.t('profile.verifiedPhone');
    expect(verified).not.toContain('profile.');
    expect(verified.length).toBeGreaterThan(3);
  });

  it('has no second phone label left to render', async () => {
    // These two assertions used to check that "Tasdiqlangan telefon" and
    // "Bogʻlanish uchun telefon" READ differently — which is the wrong problem
    // to solve. Two labels that read differently are still two phone numbers for
    // one person, and the second one was an editable text box that proved
    // nothing and reached nobody. The keys are gone; a reintroduced label would
    // resolve to something other than its own key name and fail here.
    for (const lng of ['uz', 'ru']) {
      await i18n.changeLanguage(lng);
      for (const key of ['profile.contactPhone', 'profile.contactPhoneHint']) {
        expect(i18n.t(key), `${key} is still defined in ${lng}`).toBe(key);
      }
    }
  });

  it('no longer claims the name is read from Telegram', async () => {
    // It is self-chosen and persistent; saying otherwise taught people that
    // saving it was pointless.
    for (const lng of ['uz', 'ru']) {
      await i18n.changeLanguage(lng);
      for (const key of ['profile.detailsNote', 'profile.editProfileHint']) {
        const copy = i18n.t(key);
        expect(copy).not.toMatch(/Ism, familiya va rasm Telegram/);
        expect(copy).not.toMatch(/Имя, фамилия и фото.*Telegram/);
      }
    }
  });
});
