// @vitest-environment jsdom
/**
 * C3-01 — the Telegram auto-login path must not render raw technical text.
 *
 * `bootstrapTelegramAutoLogin` used to return
 * `response.data.detail ?? Error.message`, and `LoginPage` rendered it
 * verbatim. Two things reached the screen through that: untranslated backend
 * prose, and — for any request that never got a response — Axios's English
 * "Network Error", shown to a shop owner on an Uzbek phone whose signal dropped.
 *
 * The outcome type now carries the ERROR rather than a message, so the only way
 * to display it is through `apiErrorMessage`. These tests drive the rendered
 * page for each failure shape, in both locales.
 */
import { describe, expect, it, beforeEach, vi } from 'vitest';
import { render, screen, cleanup, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router';

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

const post = vi.fn();
const setSessionToken = vi.fn();
vi.mock('@/lib/api', () => ({
  api: { get: vi.fn(), post: (...a: unknown[]) => post(...a) },
  setSessionToken: (...a: unknown[]) => setSessionToken(...a),
  hasAuthFlag: () => false,
}));

vi.mock('@/lib/telegram', async (orig) => ({
  ...(await orig<Record<string, unknown>>()),
  isInsideTelegramClient: () => true,
  getTelegramWebApp: () => ({ ready: vi.fn(), expand: vi.fn(), initData: 'query_id=A&hash=b' }),
  waitForInitData: async () => ({ ready: vi.fn(), expand: vi.fn(), initData: 'query_id=A&hash=b' }),
}));

vi.mock('@/lib/queryClient', () => ({
  queryClient: { invalidateQueries: vi.fn().mockResolvedValue(undefined) },
}));

import LoginPage from './LoginPage';
import { loginViaWebAppInitData } from '@/hooks/useMe';

/** Axios's shape for a request that never got a response. */
const OFFLINE = Object.assign(new Error('Network Error'), { code: 'ERR_NETWORK' });

/** A backend that answered, in its own words. */
const PROSE = {
  response: { status: 409, data: { detail: 'Init data signature is stale, please reopen the app' } },
  message: 'Request failed with status code 409',
};

function renderLogin() {
  return render(withQuery(<MemoryRouter initialEntries={['/auth/login']}><LoginPage /></MemoryRouter>));
}

beforeEach(async () => {
  cleanup();
  vi.clearAllMocks();
  await i18n.changeLanguage('uz');
});

describe.each(['uz', 'ru'])('an offline auto-login in %s', (lng) => {
  beforeEach(async () => { await i18n.changeLanguage(lng); });

  it('shows the localized offline message', async () => {
    post.mockRejectedValue(OFFLINE);
    renderLogin();
    await waitFor(() => {
      expect(screen.getByText(i18n.t('errors.offline'))).toBeTruthy();
    });
  });

  it('never shows Axios\'s raw "Network Error"', async () => {
    post.mockRejectedValue(OFFLINE);
    renderLogin();
    await waitFor(() => expect(screen.getByText(i18n.t('errors.offline'))).toBeTruthy());
    expect(document.body.textContent).not.toContain('Network Error');
  });
});

describe('backend prose never reaches the page', () => {
  it('renders the localized message for the status, not the sentence the server sent', async () => {
    post.mockRejectedValue(PROSE);
    renderLogin();
    await waitFor(() => {
      expect(screen.getByText(i18n.t('errors.conflict'))).toBeTruthy();
    });
    expect(document.body.textContent).not.toContain('Init data signature is stale');
    expect(document.body.textContent).not.toContain('Request failed with status code');
  });

  it('does the same for an unmapped snake_case code', async () => {
    post.mockRejectedValue({ response: { status: 500, data: { detail: 'some_internal_marker' } } });
    renderLogin();
    await waitFor(() => expect(screen.getByText(i18n.t('errors.server'))).toBeTruthy());
    expect(document.body.textContent).not.toContain('some_internal_marker');
  });

  it('shows nothing containing an underscore or a bare English sentence', async () => {
    post.mockRejectedValue(PROSE);
    renderLogin();
    await waitFor(() => expect(screen.getByText(i18n.t('errors.conflict'))).toBeTruthy());
    const shown = screen.getByText(i18n.t('errors.conflict')).textContent ?? '';
    expect(shown).not.toMatch(/[a-z]_[a-z]/);
  });
});

describe('401 handling is unchanged', () => {
  it('clears the stale session token and localizes the message', async () => {
    post.mockRejectedValue({ response: { status: 401, data: { detail: 'init_data_invalid' } } });
    renderLogin();
    await waitFor(() => {
      expect(screen.getByText(i18n.t('errors.init_data_invalid'))).toBeTruthy();
    });
    // The stale credential must still be dropped so the user lands on a fresh login.
    expect(setSessionToken).toHaveBeenCalledWith(null);
  });

  it('does not clear the token for a non-401 failure', async () => {
    post.mockRejectedValue(OFFLINE);
    renderLogin();
    await waitFor(() => expect(screen.getByText(i18n.t('errors.offline'))).toBeTruthy());
    expect(setSessionToken).not.toHaveBeenCalledWith(null);
  });
});

describe('the outcome type cannot carry a rendered message', () => {
  it('a failed bootstrap returns the error itself, not a string', async () => {
    post.mockRejectedValue(OFFLINE);
    const r = await loginViaWebAppInitData();
    expect(r.status).toBe('failed');
    if (r.status !== 'failed') return;
    // If this ever becomes a string again, LoginPage can print it verbatim.
    expect(typeof r.error).not.toBe('string');
    expect(r.error).toBe(OFFLINE);
    expect(Object.keys(r)).not.toContain('message');
  });

  it('a successful bootstrap reports logged-in', async () => {
    post.mockResolvedValue({ data: { token: 'tok' } });
    const r = await loginViaWebAppInitData();
    expect(r.status).toBe('logged-in');
  });
});
