// @vitest-environment jsdom
/**
 * FB-014 — the greeting must not come from the signed-in account's Telegram
 * profile name.
 *
 * Reported: the customer record said "Example User" and the home screen
 * said "Salom, Muddato Support". Both strings existed; the screen read the
 * wrong one. The server now resolves WHICH name to use and sends it as
 * `displayName`; this file pins that nothing here reaches past it.
 */
import { describe, expect, it, beforeAll } from 'vitest';
import i18n from '@/i18n';
import { greetingFor, CLIENT_GREETING, WELCOME_GREETING } from '@/lib/greeting';
import type { MeResponse } from '@/types';

const OPERATOR = 'Muddato Support';
const CUSTOMER = 'Example User';

const me = (over: Partial<MeResponse>) => ({
  firstName: null, lastName: null, displayName: null, ...over,
}) as MeResponse;

beforeAll(async () => { await i18n.changeLanguage('uz'); });

describe('client greeting', () => {
  it('never renders the operator name the screenshot showed', async () => {
    await i18n.changeLanguage('uz');
    const out = greetingFor(i18n.t, me({ firstName: OPERATOR, displayName: null }), CLIENT_GREETING);
    expect(out).not.toContain(OPERATOR);
    expect(out).not.toContain('Support');
  });

  it('greets a customer by the name the server resolved', async () => {
    await i18n.changeLanguage('uz');
    expect(greetingFor(i18n.t, me({ displayName: CUSTOMER }), CLIENT_GREETING)).toContain(CUSTOMER);
  });

  it('falls back to a neutral greeting, not to a blank name', async () => {
    await i18n.changeLanguage('uz');
    const out = greetingFor(i18n.t, me({}), CLIENT_GREETING);
    expect(out.trim()).toBeTruthy();
    // The old shape rendered "Salom,  👋" with a hole where the name was.
    expect(out).not.toMatch(/,\s*(👋)?\s*$/);
    expect(out).not.toContain('undefined');
    expect(out).not.toContain('null');
  });

  it('ignores firstName entirely, even when displayName is absent', async () => {
    await i18n.changeLanguage('uz');
    const withFirst = greetingFor(i18n.t, me({ firstName: 'Jasur' }), CLIENT_GREETING);
    const without = greetingFor(i18n.t, me({}), CLIENT_GREETING);
    expect(withFirst).toBe(without);
  });

  it('treats a whitespace-only name as no name', async () => {
    await i18n.changeLanguage('uz');
    expect(greetingFor(i18n.t, me({ displayName: '   ' }), CLIENT_GREETING))
      .toBe(greetingFor(i18n.t, me({}), CLIENT_GREETING));
  });

  it('handles a missing session without inventing anybody', async () => {
    await i18n.changeLanguage('uz');
    expect(greetingFor(i18n.t, null, CLIENT_GREETING).trim()).toBeTruthy();
    expect(greetingFor(i18n.t, undefined, CLIENT_GREETING).trim()).toBeTruthy();
  });

  it.each(['uz', 'ru'])('is localized in %s', async (lng) => {
    await i18n.changeLanguage(lng);
    const named = greetingFor(i18n.t, me({ displayName: CUSTOMER }), CLIENT_GREETING);
    const anon = greetingFor(i18n.t, me({}), CLIENT_GREETING);
    expect(named).toContain(CUSTOMER);
    expect(anon).not.toContain('greeting');     // not a raw key
    expect(anon).not.toBe(named);
  });

  it('applies the same rule to the welcome screen', async () => {
    await i18n.changeLanguage('uz');
    const out = greetingFor(i18n.t, me({ firstName: OPERATOR }), WELCOME_GREETING);
    expect(out).not.toContain(OPERATOR);
  });
});
