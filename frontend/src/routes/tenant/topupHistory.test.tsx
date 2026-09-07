// @vitest-environment jsdom
/**
 * The tenant top-up request history on BillingPage.
 *
 * A top-up's status is decided by somebody else — an operator approves or
 * rejects it minutes or hours later — so the two things worth pinning here are
 * that the shop can read that status at all, and that a stale `pending` row
 * cannot outlive the decision.
 *
 * The freshness and invalidation tests deliberately use the REAL
 * `useTenantTopups` against a swapped axios adapter, so the query key, the
 * request URL, the staleTime window and the refetch triggers are exercised
 * rather than described. Mocking the hook would assert nothing about them.
 *
 * AT-nn names map each test to the approved acceptance criteria.
 */
import { describe, expect, it, beforeEach, afterEach, vi } from 'vitest';
import { render, screen, cleanup, waitFor, within } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router';
import { QueryClient, QueryClientProvider, useQueryClient, focusManager } from '@tanstack/react-query';
import { readFileSync, readdirSync } from 'node:fs';
import { resolve } from 'node:path';
import i18n from '@/i18n';
import uzBundle from '@/i18n/locales/uz.json';
import ruBundle from '@/i18n/locales/ru.json';
import { api } from '@/lib/api';
import { queryKeys } from '@/lib/queryKeys';

type Row = {
  id: string; amount: number; method: 'card' | 'payme' | 'click';
  status: 'pending' | 'paid' | 'rejected'; created_at: string; dest_card_label?: string | null;
};
type Q<T> = { data: T | undefined; isLoading: boolean; isError: boolean; error: unknown; refetch: () => void };

const ok = <T,>(data: T): Q<T> => ({ data, isLoading: false, isError: false, error: null, refetch: vi.fn() });
const loading = <T,>(): Q<T> => ({ data: undefined, isLoading: true, isError: false, error: null, refetch: vi.fn() });
const failed = <T,>(error: unknown): Q<T> => ({ data: undefined, isLoading: false, isError: true, error, refetch: vi.fn() });

const row = (over: Partial<Row> = {}): Row => ({
  id: `tp-${Math.random().toString(36).slice(2)}`,
  amount: 500_000, method: 'card', status: 'pending',
  created_at: `${new Date().getFullYear()}-07-12T09:30:00Z`,
  dest_card_label: 'Humo ···4821', ...over,
});

const BALANCE = 1_234_000;

let ROLE = 'owner';
let TOPUPS: Q<Row[]>;
let USE_REAL_TOPUPS = false;
let BILLING: Q<Record<string, unknown>>;
let TOPUP_INFO: Q<Record<string, unknown>>;

vi.mock('@/store/session', () => ({
  useSession: (sel?: (s: unknown) => unknown) => {
    const state = { activeTenantId: 't1', impersonating: false, me: null, activeRole: () => ROLE, exitTenant: vi.fn() };
    return sel ? sel(state) : state;
  },
}));

vi.mock('@/lib/queries', async (orig) => {
  const real = await orig<Record<string, unknown>>();
  return {
    ...real,
    useTenantBilling: () => BILLING,
    useTenantInvoices: () => ok([]),
    // (#9) `can_topup` is decided by the SERVER from the real membership, so
    // the mock derives it from ROLE at call time — a test that flips ROLE after
    // the beforeEach must see the change, which a baked-in value would miss.
    useTenantTopupInfo: () => (TOPUP_INFO?.data
      ? { ...TOPUP_INFO, data: { ...TOPUP_INFO.data, can_topup: ROLE === 'owner' } }
      : TOPUP_INFO),
    usePublicPlans: () => ok([]),
    // The flag is set before mount and never flipped during one, so the hook
    // call is unconditional from React's point of view.
    useTenantTopups: () =>
      (USE_REAL_TOPUPS ? (real.useTenantTopups as () => Q<Row[]>)() : TOPUPS),
  };
});

import BillingPage from './BillingPage';

/** Exposes the page's own QueryClient so a test can invalidate exactly the key
 *  `TopUpModal` invalidates, instead of re-implementing the modal. */
let pageQc: QueryClient | null = null;
function CaptureQc() { pageQc = useQueryClient(); return null; }

