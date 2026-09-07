/**
 * Phase 3 UX-015 / UX-026 / UX-027 — the shared error vocabulary.
 *
 * Two properties matter and both are regression-prone:
 *   1. no raw backend token ever reaches a user, in either language;
 *   2. a failure is classified, so the UI can distinguish "you are offline"
 *      from "the server broke" from "you are not allowed to do this".
 */
import { describe, expect, it, beforeEach } from 'vitest';
import i18n from '@/i18n';
import { apiErrorMessage, errorKind, errorCode, isNotFound, isOffline, isRetryable } from './errors';

const httpError = (status: number, detail?: unknown) => ({ response: { status, data: { detail } } });

describe('errorKind', () => {
  it('classifies transport failures', () => {
    expect(errorKind({ code: 'ERR_NETWORK' })).toBe('offline');
    expect(errorKind({ code: 'ECONNABORTED' })).toBe('timeout');
  });

  it('classifies HTTP failures', () => {
    expect(errorKind(httpError(400))).toBe('validation');
    expect(errorKind(httpError(422))).toBe('validation');
    expect(errorKind(httpError(401))).toBe('permission');
    expect(errorKind(httpError(403))).toBe('permission');
    expect(errorKind(httpError(404))).toBe('notFound');
    expect(errorKind(httpError(409))).toBe('conflict');
    expect(errorKind(httpError(429))).toBe('rateLimited');
    expect(errorKind(httpError(500))).toBe('server');
    expect(errorKind(httpError(503))).toBe('server');
  });

  it('distinguishes offline from server failure', () => {
    // The whole point of UX-027: these must not collapse into one state.
    expect(errorKind({ code: 'ERR_NETWORK' })).not.toBe(errorKind(httpError(500)));
    expect(isOffline({ code: 'ERR_NETWORK' })).toBe(true);
    expect(isOffline(httpError(500))).toBe(false);
  });

  it('marks only genuinely retryable failures as retryable', () => {
    expect(isRetryable({ code: 'ERR_NETWORK' })).toBe(true);
    expect(isRetryable(httpError(500))).toBe(true);
    expect(isRetryable(httpError(429))).toBe(true);
    // Retrying the same bad input or a forbidden action cannot succeed.
    expect(isRetryable(httpError(422))).toBe(false);
    expect(isRetryable(httpError(403))).toBe(false);
    expect(isRetryable(httpError(404))).toBe(false);
  });

  it('extracts the machine code from both detail shapes', () => {
    expect(errorCode(httpError(409, 'not_pending'))).toBe('not_pending');
    expect(errorCode(httpError(402, { error: 'insufficient_balance' }))).toBe('insufficient_balance');
    expect(errorCode(httpError(500))).toBeUndefined();
  });
});

describe.each(['uz', 'ru'])('apiErrorMessage — %s', (lng) => {
  beforeEach(async () => { await i18n.changeLanguage(lng); });

  it('never shows a raw snake_case backend token', () => {
    const codes = [
      'customer_not_found', 'not_pending', 'plan_limit_reached', 'owner_only',
      'insufficient_balance', 'invitation_expired', 'some_code_we_never_mapped',
      'a_brand_new_backend_error', 'bad_status',
    ];
    for (const code of codes) {
      const msg = apiErrorMessage(httpError(409, code));
      expect(msg).not.toContain('_');
      expect(msg).not.toBe(code);
      expect(msg.length).toBeGreaterThan(0);
    }
  });

  it('names the required role on a permission failure', () => {
    const owner = apiErrorMessage(httpError(403, 'owner_only'));
    const both = apiErrorMessage(httpError(403, 'owner_or_manager_only'));
    const generic = apiErrorMessage(httpError(403, 'something_else'));
    // Each role boundary produces its OWN message, not one generic refusal.
    expect(owner).not.toBe(both);
    expect(owner).not.toBe(generic);
    expect(both).not.toBe(generic);
  });

  it('gives offline, timeout, server and validation distinct messages', () => {
    const msgs = [
      apiErrorMessage({ code: 'ERR_NETWORK' }),
      apiErrorMessage({ code: 'ECONNABORTED' }),
      apiErrorMessage(httpError(500)),
      apiErrorMessage(httpError(422)),
      apiErrorMessage(httpError(404)),
      apiErrorMessage(httpError(409)),
    ];
    expect(new Set(msgs).size).toBe(msgs.length);
  });

  it('is localized — uz and ru differ', async () => {
    await i18n.changeLanguage('uz');
    const uz = apiErrorMessage(httpError(403, 'owner_only'));
    await i18n.changeLanguage('ru');
    const ru = apiErrorMessage(httpError(403, 'owner_only'));
    expect(uz).not.toBe(ru);
    await i18n.changeLanguage(lng);
  });

  it('never shows backend prose, however human it looks', () => {
    // (P2-03) Server-written sentences are untranslated and can carry internal
    // detail. A message that merely "looks human" is still not localized, so it
    // falls through to the message for its kind.
    const prose = 'Tarif chegarasiga yetdingiz, iltimos tarifni oshiring';
    const msg = apiErrorMessage(httpError(409, { message: prose }));
    expect(msg).not.toBe(prose);
    expect(msg).toBe(i18n.t('errors.conflict'));
  });

  it('builds the plan-limit sentence from the structured limit, not the prose', () => {
    const prose = 'You have reached your plan limit of 25 contracts';
    const msg = apiErrorMessage(httpError(402, {
      error: 'plan_limit_reached', limit_key: 'contract', limit: 25, message: prose,
    }));
    expect(msg).not.toContain(prose);
    expect(msg).toContain('25');
    expect(msg).toBe(i18n.t('errors.plan_limit_reached_n', { limit: 25 }));
  });

  it('falls back to the generic plan-limit message when no number is sent', () => {
    const msg = apiErrorMessage(httpError(402, { error: 'plan_limit_reached' }));
    expect(msg).toBe(i18n.t('errors.plan_limit_reached'));
  });
});

describe('isNotFound', () => {
  it('is true only for 404', () => {
    expect(isNotFound(httpError(404))).toBe(true);
    expect(isNotFound(httpError(409))).toBe(false);
    expect(isNotFound({ code: 'ERR_NETWORK' })).toBe(false);
  });
});
