/**
 * MUDD-76 (client half) — a refused *replay* must not log the user out.
 *
 * The backend now spends each Mini-App `initData` exactly once. Telegram hands
 * an app the SAME signed string for the whole launch, so the second exchange in
 * one launch — a reload, or an involuntary bounce back through /auth/login —
 * legitimately arrives as a replay and is refused with 401
 * `telegram_initdata_replayed`. The client cannot mint a fresh string to retry
 * with; the session the FIRST exchange established is the credential it has.
 *
 * So the bootstrap must tell the two 401s apart:
 *
 *   * `telegram_initdata_replayed` → keep the session, let /auth/me rule on it
 *   * anything else                → drop it, land on a fresh login
 *
 * Collapsing them logs a legitimate user out of their own reload, and the
 * tempting "fix" for that is to start accepting replays again. This is not a
 * hole: an attacker replaying a captured initData holds no session cookie, so
 * the /auth/me that follows still 401s them.
 */
import { describe, expect, it, beforeEach, vi } from 'vitest';

const post = vi.fn();
const setSessionToken = vi.fn();
const hasAuthFlag = vi.fn(() => false);

vi.mock('@/lib/api', () => ({
  api: { post: (...a: unknown[]) => post(...a) },
  setSessionToken: (...a: unknown[]) => setSessionToken(...a),
  hasAuthFlag: () => hasAuthFlag(),
}));
vi.mock('@/lib/queryClient', () => ({
  queryClient: { invalidateQueries: vi.fn(async () => undefined) },
}));
vi.mock('@/lib/telegram', () => ({
  getTelegramWebApp: () => ({ initData: 'auth_date=1&hash=abc', ready() {}, expand() {} }),
  isInsideTelegramClient: () => true,
  waitForInitData: async () => null,
}));

const { loginViaWebAppInitData } = await import('./useMe');

/** An axios-shaped 401 carrying the backend's `detail` code. */
function rejectWith(detail: string) {
  return Object.assign(new Error('Request failed'), {
    response: { status: 401, data: { detail } },
  });
}

beforeEach(() => {
  post.mockReset();
  setSessionToken.mockReset();
  hasAuthFlag.mockReset();
  hasAuthFlag.mockReturnValue(false);
});

describe('a replayed initData does not destroy the session it already bought', () => {
  it('reports logged-in and keeps the session when one already exists', async () => {
    hasAuthFlag.mockReturnValue(true);
    post.mockRejectedValue(rejectWith('telegram_initdata_replayed'));

    const out = await loginViaWebAppInitData();

    expect(out.status).toBe('logged-in');
    // The clearing call is what the bug would look like — pin its absence.
    expect(setSessionToken).not.toHaveBeenCalledWith(null);
  });

  it('still fails when the replay refusal is all we have (no session to keep)', async () => {
    hasAuthFlag.mockReturnValue(false);
    post.mockRejectedValue(rejectWith('telegram_initdata_replayed'));

    const out = await loginViaWebAppInitData();

    // An attacker replaying a captured string lands here: no cookie, no login.
    expect(out.status).toBe('failed');
    expect(setSessionToken).not.toHaveBeenCalledWith(null);
  });
});

describe('an invalid credential is still treated as one', () => {
  it('drops the stored session on a plain 401', async () => {
    hasAuthFlag.mockReturnValue(true);
    post.mockRejectedValue(rejectWith('telegram_initdata_invalid'));

    const out = await loginViaWebAppInitData();

    expect(out.status).toBe('failed');
    expect(setSessionToken).toHaveBeenCalledWith(null);
  });
});

describe('the happy path is untouched', () => {
  it('records the session when the exchange succeeds', async () => {
    post.mockResolvedValue({ data: { token: 'tok' } });

    const out = await loginViaWebAppInitData();

    expect(out.status).toBe('logged-in');
    expect(setSessionToken).toHaveBeenCalledWith('tok');
  });
});
