// @vitest-environment jsdom
/**
 * OBS-002 — S-52 `ClientInstallmentDetailPage` renders four mutually exclusive
 * states and, before this pass, titled none of them with an `h1`.
 *
 * `RouteFocusManager` moves focus into `<main>` on every navigation, so the
 * first thing a screen-reader user meets on this screen is whatever the page
 * opened with. S-52 opened with a `<div>` shop name, a `<div>` product name and
 * a run of `<h2>` section labels — a document outline that starts at level 2
 * and never states which page it is.
 *
 * The count is asserted in EVERY branch, because a heading added to the success
 * state is a heading still missing from loading, transient-error and not-found
 * — and four headings added by hand are a duplicate waiting to happen.
 *
 * The heading TEXT is pinned to `routeTitleKeyFor()` rather than to a literal,
 * so the page heading, the Telegram header (`MiniAppHeader`, same helper) and
 * the document title (`RouteTitleManager`, same helper) cannot drift apart, and
 * so this test fails if someone hardcodes customer-facing copy here.
 */
import { describe, expect, it, beforeEach, afterEach, vi } from 'vitest';
import { cleanup, render, waitFor } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import i18n from '@/i18n';
import { api } from '@/lib/api';
import { installDevMock } from '@/lib/devMock';
import { routeTitleKeyFor } from '@/lib/routeTitle';

const LOCALES = ['uz', 'ru'] as const;
const PATH = '/uz/me/installments/i1';
const savedAdapter = api.defaults.adapter;

/** Drives `MiniAppHeader`, which renders its chrome title only inside Telegram. */
let INSIDE_TG = false;
vi.mock('@/lib/telegram', async (orig) => ({
  ...(await orig<Record<string, unknown>>()),
  isInsideTelegramClient: () => INSIDE_TG,
}));

/**
 * Overrides the query result so each mutually exclusive branch can be entered
 * deliberately. `null` ⇒ fall through to the real hook over the devMock
 * transport, which is how the success state is reached.
 */
let STATE: Record<string, unknown> | null = null;
vi.mock('@/lib/queries', async (orig) => {
  const real = await orig<Record<string, unknown>>();
  return {
    ...real,
    useMyInstallment: (id?: string) =>
      STATE ?? (real.useMyInstallment as (i?: string) => unknown)(id),
  };
});

import ClientInstallmentDetailPage from './InstallmentDetailPage';
import { ClientShell } from '@/components/layout/ClientShell';
import { bottomNavFor } from '@/routes/_navConfig';

/** `errorKind()` reads `response.status`; 503 ⇒ transient, 404 ⇒ notFound. */
const failure = (status: number) => ({
  isLoading: false, isError: true, error: { response: { status } },
  data: undefined, refetch: vi.fn(),
});

const STATES: Array<[string, Record<string, unknown> | null]> = [
  ['loading', { isLoading: true, isError: false, error: null, data: undefined, refetch: vi.fn() }],
  ['a transient fetch failure', failure(503)],
  ['a missing installment', failure(404)],
  ['the loaded detail', null],
];

function renderDetail(node: React.ReactNode = <ClientInstallmentDetailPage />) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={[PATH]}>
        <Routes><Route path="/:locale/me/installments/:id" element={node} /></Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

const h1s = (root: ParentNode) => Array.from(root.querySelectorAll('h1'));

/** What every title-bearing surface for this path resolves to. */
const routeTitle = (lng: string) =>
  i18n.getFixedT(lng)(routeTitleKeyFor(PATH) as string) as string;

beforeEach(() => {
  STATE = null;
  INSIDE_TG = false;
  vi.spyOn(console, 'info').mockImplementation(() => {});
  installDevMock();
});
afterEach(async () => {
  cleanup();
  vi.restoreAllMocks();
  api.defaults.adapter = savedAdapter;
  await i18n.changeLanguage('uz');
});

describe.each(LOCALES)('S-52 is a titled page in %s', (lng) => {
  beforeEach(async () => { await i18n.changeLanguage(lng); });

  it.each(STATES)('has exactly one h1 while showing %s', async (_label, state) => {
    STATE = state;
    const { container } = renderDetail();
    // The success branch has to settle before it is the branch under test.
    if (state === null) {
      await waitFor(() => expect(container.querySelector('section')).toBeTruthy());
    }
    expect(h1s(container)).toHaveLength(1);
    expect(h1s(container)[0]!.textContent).toBe(routeTitle(lng));
  });

  it('titles the page from the shared route-title map, not a literal', () => {
    // Guards the copy source: if the h1 stops agreeing with the helper that
    // titles the document and the Telegram header, the three drift apart.
    expect(routeTitleKeyFor(PATH)).toBe('nav.installments');
    STATE = STATES[0]![1];
    const { container } = renderDetail();
    expect(h1s(container)[0]!.textContent).toBe(routeTitle(lng));
    expect(h1s(container)[0]!.textContent).not.toBe('');
  });
});

/**
 * COR-01 — the contract is exactly one h1 in the WHOLE rendered document, in
 * every environment, not "one per page subtree".
 *
 * The earlier version of this suite counted only the page's own contribution
 * and therefore accepted a real collision: inside Telegram `MiniAppHeader`
 * emitted its own `h1` for the chrome title, so a Mini-App user's document had
 * two competing page headings. Counting the subtree made that invisible. These
 * assertions mount the REAL `ClientShell` and count `h1` across the entire
 * container, so the collision cannot hide again.
 */
describe.each(LOCALES)('S-52 inside the real ClientShell in %s', (lng) => {
  beforeEach(async () => { await i18n.changeLanguage(lng); });

  const renderInShell = () => {
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const nav = bottomNavFor('client', lng, i18n.getFixedT(lng) as never);
    return render(
      <QueryClientProvider client={qc}>
        <MemoryRouter initialEntries={[`/${lng}/me/installments/i1`]}>
          <Routes>
            <Route
              path="/:locale/me/installments/:id"
              element={
                <ClientShell navItems={nav}>
                  <ClientInstallmentDetailPage />
                </ClientShell>
              }
            />
          </Routes>
        </MemoryRouter>
      </QueryClientProvider>,
    );
  };

  it.each([['outside Telegram', false], ['inside Telegram', true]] as const)(
    'has exactly one h1 in the whole document %s', async (_label, tg) => {
      INSIDE_TG = tg;
      const { container } = renderInShell();
      await waitFor(() => expect(container.querySelector('main')).toBeTruthy());
      const found = h1s(container);
      expect(found, found.map((e) => `<h1>${e.textContent}</h1>`).join(' + ')).toHaveLength(1);
      // …and it is the page's own, localized heading — not the chrome title.
      expect(found[0]!.textContent).toBe(i18n.getFixedT(lng)('nav.installments'));
      expect(found[0]!.closest('main'), 'the single h1 must live inside <main>').toBeTruthy();
    });
});
