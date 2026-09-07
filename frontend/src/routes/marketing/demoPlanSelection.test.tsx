// @vitest-environment jsdom
/**
 * #18 — the plan on a demo lead is one the visitor actually picked.
 *
 * The form used to default to the POPULAR plan whenever `?plan=` was absent or
 * named a tier that no longer existed. So a visitor who opened the demo page
 * directly submitted a plan they had never seen, sales opened the conversation
 * on the wrong tier, and the lead record presented it as the visitor's choice.
 * A blank required field beats a confident wrong answer.
 *
 * Kept apart from `demoLeadCapture.test.tsx` because that file mocks the catalog
 * as EMPTY — which is its own case (the field is hidden and not enforced, so a
 * catalog outage cannot strand a lead) and cannot express these.
 */
import { describe, expect, it, beforeEach, afterEach, vi } from 'vitest';
import { render, screen, cleanup, waitFor, fireEvent } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import i18n from '@/i18n';

const PLANS = [
  { code: 'lite', name_uz: 'Lite', name_ru: 'Лайт', is_contact_sales: false, is_popular: false },
  { code: 'pro', name_uz: 'Pro', name_ru: 'Про', is_contact_sales: false, is_popular: true },
  { code: 'enterprise', name_uz: 'Korxona', name_ru: 'Предприятие', is_contact_sales: true, is_popular: false },
];

vi.mock('@/components/layout/Logo', () => ({ Logo: () => <span>Muddato</span> }));
vi.mock('@/lib/queries', async (orig) => ({
  ...(await orig<Record<string, unknown>>()),
  usePublicPlans: () => ({ data: PLANS, isLoading: false, isError: false, error: null, refetch: vi.fn() }),
}));

const post = vi.fn();
vi.mock('@/lib/api', () => ({ api: { post: (...a: unknown[]) => post(...a) } }));

import DemoPage from './DemoPage';

function renderAt(search = '') {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={[`/uz/demo${search}`]}>
        <Routes><Route path="/:locale/demo" element={<DemoPage />} /></Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

function fillRequiredExceptPlan() {
  const boxes = [...document.querySelectorAll('input')].filter((i) => i.type !== 'checkbox') as HTMLInputElement[];
  fireEvent.change(boxes[0], { target: { value: 'Demo Electronics' } });
  fireEvent.change(boxes[1], { target: { value: 'Sardor Mamatov' } });
  fireEvent.change(boxes[2], { target: { value: '+998 90 123 45 67' } });
  fireEvent.click(screen.getByRole('checkbox'));
}

/**
 * The shared Select is a listbox, never a native <select> (platform standard).
 * A pick is: open the combobox, then mouseDown the option — the component
 * commits on mousedown with preventDefault so the trigger keeps focus rather
 * than the list stealing it and closing before the click lands.
 */
function pickPlan(label: string) {
  fireEvent.click(screen.getByRole('combobox'));
  const option = screen.getByRole('option', { name: label });
  fireEvent.mouseDown(option.querySelector('button') ?? option);
}

const submitBtn = () =>
  screen.getByRole('button', { name: new RegExp(i18n.getFixedT('uz')('demo.submit'), 'i') });

beforeEach(() => {
  post.mockReset();
  // jsdom does not implement it, and Select scrolls the active option into view
  // when the list opens — without this the listbox throws instead of opening.
  Element.prototype.scrollIntoView = vi.fn();
});
afterEach(cleanup);

describe('arriving from a pricing card', () => {
  it('preselects that plan and submits its code', async () => {
    post.mockResolvedValue({ data: { ok: true } });
    renderAt('?plan=lite');
    fillRequiredExceptPlan();
    fireEvent.click(submitBtn());
    await waitFor(() => expect(post).toHaveBeenCalledTimes(1));
    expect((post.mock.calls[0][1] as Record<string, unknown>).plan_code).toBe('lite');
  });

  it('honours a non-popular plan rather than overriding it with the popular one', async () => {
    // The old default fired whenever the code did not match, and `pro` is the
    // popular tier — so this is the case that silently rewrote the visitor's pick.
    post.mockResolvedValue({ data: { ok: true } });
    renderAt('?plan=lite');
    fillRequiredExceptPlan();
    fireEvent.click(submitBtn());
    await waitFor(() => expect(post).toHaveBeenCalledTimes(1));
    expect((post.mock.calls[0][1] as Record<string, unknown>).plan_code).not.toBe('pro');
  });
});

describe('opening the form with no plan context', () => {
  it('does not submit a hidden default', () => {
    renderAt();
    fillRequiredExceptPlan();
    fireEvent.click(submitBtn());
    expect(post).not.toHaveBeenCalled();
  });

  it('says which field is missing', () => {
    renderAt();
    fillRequiredExceptPlan();
    fireEvent.click(submitBtn());
    expect(screen.getByText(i18n.getFixedT('uz')('demo.errPlan'))).toBeTruthy();
  });

  it('submits once the visitor picks one', async () => {
    post.mockResolvedValue({ data: { ok: true } });
    renderAt();
    fillRequiredExceptPlan();
    pickPlan('Lite');
    fireEvent.click(submitBtn());
    await waitFor(() => expect(post).toHaveBeenCalledTimes(1));
    expect((post.mock.calls[0][1] as Record<string, unknown>).plan_code).toBe('lite');
  });
});

describe('a stale or hostile ?plan=', () => {
  it.each(['gone', 'enterprise'])('clears %s instead of substituting a guess', (code) => {
    // `gone` no longer exists; `enterprise` is contact-sales and is not
    // demo-able. Either way the answer is an empty required field, not a
    // different plan chosen on the visitor's behalf.
    renderAt(`?plan=${code}`);
    fillRequiredExceptPlan();
    fireEvent.click(submitBtn());
    expect(post).not.toHaveBeenCalled();
    expect(screen.getByText(i18n.getFixedT('uz')('demo.errPlan'))).toBeTruthy();
  });
});

describe('the options offered', () => {
  it('excludes contact-sales tiers', () => {
    renderAt();
    fireEvent.click(screen.getByRole('combobox'));
    const labels = screen.getAllByRole('option').map((o) => o.textContent);
    expect(labels).toContain('Lite');
    expect(labels).toContain('Pro');
    // `enterprise` is contact-sales — you cannot self-serve a demo of it.
    expect(labels).not.toContain('Korxona');
  });
});
