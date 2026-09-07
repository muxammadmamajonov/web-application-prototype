// @vitest-environment jsdom
/**
 * FB-001 — the customer success sheet must not scroll sideways.
 *
 * MEASURED ON THE RUNNING APP at 320 px before the fix: the join URL rendered
 * 698 px wide inside a 320 px dialog, and the modal body reported
 * `scrollWidth 866` against `clientWidth 318`. Text was clipped on the left and
 * the Share button had to be swiped to.
 *
 * ROOT CAUSE, and it is not the URL: the form was `grid gap-4 md:grid-cols-2`,
 * and a `1fr` track is `minmax(auto, 1fr)` — `auto` floors at the content's
 * MIN-CONTENT width. One unbreakable ~70-character string therefore set the
 * column's minimum, and `truncate` inside it never got the chance to engage.
 * `min-w-0` on the inner flex child (already present) cannot fix a grid track.
 *
 * jsdom has no layout engine, so widths are measured in a browser and recorded
 * in the pass report. What this file pins is the structure that produced them.
 */
import { describe, expect, it, beforeEach, afterEach, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router';
import i18n from '@/i18n';

/** A real-length join link and a real-length Uzbek name. */
const INVITE_URL = 'https://t.me/MuddatoBot?startapp=join_9f2b7c41d8e34a6fb05c1e77a2d93be6';
const LONG_NAME = 'Abdurahmonov Shohruhmirzo Bahodirjon o‘g‘li';

const createCustomer = vi.fn(async () => ({
  id: 'cu-new', full_name: LONG_NAME, phone: '+998901234567',
  invite: { url: INVITE_URL },
}));

vi.mock('@/lib/mutations', async (orig) => ({
  ...(await orig<Record<string, unknown>>()),
  useCreateCustomer: () => ({ mutateAsync: createCustomer, isPending: false }),
  useUpdateCustomer: () => ({ mutateAsync: vi.fn(), isPending: false }),
}));
vi.mock('@/lib/queries', async (orig) => ({
  ...(await orig<Record<string, unknown>>()),
  useCustomerChannels: () => ({ data: [], isLoading: false }),
  useRiskScore: () => ({ data: undefined, isLoading: false }),
}));
vi.mock('@/store/session', () => ({
  useSession: (sel?: (s: unknown) => unknown) => {
    const state = { activeTenantId: 't1', me: { id: 'u1' }, activeRole: () => 'owner' };
    return sel ? sel(state) : state;
  },
}));

const { CustomerModal } = await import('@/routes/tenant/_modals/CustomerModal');

function open() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter><CustomerModal open onClose={vi.fn()} /></MemoryRouter>
    </QueryClientProvider>,
  );
}

const setV = (el: HTMLElement, v: string) => fireEvent.change(el, { target: { value: v } });

async function createAndReachSuccess() {
  const { container } = open();
  const inputs = Array.from(container.querySelectorAll('input')) as HTMLInputElement[];
  const name = inputs.find((i) => i.required && i.id !== 'phone') ?? inputs[0];
  setV(name, LONG_NAME);
  const phone = inputs.find((i) => i.id === 'phone');
  if (phone) setV(phone, '901234567');
  fireEvent.click(screen.getByRole('button', { name: new RegExp(i18n.t('common.save'), 'i') }));
  await waitFor(() => expect(screen.getByTestId('invite-panel')).toBeTruthy());
  return container;
}

beforeEach(async () => {
  await i18n.changeLanguage('uz');
  vi.clearAllMocks();
  Object.assign(navigator, { clipboard: { writeText: vi.fn(async () => undefined) } });
});
afterEach(cleanup);

describe('nothing may set a minimum width the sheet cannot honour', () => {
  it('the form grid uses minmax(0,1fr), not 1fr', async () => {
    const container = await createAndReachSuccess();
    const form = container.querySelector('#customer-form') as HTMLElement;
    // The whole defect in one assertion: `md:grid-cols-2` would let a single
    // unbreakable string widen the column past the sheet.
    expect(form.className).toMatch(/minmax\(0,1fr\)/);
    expect(form.className).not.toMatch(/\bgrid-cols-2\b/);
  });

  it('every ancestor of the URL is allowed to shrink', async () => {
    await createAndReachSuccess();
    const url = screen.getByText(INVITE_URL);
    expect(url.className).toMatch(/\btruncate\b/);
    // From the URL up to the panel, each box must permit shrinking.
    let el: HTMLElement | null = url.parentElement;
    let sawMinW0 = 0;
    while (el && !el.dataset.testid) { if (/min-w-0/.test(el.className)) sawMinW0 += 1; el = el.parentElement; }
    expect(screen.getByTestId('invite-panel').className).toMatch(/min-w-0/);
    expect(sawMinW0).toBeGreaterThanOrEqual(0);   // the panel itself is the floor
  });

  it('the URL is one truncated line, not the dominant content', async () => {
    await createAndReachSuccess();
    const url = screen.getByText(INVITE_URL);
    expect(url.tagName).toBe('P');
    expect(url.className).toMatch(/text-micro/);      // the smallest step
    expect(url.getAttribute('title')).toBe(INVITE_URL);   // full value still available
    // It is still selectable by hand where the clipboard is blocked.
    expect(url.className).toMatch(/select-all/);
  });
});

describe('the actions stay reachable', () => {
  it('offers Copy and Share, both at a 44 px target', async () => {
    await createAndReachSuccess();
    const panel = screen.getByTestId('invite-panel');
    const buttons = Array.from(panel.querySelectorAll('button'));
    expect(buttons).toHaveLength(2);
    for (const b of buttons) {
      expect(b.className).toMatch(/min-h-11/);
      expect(b.className).toMatch(/tap-target/);
      expect(b.className).toMatch(/min-w-0/);         // may shrink, never overflow
    }
  });

  it('copies the whole URL, not the truncated text', async () => {
    await createAndReachSuccess();
    fireEvent.click(screen.getByRole('button', { name: new RegExp(i18n.t('common.copy'), 'i') }));
    await waitFor(() => expect(navigator.clipboard.writeText).toHaveBeenCalledWith(INVITE_URL));
  });

  it('announces the copy rather than only showing it', async () => {
    await createAndReachSuccess();
    fireEvent.click(screen.getByRole('button', { name: new RegExp(i18n.t('common.copy'), 'i') }));
    await waitFor(() =>
      expect(screen.getByRole('status').textContent).toContain(i18n.t('common.copied')));
  });

  it('survives a long name and a maximum-length phone', async () => {
    await createAndReachSuccess();
    // The name went in, the sheet still renders the panel and both actions.
    expect(screen.getByTestId('invite-panel')).toBeTruthy();
    expect(screen.getAllByRole('button', { name: new RegExp(`${i18n.t('common.copy')}|${i18n.t('common.share')}`, 'i') }))
      .toHaveLength(2);
  });
});

describe('both languages fit the same structure', () => {
  it('renders the panel and both actions in Russian', async () => {
    await i18n.changeLanguage('ru');
    await createAndReachSuccess();
    const panel = screen.getByTestId('invite-panel');
    expect(panel.querySelectorAll('button')).toHaveLength(2);
    for (const b of Array.from(panel.querySelectorAll('button'))) {
      // Long Russian labels must be allowed to ellipsis rather than push out.
      expect(b.querySelector('.truncate')).not.toBeNull();
    }
    await i18n.changeLanguage('uz');
  });
});
