// @vitest-environment jsdom
/**
 * Phase 9 M-1 — the demo form is a lead-capture form.
 *
 * It is retained (MKT-004 KEEP) while the auto-provisioning behind it is LATER,
 * so what it must do is: validate, submit, and tell the visitor a human will get
 * in touch. What it must NOT do is hand back — or open — anything that reads as
 * an already-provisioned shop.
 */
import { describe, expect, it, beforeEach, afterEach, vi } from 'vitest';
import { render, screen, cleanup, waitFor, fireEvent } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import i18n from '@/i18n';

vi.mock('@/components/layout/Logo', () => ({ Logo: () => <span>Muddato</span> }));
vi.mock('@/lib/queries', async (orig) => ({
  ...(await orig<Record<string, unknown>>()),
  usePublicPlans: () => ({ data: [], isLoading: false, isError: false, error: null, refetch: vi.fn() }),
}));

const post = vi.fn();
vi.mock('@/lib/api', () => ({ api: { post: (...a: unknown[]) => post(...a) } }));

import DemoPage from './DemoPage';

const openSpy = vi.spyOn(window, 'open');

function renderAt(lng: 'uz' | 'ru') {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={[`/${lng}/demo`]}>
        <Routes><Route path="/:locale/demo" element={<DemoPage />} /></Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

/** Fill the three text fields. The contact field accepts a phone or an email. */
function fillFields(contact = '+998 90 123 45 67') {
  const boxes = [...document.querySelectorAll('input')].filter(
    (i) => i.type !== 'checkbox',
  ) as HTMLInputElement[];
  fireEvent.change(boxes[0], { target: { value: 'Demo Electronics' } });
  fireEvent.change(boxes[1], { target: { value: 'Sardor Mamatov' } });
  fireEvent.change(boxes[2], { target: { value: contact } });
}

function submitButton(lng: 'uz' | 'ru') {
  return screen.getByRole('button', { name: new RegExp(i18n.getFixedT(lng)('demo.submit'), 'i') });
}

function fillAndSubmit(lng: 'uz' | 'ru', contact?: string) {
  fillFields(contact);
  fireEvent.click(screen.getByRole('checkbox'));
  fireEvent.click(submitButton(lng));
}

beforeEach(() => { post.mockReset(); openSpy.mockReset(); });
afterEach(cleanup);

describe.each(['uz', 'ru'] as const)('the demo request in %s', (lng) => {
  it('submits the lead to the demo endpoint', async () => {
    post.mockResolvedValue({ data: { ok: true } });
    renderAt(lng);
    fillAndSubmit(lng);
    await waitFor(() => expect(post).toHaveBeenCalledTimes(1));
    expect(post.mock.calls[0][0]).toBe('/demo/intent');
    expect(post.mock.calls[0][1]).toMatchObject({ business_name: 'Demo Electronics' });
  });

  // P9-P2-001. The field takes a phone OR an email, so it must not be posted
  // under a name that means "phone" — the server typed it that way and rejected
  // every email lead with a 422 the visitor never saw.
  it.each([
    ['a phone', '+998 90 123 45 67', '+998 90 123 45 67'],
    ['an email', 'sardor@gmail.com', 'sardor@gmail.com'],
  ])('posts %s under the neutral `contact` key', async (_kind, typed, expected) => {
    post.mockResolvedValue({ data: { ok: true } });
    renderAt(lng);
    fillAndSubmit(lng, typed);
    await waitFor(() => expect(post).toHaveBeenCalledTimes(1));
    const body = post.mock.calls[0][1] as Record<string, unknown>;
    expect(body.contact).toBe(expected);
    expect(body).not.toHaveProperty('phone');
    expect(body).toMatchObject({ business_name: 'Demo Electronics', locale: lng });
  });

  it('keeps an email lead on the form only when it is genuinely invalid', async () => {
    renderAt(lng);
    fillAndSubmit(lng, 'sardor@gmail');       // no TLD — the form's own gate
    expect(post).not.toHaveBeenCalled();
    expect(screen.getByText(i18n.getFixedT(lng)('demo.errContact'))).toBeTruthy();
  });

  it('confirms that a human will follow up, and claims no provisioned shop', async () => {
    post.mockResolvedValue({ data: { ok: true } });
    renderAt(lng);
    fillAndSubmit(lng);

    const t = i18n.getFixedT(lng);
    const heading = await screen.findByText(t('demo.success.title'));
    expect(screen.getByText(t('demo.success.body'))).toBeTruthy();
    // Nothing was opened, and the panel offers no onward link at all. The
    // footer's `t.me` support links are shell chrome, so the assertion is
    // scoped to the panel rather than the document.
    expect(openSpy).not.toHaveBeenCalled();
    const panel = heading.closest('[role="status"]') as HTMLElement;
    expect(panel).toBeTruthy();
    expect(panel.querySelectorAll('a')).toHaveLength(0);
    expect(panel.innerHTML).not.toContain('start=demo_');
  });

  it('shows an accessible error and stays on the form when the request fails', async () => {
    post.mockRejectedValue(new Error('nope'));
    renderAt(lng);
    fillAndSubmit(lng);
    await waitFor(() => expect(screen.getAllByRole('alert').length).toBeGreaterThan(0));
    expect(screen.queryByText(i18n.getFixedT(lng)('demo.success.title'))).toBeNull();
  });

  it('blocks submission until consent is given, and says which field', async () => {
    renderAt(lng);
    const t = i18n.getFixedT(lng);
    fillFields();
    fireEvent.click(submitButton(lng));
    expect(post).not.toHaveBeenCalled();
    expect(screen.getByText(t('demo.consentRequired'))).toBeTruthy();
  });
});

describe('the page keeps no provisioning affordance', () => {
  it('neither imports nor renders a bot deep link', async () => {
    const { readFileSync } = await import('node:fs');
    // Comments stripped: the file explains what the deep link *used* to do, and
    // that rationale is worth keeping. Assertions are about shipped behaviour.
    const code = readFileSync('src/routes/marketing/DemoPage.tsx', 'utf-8')
      .replace(/\/\*[\s\S]*?\*\//g, '')
      .replace(/\/\/.*$/gm, '');
    expect(code).not.toContain('window.open');
    expect(code).not.toContain('telegram_url');
    expect(code).not.toContain('BOT_USERNAME');
  });
});
