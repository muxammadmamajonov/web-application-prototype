/**
 * A 5xx must arrive with something support can trace (#4).
 *
 * "Serverda vaqtinchalik nosozlik" is the same sentence for a 500, a 503 from a
 * fail-closed rate limiter, and a 504 from the proxy's 30s upstream abort. The
 * server already stamps X-Request-Id on every response; the client threw it
 * away, so the three were indistinguishable from a bug report.
 */
import { describe, expect, it } from 'vitest';
import { apiErrorMessage, errorRequestId } from './errors';

const RID = 'ef3c743b858d48e3952d61be4dcbdf67';

/** Axios-shaped failure with a plain lowercased header map. */
const fail = (status: number, headers?: Record<string, unknown>) =>
  ({ response: { status, data: { detail: 'boom' }, headers } });

describe('errorRequestId', () => {
  it('reads a lowercased axios header', () => {
    expect(errorRequestId(fail(500, { 'x-request-id': RID }))).toBe('ef3c743b');
  });

  it('also reads the canonical casing, which proxies preserve', () => {
    expect(errorRequestId(fail(500, { 'X-Request-Id': RID }))).toBe('ef3c743b');
  });

  it('reads an AxiosHeaders-style getter', () => {
    const headers = { get: (n: string) => (n === 'x-request-id' ? RID : undefined) };
    expect(errorRequestId(fail(500, headers as never))).toBe('ef3c743b');
  });

  it('truncates to 8 characters — readable over the phone', () => {
    expect(errorRequestId(fail(500, { 'x-request-id': RID }))).toHaveLength(8);
  });

  it.each([
    ['no headers at all', fail(500)],
    ['header absent', fail(500, { 'content-type': 'application/json' })],
    ['empty value', fail(500, { 'x-request-id': '   ' })],
    ['non-string value', fail(500, { 'x-request-id': 12345 })],
    ['not a response error', new Error('offline')],
  ])('returns undefined when %s', (_label, err) => {
    expect(errorRequestId(err)).toBeUndefined();
  });
});

describe('apiErrorMessage appends it, but only where it helps', () => {
  it('adds the id to a 5xx', () => {
    const msg = apiErrorMessage(fail(500, { 'x-request-id': RID }));
    expect(msg).toContain('(#ef3c743b)');
  });

  it.each([503, 504, 502])('covers %i — the codes that share one sentence', (status) => {
    expect(apiErrorMessage(fail(status, { 'x-request-id': RID }))).toContain('(#ef3c743b)');
  });

  it('leaves a 5xx without the header clean', () => {
    // Client-side and mocked failures must not grow an empty "(#)".
    const msg = apiErrorMessage(fail(500));
    expect(msg).not.toContain('#');
    expect(msg.length).toBeGreaterThan(10);
  });

  it.each([
    ['validation', 422],
    ['not found', 404],
    ['conflict', 409],
    ['rate limited', 429],
  ])('does not clutter a %s the user can act on', (_label, status) => {
    // These tell the user what to change; a trace id is noise on top of that.
    expect(apiErrorMessage(fail(status, { 'x-request-id': RID }))).not.toContain('#');
  });

  it('does not disturb a mapped backend code', () => {
    const err = { response: { status: 409, data: { detail: 'customer_blocked' }, headers: { 'x-request-id': RID } } };
    expect(apiErrorMessage(err)).not.toContain('#');
  });
});