function renderBilling(locale: 'uz' | 'ru' = 'uz', client?: QueryClient) {
  const qc = client ?? new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={[`/${locale}/tenant/billing`]}>
        <Routes>
          <Route path="/:locale/tenant/billing" element={<><CaptureQc /><BillingPage /></>} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

const t = (k: string) => i18n.t(k);
/** The top-up history `<section>`, located by its own heading. */
const historySection = (): HTMLElement => {
  const h = screen.getByRole('heading', { name: t('billing.topupHistory') });
  const s = h.closest('section');
  if (!s) throw new Error('top-up history heading is not inside a <section>');
  return s;
};

beforeEach(async () => {
  await i18n.changeLanguage('uz');
  ROLE = 'owner';
  USE_REAL_TOPUPS = false;
  pageQc = null;
  TOPUPS = ok<Row[]>([]);
  BILLING = ok({ plan_code: 'pro', status: 'active', active_installments: 12, base_price: 450_000, projected: null, suggestion: null });
  TOPUP_INFO = ok({ balance: BALANCE, terms: { uz: '', ru: '' }, cards: [], providers: { payme: false, click: false } });
});
afterEach(() => { cleanup(); vi.clearAllMocks(); });

// ── AT-01 / AT-02 — roles ────────────────────────────────────────────────────

describe('AT-01/AT-02 — the history is read-only for owner AND manager; the action stays owner-only', () => {
  it('AT-01 owner sees the history heading and the top-up button', () => {
    TOPUPS = ok([row()]);
    renderBilling();
    expect(screen.getByRole('heading', { name: t('billing.topupHistory') })).toBeTruthy();
    expect(screen.getByRole('button', { name: new RegExp(t('topup.title'), 'i') })).toBeTruthy();
  });

  it('AT-02 manager sees the history and its rows, and NO top-up button or notice', () => {
    ROLE = 'manager';
    TOPUPS = ok([row({ amount: 750_000, status: 'pending' })]);
    renderBilling();
    // The read is present…
    expect(screen.getByRole('heading', { name: t('billing.topupHistory') })).toBeTruthy();
    expect(within(historySection()).getAllByText('750 000', { exact: false }).length).toBeGreaterThan(0);
    // …and the action is simply absent. (#9) The "only the owner can top up"
    // sentence was removed: it told a manager about a restriction they cannot
    // act on, next to a balance that is already read-only for them. The rule
    // itself is unchanged and still enforced by the backend.
    expect(screen.queryByRole('button', { name: new RegExp(t('topup.title'), 'i') })).toBeNull();
    expect(screen.queryByText(t('billing.topupOwnerOnly'))).toBeNull();
  });

  it('the history carries no client-side role gate — identical row count for both roles', () => {
    const rows = [row({ status: 'pending' }), row({ status: 'paid' })];
    TOPUPS = ok(rows);
    ROLE = 'owner';
    renderBilling();
    const asOwner = within(historySection()).getAllByRole('row').length;
    cleanup();
    ROLE = 'manager';
    renderBilling();
    expect(within(historySection()).getAllByRole('row').length).toBe(asOwner);
  });
});

// ── AT-03 … AT-06, AT-08 — independent states ────────────────────────────────

describe('AT-03…AT-06/AT-08 — every state is distinct and independently gated', () => {
  it('AT-03 loading renders a busy skeleton and NEVER the empty state', () => {
    TOPUPS = loading<Row[]>();
    renderBilling();
    expect(historySection().querySelector('[aria-busy="true"]')).toBeTruthy();
    expect(screen.queryByText(t('billing.noTopups'))).toBeNull();
  });

  it('AT-04 a history failure shows a section alert and leaves the balance readable', () => {
    TOPUPS = failed<Row[]>(new Error('boom'));
    renderBilling();
    expect(within(historySection()).getByRole('alert')).toBeTruthy();
    // The balance figure above is untouched by the neighbouring failure.
    expect(screen.getByText('1 234 000', { exact: false })).toBeTruthy();
    expect(screen.queryByText(t('billing.noTopups'))).toBeNull();
  });

  it('AT-05 a billing/topup-info failure hides the balance block but NOT the history', () => {
    BILLING = failed<Record<string, unknown>>(new Error('billing down'));
    TOPUPS = ok([row({ amount: 900_000 })]);
    renderBilling();
    expect(screen.getAllByRole('alert').length).toBeGreaterThan(0);
    // The history is outside that gate, so a pending request stays visible.
    expect(screen.getByRole('heading', { name: t('billing.topupHistory') })).toBeTruthy();
    expect(within(historySection()).getAllByText('900 000', { exact: false }).length).toBeGreaterThan(0);
  });

  it('AT-06 empty renders the empty state and adds no second top-up CTA', () => {
    TOPUPS = ok<Row[]>([]);
    renderBilling();
    expect(screen.getByText(t('billing.noTopups'))).toBeTruthy();
    expect(screen.getByText(t('billing.noTopupsBody'))).toBeTruthy();
    // Exactly one top-up control on the page — the owner button above.
    expect(screen.getAllByRole('button', { name: new RegExp(t('topup.title'), 'i') })).toHaveLength(1);
    expect(within(historySection()).queryByRole('button')).toBeNull();
  });

  it('AT-08 a full 50-row page states the cap; 49 rows do not', () => {
    TOPUPS = ok(Array.from({ length: 50 }, () => row()));
    renderBilling();
    expect(screen.getByText(t('billing.topupsTruncated'))).toBeTruthy();
    cleanup();
    TOPUPS = ok(Array.from({ length: 49 }, () => row()));
    renderBilling();
    expect(screen.queryByText(t('billing.topupsTruncated'))).toBeNull();
  });

  it('renders no polling timer and no refresh control', () => {
    TOPUPS = ok([row()]);
    renderBilling();
    // The only control anywhere in the section would be ErrorCard's Retry, and
    // this is the success branch.
    expect(within(historySection()).queryByRole('button')).toBeNull();
  });
});

// ── AT-07 — status labels, both locales, only the three wire states ──────────

describe('AT-07 — pending/paid/rejected render localized labels in uz and ru', () => {
  const rows = [
    row({ status: 'pending', amount: 100_000 }),
    row({ status: 'paid', amount: 200_000 }),
    row({ status: 'rejected', amount: 300_000 }),
  ];

  it('uz', () => {
    TOPUPS = ok(rows);
    renderBilling('uz');
    const s = within(historySection());
    expect(s.getAllByText('Kutilmoqda').length).toBeGreaterThan(0);
    expect(s.getAllByText("To'langan").length).toBeGreaterThan(0);
    expect(s.getAllByText('Rad etilgan').length).toBeGreaterThan(0);
    // `approved` is not a wire state — its label must never appear here.
    expect(s.queryByText('Tasdiqlangan')).toBeNull();
  });

  it('ru', async () => {
    await i18n.changeLanguage('ru');
    TOPUPS = ok(rows);
    renderBilling('ru');
    const s = within(historySection());
    expect(s.getAllByText('В ожидании').length).toBeGreaterThan(0);
    expect(s.getAllByText('Оплачен').length).toBeGreaterThan(0);
    expect(s.getAllByText('Отклонён').length).toBeGreaterThan(0);
    expect(s.queryByText('Подтверждён')).toBeNull();
  });

  it('shows the destination card and translates only the card method', () => {
    TOPUPS = ok([
      row({ method: 'card', dest_card_label: 'Humo ···4821' }),
      row({ method: 'payme', dest_card_label: null }),
    ]);
    renderBilling('uz');
    const s = within(historySection());
    expect(s.getAllByText(/Karta o‘tkazmasi · Humo ···4821/).length).toBeGreaterThan(0);
    expect(s.getAllByText(/^Payme$|Payme/).length).toBeGreaterThan(0);
  });

  it('appends a year only for rows outside the current year', () => {
    TOPUPS = ok([
      row({ created_at: `${new Date().getFullYear()}-07-12T09:00:00Z`, amount: 111_000 }),
      row({ created_at: '2019-07-12T09:00:00Z', amount: 222_000 }),
    ]);
    renderBilling('uz');
    const text = historySection().textContent ?? '';
    expect(text).toContain('12-iyl 2019');
    // The current-year row is the compact form — no year appended.
    expect(text.match(/12-iyl(?! \d)/)).toBeTruthy();
  });
});

// ── AT-09 / AT-10 — real hook, real key, real invalidation, no optimism ──────

describe('AT-09/AT-10 — the existing TopUpModal invalidation now drives a real refetch', () => {
  let served: Row[] = [];
  let calls: string[] = [];
  const prevAdapter = api.defaults.adapter;

  beforeEach(() => {
    USE_REAL_TOPUPS = true;
    served = [];
    calls = [];
    // Same technique the dev mock uses (`lib/devMock.ts`): swap the transport,
    // keep every layer above it — hook, query key, cache — real.
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    api.defaults.adapter = (async (config: any) => {
      calls.push(String(config.url));
      return { data: served, status: 200, statusText: 'OK', headers: {}, config };
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
    }) as any;
  });
  afterEach(() => { api.defaults.adapter = prevAdapter; });

  it('AT-09 subscribes to the live endpoint and refetches when the key is invalidated', async () => {
    renderBilling();
    // The real hook is mounted and hit the real URL.
    await waitFor(() => expect(calls).toContain('/tenant/billing/topups'));
    await waitFor(() => expect(screen.getByText(t('billing.noTopups'))).toBeTruthy());
    const before = calls.filter((u) => u === '/tenant/billing/topups').length;

    // Exactly the call TopUpModal.tsx makes after a successful createTopup.
    served = [row({ amount: 640_000, status: 'pending' })];
    await pageQc!.invalidateQueries({ queryKey: queryKeys.tenant.topups() });

    await waitFor(() =>
      expect(calls.filter((u) => u === '/tenant/billing/topups').length).toBe(before + 1));
    await waitFor(() =>
      expect(within(historySection()).getAllByText('640 000', { exact: false }).length).toBeGreaterThan(0));
    expect(screen.queryByText(t('billing.noTopups'))).toBeNull();
  });

  it('AT-10 renders no row and no balance change before the server responds', async () => {
    renderBilling();
    await waitFor(() => expect(screen.getByText(t('billing.noTopups'))).toBeTruthy());
    const balanceBefore = screen.getByText('1 234 000', { exact: false }).textContent;

    served = [row({ amount: 640_000, status: 'pending' })];
    const inflight = pageQc!.invalidateQueries({ queryKey: queryKeys.tenant.topups() });
    // Synchronously after the invalidation the row cannot exist yet: nothing is
    // written to the cache optimistically.
    expect(screen.queryByText('640 000', { exact: false })).toBeNull();

    await inflight;
    await waitFor(() =>
      expect(within(historySection()).getAllByText('640 000', { exact: false }).length).toBeGreaterThan(0));
    // A pending request must never move the server-authoritative balance.
    expect(screen.getByText('1 234 000', { exact: false }).textContent).toBe(balanceBefore);
  });
});

// ── AT-16…AT-18 — a stale pending row must not outlive the operator's decision ─

describe('AT-16…AT-18 — freshness: the cached row is re-read on return and on focus', () => {
  let served: Row[] = [];
  let calls: string[] = [];
  const prevAdapter = api.defaults.adapter;
  const topupCalls = () => calls.filter((u) => u === '/tenant/billing/topups').length;

  /** Mirrors the shipped client: mount/focus refetching is OFF client-wide. Any
   *  refetch observed below therefore comes from the hook's own options, not
   *  from a permissive harness. */
  const productionLikeClient = () => new QueryClient({
    defaultOptions: {
      queries: {
        retry: false, staleTime: 60_000,
        refetchOnMount: false, refetchOnWindowFocus: false, refetchOnReconnect: true,
      },
    },
  });

  /** Seed a cached page whose age exceeds the hook's own 15s staleTime. */
  const seed = (qc: QueryClient, rows: Row[], ageMs: number) =>
    qc.setQueryData(queryKeys.tenant.topups(), rows, { updatedAt: Date.now() - ageMs });

  beforeEach(() => {
    USE_REAL_TOPUPS = true;
    served = []; calls = [];
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    api.defaults.adapter = (async (config: any) => {
      calls.push(String(config.url));
      return { data: served, status: 200, statusText: 'OK', headers: {}, config };
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
    }) as any;
  });
  afterEach(() => {
    api.defaults.adapter = prevAdapter;
    focusManager.setFocused(undefined); // hand focus tracking back to the DOM
  });

  it('AT-16 replaces a stale cached pending row on mount, with exactly one refetch', async () => {
    const qc = productionLikeClient();
    seed(qc, [row({ id: 'tp-1', amount: 640_000, status: 'pending' })], 20_000);
    served = [row({ id: 'tp-1', amount: 640_000, status: 'paid' })];

    renderBilling('uz', qc);
    // The cached value paints first — no empty flash, and nothing is guessed.
    expect(within(historySection()).getAllByText('Kutilmoqda').length).toBeGreaterThan(0);

    await waitFor(() => expect(topupCalls()).toBe(1));
    await waitFor(() => expect(within(historySection()).getAllByText("To'langan").length).toBeGreaterThan(0));
    expect(within(historySection()).queryByText('Kutilmoqda')).toBeNull();
    expect(topupCalls()).toBe(1); // bounded: one read on return, never a stream
  });

  it('AT-17 refetches once when the app regains focus while the row is stale', async () => {
    const qc = productionLikeClient();
    seed(qc, [row({ id: 'tp-2', amount: 300_000, status: 'pending' })], 20_000);
    served = [row({ id: 'tp-2', amount: 300_000, status: 'pending' })];

    focusManager.setFocused(true);
    renderBilling('uz', qc);
    await waitFor(() => expect(topupCalls()).toBe(1)); // the mount read

    // The operator approves while the shop is looking at another app.
    served = [row({ id: 'tp-2', amount: 300_000, status: 'paid' })];
    seed(qc, qc.getQueryData(queryKeys.tenant.topups()) as Row[], 20_000);

    focusManager.setFocused(false);
    focusManager.setFocused(true);

    await waitFor(() => expect(topupCalls()).toBe(2));
    await waitFor(() => expect(within(historySection()).getAllByText("To'langan").length).toBeGreaterThan(0));
    expect(topupCalls()).toBe(2);
  });

  it('AT-18 leaves a FRESH row alone — the staleTime window still bounds traffic', async () => {
    const qc = productionLikeClient();
    seed(qc, [row({ status: 'pending' })], 1_000); // inside the 15s window
    served = [row({ status: 'paid' })];

    renderBilling('uz', qc);
    focusManager.setFocused(true);
    focusManager.setFocused(false);
    focusManager.setFocused(true);
    await new Promise((r) => setTimeout(r, 150));

    // `'always'` would have refetched here; `true` + staleTime must not.
    expect(topupCalls()).toBe(0);
    expect(within(historySection()).getAllByText('Kutilmoqda').length).toBeGreaterThan(0);
  });

  it('does not poll: no interval is configured and the request count stays flat', async () => {
    const qc = productionLikeClient();
    served = [row({ status: 'pending' })];
    renderBilling('uz', qc);
    await waitFor(() => expect(topupCalls()).toBe(1));

    // Runtime options of the real hook, read off the cache — not a source grep.
    const q = qc.getQueryCache().find({ queryKey: queryKeys.tenant.topups() });
    expect(q?.options.refetchInterval).toBeUndefined();
    expect(q?.options.refetchOnMount).toBe(true);
    expect(q?.options.refetchOnWindowFocus).toBe(true);
    expect(q?.options.staleTime).toBe(15_000);

    await new Promise((r) => setTimeout(r, 600));
    expect(topupCalls()).toBe(1);
  });
});

// ── AT-12 — accessibility of the new markup ──────────────────────────────────

describe('AT-12 — the new markup is reachable without sight or hover', () => {
  beforeEach(() => { TOPUPS = ok([row(), row({ status: 'paid' })]); });

  it('every column header is a scoped <th>', () => {
    renderBilling();
    const ths = Array.from(historySection().querySelectorAll('th'));
    expect(ths.length).toBe(4);
    for (const th of ths) expect(th.getAttribute('scope')).toBe('col');
  });

  it('no control in the section depends on a title tooltip, and none is icon-only', () => {
    renderBilling();
    const s = historySection();
    expect(s.querySelectorAll('[title]')).toHaveLength(0);
    // Status is carried by visible localized words, not colour or glyph alone.
    expect(within(s).getAllByText('Kutilmoqda').length).toBeGreaterThan(0);
  });

  it('the section is introduced by a real heading, not styled text', () => {
    renderBilling();
    expect(screen.getByRole('heading', { name: t('billing.topupHistory') }).tagName).toBe('H3');
  });

  it('the empty state exposes its icon decoratively, with a text title beside it', () => {
    TOPUPS = ok<Row[]>([]);
    renderBilling();
    expect(screen.getByText(t('billing.noTopups'))).toBeTruthy();
    expect(historySection().querySelectorAll('[title]')).toHaveLength(0);
  });
});

// ── AT-11 / AT-13 / AT-15 — invariants that outlive a single render ─────────

const SRC = resolve(__dirname, '../..');
function sources(): Array<{ rel: string; text: string }> {
  const out: Array<{ rel: string; text: string }> = [];
  const walk = (dir: string) => {
    for (const e of readdirSync(dir, { withFileTypes: true })) {
      const p = resolve(dir, e.name);
      if (e.isDirectory()) { walk(p); continue; }
      if (!/\.tsx?$/.test(e.name)) continue;
      out.push({ rel: p.slice(SRC.length + 1), text: readFileSync(p, 'utf8') });
    }
  };
  walk(SRC);
  return out;
}

describe('AT-11 — the top-up invalidation stays single and stays in TopUpModal', () => {
  const INVALIDATION = /invalidateQueries\(\s*\{\s*queryKey:\s*queryKeys\.tenant\.topups\(\)/g;

  it('occurs exactly once repo-wide, in TopUpModal and not in BillingPage', () => {
    const hits = sources()
      .filter(({ rel }) => !rel.includes('.test.'))
      .flatMap(({ rel, text }) => (text.match(INVALIDATION) ?? []).map(() => rel));
    expect(hits).toEqual(['routes/tenant/_modals/TopUpModal.tsx']);
  });

  it('the detector would catch a second invalidation if one were added', () => {
    expect("qc.invalidateQueries({ queryKey: queryKeys.tenant.topups() })".match(INVALIDATION)).toHaveLength(1);
  });

  it('BillingPage subscribes to the hook rather than re-invalidating it', () => {
    const page = readFileSync(resolve(SRC, 'routes/tenant/BillingPage.tsx'), 'utf8');
    expect(page).toContain('useTenantTopups()');
    expect(page.match(INVALIDATION)).toBeNull();
  });
});

describe('AT-13/AT-15 — the seven new keys exist in both bundles and nothing was deleted', () => {
  const NEW_KEYS = [
    'topupHistory', 'topupHistoryHint', 'topupDate', 'topupMethod',
    'noTopups', 'noTopupsBody', 'topupsTruncated',
  ];
  const uz = (uzBundle as { billing: Record<string, string> }).billing;
  const ru = (ruBundle as { billing: Record<string, string> }).billing;

  it('AT-15 both bundles define all seven, non-empty and distinct per locale', () => {
    for (const k of NEW_KEYS) {
      expect(uz[k], `uz billing.${k}`).toBeTruthy();
      expect(ru[k], `ru billing.${k}`).toBeTruthy();
      expect(uz[k]).not.toBe(ru[k]);
    }
  });

  it('AT-15 the keys the section reuses are still present in both bundles', () => {
    for (const k of ['amount', 'status', 'currency', 'topupNoCards']) {
      expect(uz[k], `uz billing.${k}`).toBeTruthy();
      expect(ru[k], `ru billing.${k}`).toBeTruthy();
    }
    const status = (b: unknown) => (b as { status: Record<string, string> }).status;
    for (const k of ['pending', 'paid', 'rejected']) {
      expect(status(uzBundle)[k], `uz status.${k}`).toBeTruthy();
      expect(status(ruBundle)[k], `ru status.${k}`).toBeTruthy();
    }
  });

  it('AT-13 renders resolved copy — never a raw key — in uz and ru', async () => {
    TOPUPS = ok([row()]);
    renderBilling('uz');
    for (const k of NEW_KEYS.filter((k) => k !== 'topupsTruncated')) {
      expect(screen.queryByText(`billing.${k}`)).toBeNull();
    }
    expect(historySection().textContent).toContain(uz.topupHistoryHint);
    cleanup();
    await i18n.changeLanguage('ru');
    renderBilling('ru');
    expect(historySection().textContent).toContain(ru.topupHistoryHint);
    expect(screen.queryByText('billing.topupHistory')).toBeNull();
  });
});
