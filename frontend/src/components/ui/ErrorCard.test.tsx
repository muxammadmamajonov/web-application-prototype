// @vitest-environment jsdom
/**
 * Phase 3 remediation R-03 — failures must render *distinctly*.
 *
 * The first pass built a kind-aware error card and then left the `error` prop
 * optional, so all 42 retained call sites kept omitting it: offline, a 500, a
 * 403, a 404 and a 422 all produced the same sentence and the same Retry
 * button. The prop is now required, and these tests pin what that buys — that
 * each failure kind reaches the user as a different, actionable state, and that
 * Retry is offered only where pressing it could actually change the outcome.
 */
import { describe, expect, it, beforeEach, vi } from 'vitest';
import { render, screen, cleanup } from '@testing-library/react';
import i18n from '@/i18n';
import { ErrorCard } from './ErrorCard';

beforeEach(async () => { cleanup(); await i18n.changeLanguage('uz'); });

const http = (status: number, detail?: unknown) => ({ response: { status, data: { detail } } });
const OFFLINE = { code: 'ERR_NETWORK' };
const TIMEOUT = { code: 'ECONNABORTED' };

const textOf = () => screen.getByRole('alert').textContent ?? '';
const retry = () => screen.queryByRole('button');

describe('each failure kind produces its own message', () => {
  it('renders six distinct messages', () => {
    const seen = new Set<string>();
    for (const err of [OFFLINE, TIMEOUT, http(500), http(403), http(404), http(422)]) {
      cleanup();
      render(<ErrorCard error={err} onRetry={() => {}} />);
      // The first paragraph is the classified message; the offline state adds a
      // second one about automatic recovery, which is not what we compare here.
      seen.add(screen.getByRole('alert').querySelector('p')?.textContent ?? '');
    }
    expect(seen.size, `distinct messages, got: ${[...seen].join(' | ')}`).toBe(6);
  });
});

describe('offline', () => {
  it('says connectivity, and that recovery is automatic', () => {
    render(<ErrorCard error={OFFLINE} onRetry={() => {}} />);
    const body = textOf();
    expect(body).toContain(i18n.t('errors.offline'));
    // The reconnect recovery wired in queryClient is only useful if the user
    // knows to expect it rather than sitting on a dead screen.
    expect(body).toContain(i18n.t('common.offlineAutoRetry'));
  });

  it('still offers a manual retry', () => {
    render(<ErrorCard error={OFFLINE} onRetry={() => {}} />);
    expect(retry()).toBeTruthy();
  });
});

describe('retry is offered only where it can help', () => {
  it.each([
    ['offline', OFFLINE],
    ['timeout', TIMEOUT],
    ['server 500', http(500)],
    ['rate limited', http(429)],
  ])('offers retry for %s', (_label, err) => {
    render(<ErrorCard error={err} onRetry={() => {}} />);
    expect(retry()).toBeTruthy();
  });

  it.each([
    ['permission 403', http(403)],
    ['not found 404', http(404)],
    ['validation 422', http(422)],
    ['conflict 409', http(409)],
  ])('withholds retry for %s', (_label, err) => {
    render(<ErrorCard error={err} onRetry={() => {}} />);
    expect(retry(), 'retrying an unchanged request cannot succeed here').toBeNull();
  });
});

describe('permission failures name the role', () => {
  it('distinguishes owner-only from owner-or-manager', () => {
    render(<ErrorCard error={http(403, 'owner_only')} />);
    const ownerOnly = textOf();
    cleanup();
    render(<ErrorCard error={http(403, 'owner_or_manager_only')} />);
    expect(textOf()).not.toBe(ownerOnly);
  });

  it('never shows the raw backend code', () => {
    render(<ErrorCard error={http(403, 'owner_only')} />);
    expect(textOf()).not.toContain('owner_only');
  });
});

describe('server, validation and not-found', () => {
  it('a 500 reads as a server problem, not as user error', () => {
    render(<ErrorCard error={http(500)} />);
    expect(textOf()).toContain(i18n.t('errors.server'));
  });

  it('a 422 asks the user to fix their input', () => {
    render(<ErrorCard error={http(422)} />);
    expect(textOf()).toContain(i18n.t('errors.validation'));
  });

  it('a 404 says the thing is gone', () => {
    render(<ErrorCard error={http(404)} />);
    expect(textOf()).toContain(i18n.t('errors.notFound'));
  });
});

describe('call-site contract', () => {
  it('an explicit null keeps the generic copy and the retry', () => {
    // `error={null}` is the deliberate opt-out for a call site with nothing to
    // classify — distinct from *forgetting*, which the required prop prevents.
    render(<ErrorCard error={null} onRetry={() => {}} />);
    expect(textOf()).toContain(i18n.t('common.loadFailed'));
    expect(retry()).toBeTruthy();
  });

  it('an explicit message still wins over the classified one', () => {
    render(<ErrorCard error={http(500)} message="Maxsus xabar" />);
    expect(textOf()).toContain('Maxsus xabar');
  });

  it('announces assertively so a failure is not missed', () => {
    render(<ErrorCard error={http(500)} />);
    const alert = screen.getByRole('alert');
    expect(alert.getAttribute('aria-live')).toBe('assertive');
  });
});

describe('localization', () => {
  it('renders the same failure differently in uz and ru', async () => {
    render(<ErrorCard error={http(500)} />);
    const uz = textOf();
    cleanup();
    await i18n.changeLanguage('ru');
    render(<ErrorCard error={http(500)} />);
    expect(textOf()).not.toBe(uz);
    await i18n.changeLanguage('uz');
  });
});

describe('retry wiring', () => {
  it('calls back when pressed', async () => {
    const onRetry = vi.fn();
    render(<ErrorCard error={http(500)} onRetry={onRetry} />);
    retry()!.click();
    expect(onRetry).toHaveBeenCalledOnce();
  });
});
