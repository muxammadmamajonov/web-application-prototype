// @vitest-environment jsdom
/**
 * QA-016 (frontend half) — the platform plan-catalogue cap and a tenant's
 * subscription limit must not resolve to the same sentence.
 *
 * `POST /api/platform/plans` refuses a sixth plan with 409
 * `platform_plan_limit_reached`; `POST /api/tenant/installments` refuses a
 * contract over the shop's ceiling with 402 `plan_limit_reached` plus a
 * structured `limit`. Those are different actors and different remedies.
 *
 * Two independent failures produced one wrong message, and both are pinned:
 *
 *   1. `errors.ts` had no entry for the catalogue cap, so the platform 409 fell
 *      through to the tenant's "Tarif limiti to'ldi … upgrade your plan";
 *   2. `CreatePlanModal` tried to special-case it with
 *      `apiErrorMessage(ex) === 'plan_limit_reached'` — comparing a TRANSLATED
 *      sentence against a machine code, which can never be true, so the branch
 *      had never once executed. The same dead comparison covered `plan_exists`
 *      and `reserved_code`.
 *
 * The modal half is a runtime test, not a source check: a comparison that is
 * always false looks perfectly reasonable in source.
 */
import { describe, expect, it, beforeEach, afterEach, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import i18n from '@/i18n';
import { apiErrorMessage, errorCode } from '@/lib/errors';

const LOCALES = ['uz', 'ru'] as const;

/** An axios-shaped rejection, as the interceptor hands it to a caller. */
const httpError = (status: number, detail: unknown) =>
  ({ response: { status, data: { detail } } });

/** The two refusals, exactly as their endpoints serialize them. */
const PLATFORM_CAP = httpError(409, 'platform_plan_limit_reached');
const TENANT_CAP = httpError(402, {
  error: 'plan_limit_reached', limit_key: 'contract', limit: 25, message: 'server prose',
});

// ── 1. mapping ──────────────────────────────────────────────────────────────

describe.each(LOCALES)('the two plan-limit refusals in %s', (lng) => {
  beforeEach(async () => { await i18n.changeLanguage(lng); });
  afterEach(async () => { await i18n.changeLanguage('uz'); });

  it('do not resolve to the same message', () => {
    expect(apiErrorMessage(PLATFORM_CAP)).not.toBe(apiErrorMessage(TENANT_CAP));
  });

  it('give the catalogue cap its own localized copy', () => {
    const msg = apiErrorMessage(PLATFORM_CAP);
    expect(msg).toBe(i18n.getFixedT(lng)('errors.platform_plan_limit_reached'));
    // Not the generic 409 fallback, and not raw backend text.
    expect(msg).not.toBe(i18n.getFixedT(lng)('errors.conflict'));
    expect(msg).not.toContain('plan_limit');
    expect(msg.trim().length).toBeGreaterThan(0);
  });

  it('leave the tenant subscription message exactly as it was', () => {
    expect(apiErrorMessage(TENANT_CAP))
      .toBe(i18n.getFixedT(lng)('errors.plan_limit_reached_n', { limit: 25 }));
    expect(apiErrorMessage(httpError(402, { error: 'plan_limit_reached' })))
      .toBe(i18n.getFixedT(lng)('errors.plan_limit_reached'));
  });
});

// ── 2. runtime — what the operator actually sees ────────────────────────────

const toasts: Array<{ kind: string; text: string }> = [];
vi.mock('@/lib/feedback', async (orig) => ({
  ...(await orig<Record<string, unknown>>()),
  toast: {
    error: (text: string) => toasts.push({ kind: 'error', text }),
    success: (text: string) => toasts.push({ kind: 'success', text }),
    info: (text: string) => toasts.push({ kind: 'info', text }),
  },
}));

let REJECTION: unknown = PLATFORM_CAP;
vi.mock('@/lib/mutations', async (orig) => ({
  ...(await orig<Record<string, unknown>>()),
  useCreatePlan: () => ({ mutateAsync: () => Promise.reject(REJECTION), isPending: false }),
}));

import { CreatePlanModal } from './_modals/CreatePlanModal';

function renderModal() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <CreatePlanModal open onClose={() => {}} />
    </QueryClientProvider>,
  );
}

/** Fill the required fields and submit. */
async function submitValidPlan(lng: 'uz' | 'ru') {
  const t = i18n.getFixedT(lng);
  renderModal();
  fireEvent.change(screen.getByLabelText(t('platformPlans.code') as string, { exact: false }), { target: { value: 'lite2' } });
  fireEvent.change(screen.getByLabelText(t('platformPlans.nameUz') as string, { exact: false }), { target: { value: 'Lite 2' } });
  fireEvent.change(screen.getByLabelText(t('platformPlans.nameRu') as string, { exact: false }), { target: { value: 'Лайт 2' } });
  fireEvent.submit(document.querySelector('#create-plan-form')!);
}

describe.each(LOCALES)('creating a plan at the catalogue cap, in %s', (lng) => {
  beforeEach(async () => {
    cleanup();
    toasts.length = 0;
    REJECTION = PLATFORM_CAP;
    await i18n.changeLanguage(lng);
  });
  afterEach(async () => { cleanup(); await i18n.changeLanguage('uz'); });

  it('tells the operator about the five-plan ceiling, not about upgrading a tariff', async () => {
    await submitValidPlan(lng);
    const t = i18n.getFixedT(lng);
    await waitFor(() => expect(toasts).toHaveLength(1));
    expect(toasts[0]!.kind).toBe('error');
    expect(toasts[0]!.text).toBe(t('platformPlans.limitReached'));
    expect(toasts[0]!.text).not.toBe(t('errors.plan_limit_reached'));
  });

  it('still recognises the sibling 409 on the same route', async () => {
    REJECTION = httpError(409, 'plan_exists');
    await submitValidPlan(lng);
    await waitFor(() => expect(toasts).toHaveLength(1));
    expect(toasts[0]!.text).toBe(i18n.getFixedT(lng)('platformPlans.exists'));
  });

  it('and the reserved-code refusal', async () => {
    REJECTION = httpError(400, 'reserved_code');
    await submitValidPlan(lng);
    await waitFor(() => expect(toasts).toHaveLength(1));
    expect(toasts[0]!.text).toBe(i18n.getFixedT(lng)('platformPlans.reserved'));
  });

  it('falls back to the localized message for an unrecognised failure', async () => {
    REJECTION = httpError(500, 'boom');
    await submitValidPlan(lng);
    await waitFor(() => expect(toasts).toHaveLength(1));
    expect(toasts[0]!.text).toBe(apiErrorMessage(REJECTION));
    expect(toasts[0]!.text).not.toContain('boom');
  });
});

// ── 3. the comparison that could never be true ──────────────────────────────

describe('a machine code is never compared against a translated sentence', () => {
  it('because the two are different values by construction', () => {
    expect(errorCode(PLATFORM_CAP)).toBe('platform_plan_limit_reached');
    expect(apiErrorMessage(PLATFORM_CAP)).not.toBe(errorCode(PLATFORM_CAP));
  });
});
