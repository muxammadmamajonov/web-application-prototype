// @vitest-environment jsdom
/**
 * P2-03 — no raw technical or backend text may reach a joining customer.
 *
 * `JoinPage` used to trust `e.message` for any error without an HTTP response.
 * A locally-thrown validation error and an Axios transport failure both match
 * that shape, so an offline submit displayed Axios's raw English
 * "Network Error" to a customer on a Uzbek phone.
 *
 * Only an explicitly tagged `LocalizedError` may show its own message.
 */
import { describe, expect, it, beforeEach, vi } from 'vitest';
import { fireEvent, render, screen, cleanup, waitFor } from '@testing-library/react';
import i18n from '@/i18n';

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
import { apiErrorMessage, isLocalizedError, LocalizedError } from '@/lib/errors';

const post = vi.fn();
const get = vi.fn();
vi.mock('@/lib/api', () => ({
  api: { get: (...a: unknown[]) => get(...a), post: (...a: unknown[]) => post(...a) },
  setSessionToken: vi.fn(),
}));

let INIT_DATA = 'query_id=AAH&user=%7B%22id%22%3A1%7D&hash=abc';
vi.mock('@/lib/telegram', async (orig) => ({
  ...(await orig<Record<string, unknown>>()),
  getTelegramWebApp: () => ({ ready: vi.fn(), expand: vi.fn(), initData: INIT_DATA, initDataUnsafe: {} }),
  isInsideTelegramClient: () => true,
}));
vi.mock('react-router', () => ({ useParams: () => ({ token: 'tok-1' }) }));

import JoinPage from './JoinPage';

/** Axios's shape for a dropped request: no response, raw English message. */
const OFFLINE = Object.assign(new Error('Network Error'), { code: 'ERR_NETWORK' });

const CTX = {
  kind: 'staff', tenant_name: 'Shop', tenant_slug: 'shop', tenant_logo: null,
  role: 'sales', preset: {}, custom_fields: [],
};

async function renderAndSubmit() {
  render(withQuery(<JoinPage />));
  // "wait until the form is on screen" — `findAll`, because the staff branch
  // now also renders the phone-verification card and its own button.
  await screen.findAllByRole('button', { name: /.+/ });
  const form = document.querySelector('form');
  expect(form, 'the join form rendered').toBeTruthy();
  fireEvent.submit(form!);
}

beforeEach(async () => {
  cleanup();
  vi.clearAllMocks();
  INIT_DATA = 'query_id=AAH&user=%7B%22id%22%3A1%7D&hash=abc';
  get.mockResolvedValue({ data: CTX });
  await i18n.changeLanguage('uz');
});

describe('an offline submit shows a localized message', () => {
  it('never displays Axios\'s raw "Network Error"', async () => {
    post.mockRejectedValue(OFFLINE);
    await renderAndSubmit();

    await waitFor(() => {
      expect(screen.getByText(i18n.t('errors.offline'))).toBeTruthy();
    });
    expect(screen.queryByText(/Network Error/i), 'raw transport text must not surface').toBeNull();
  });

  it('shows the same message a Russian speaker would get, in Russian', async () => {
    await i18n.changeLanguage('ru');
    post.mockRejectedValue(OFFLINE);
    await renderAndSubmit();

    await waitFor(() => {
      expect(screen.getByText(i18n.t('errors.offline'))).toBeTruthy();
    });
    await i18n.changeLanguage('uz');
  });

  it('localizes a server failure too', async () => {
    post.mockRejectedValue({ response: { status: 500, data: {} } });
    await renderAndSubmit();
    await waitFor(() => {
      expect(screen.getByText(i18n.t('errors.server'))).toBeTruthy();
    });
  });

  it('localizes an expired invitation instead of echoing the code', async () => {
    post.mockRejectedValue({ response: { status: 409, data: { detail: 'invitation_expired' } } });
    await renderAndSubmit();
    await waitFor(() => {
      expect(screen.getByText(i18n.t('errors.invitation_expired'))).toBeTruthy();
    });
    expect(screen.queryByText(/invitation_expired/)).toBeNull();
  });
});

describe('a tagged local validation error keeps its own message', () => {
  it('shows the "open in Telegram first" text when initData is missing', async () => {
    INIT_DATA = '';
    await renderAndSubmit();
    await waitFor(() => {
      expect(screen.getByText(i18n.t('join.openInTelegramFirst'))).toBeTruthy();
    });
    expect(post, 'nothing is sent without initData').not.toHaveBeenCalled();
  });
});

describe('LocalizedError is the only trusted message source', () => {
  it('recognises the tagged error and nothing else', () => {
    expect(isLocalizedError(new LocalizedError('ok'))).toBe(true);
    expect(isLocalizedError(new Error('Network Error'))).toBe(false);
    expect(isLocalizedError(OFFLINE)).toBe(false);
    expect(isLocalizedError({ message: 'Network Error' })).toBe(false);
  });

  it('an untagged transport error maps to the offline message', () => {
    expect(apiErrorMessage(OFFLINE)).toBe(i18n.t('errors.offline'));
    expect(apiErrorMessage(OFFLINE)).not.toContain('Network Error');
  });
});
