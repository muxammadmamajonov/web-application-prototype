// @vitest-environment jsdom
/**
 * QA-013 — the plan list must reconcile by plan identity, and the platform
 * plans screen must render without React complaining.
 *
 * The audit saw `Each child in a list should have a unique "key" prop … Check
 * the render method of PlatformPlansPage`. The JSX was never keyless: it keys
 * on `p.id`. The devMock's `GET /platform/plans` fixture was a three-field stub
 * (`{ code }` only), so `p.id` was `undefined` on every row and React saw the
 * same (absent) key repeated — the key IDENTITY was missing, not the prop.
 *
 * That is why the fix is the fixture, checked against the shipped `PlanRow`
 * contract and against what `GET /platform/plans` actually serializes, and not
 * an index key: an index key would silence the console on a screen whose entire
 * purpose is adding, reordering and deleting the rows it keys.
 *
 * Two things are pinned here:
 *   1. no unexpected React console error/warning on the real screen through the
 *      real mock transport — the console was silent in CI precisely because
 *      nothing asserted on it;
 *   2. reconciliation follows the plan, not the slot: a DOM node stamped while
 *      it belonged to one plan must still belong to that plan after the list is
 *      reordered, and must be gone after that plan is removed.
 */
import { describe, expect, it, beforeEach, afterEach, vi } from 'vitest';
import { cleanup, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import i18n from '@/i18n';
import { api } from '@/lib/api';
import { installDevMock } from '@/lib/devMock';
import type { PlanRow } from '@/lib/queries';

const LOCALES = ['uz', 'ru'] as const;
const savedAdapter = api.defaults.adapter;

/** Controlled rows for the reconciliation half; `null` ⇒ use the real mock. */
let CONTROLLED: PlanRow[] | null = null;
vi.mock('@/lib/queries', async (orig) => {
  const real = await orig<Record<string, unknown>>();
  return {
    ...real,
    usePlatformPlans: () => {
      const hook = real.usePlatformPlans as () => { data?: PlanRow[] };
      const live = hook();
      return CONTROLLED ? { ...live, data: CONTROLLED, isLoading: false, isError: false } : live;
    },
  };
});

import PlatformPlansPage from './PlansPage';

function renderPage(lng: 'uz' | 'ru' = 'uz') {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={[`/${lng}/platform/plans`]}>
        <Routes><Route path="/:locale/platform/plans" element={<PlatformPlansPage />} /></Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

beforeEach(() => { CONTROLLED = null; installDevMock(); });
afterEach(async () => {
  cleanup();
  vi.restoreAllMocks();
  api.defaults.adapter = savedAdapter;
  await i18n.changeLanguage('uz');
});

// ── 1. the screen renders clean ─────────────────────────────────────────────

/**
 * Pre-existing noise from the pinned react-router version, emitted by the
 * test's own MemoryRouter rather than by anything on this screen. Everything
 * else — React's own diagnostics included — is a failure.
 */
const HARNESS_NOISE = [/React Router Future Flag Warning/];

/**
 * React de-duplicates each distinct warning for the process, so the FIRST
 * render in this file is the only one that can observe a key diagnostic. That
 * is why this is one test and not one per locale: a second, already-deduplicated
 * render would assert nothing. Key identity does not vary by locale; the
 * locale-parameterised test below covers what does.
 */
describe('the platform plans screen', () => {
  it('renders without a React console error or warning', async () => {
    const noisy = (a: unknown[]) => HARNESS_NOISE.some((re) => re.test(a.map(String).join(' ')));
    const errors: unknown[][] = [];
    const warns: unknown[][] = [];
    vi.spyOn(console, 'error').mockImplementation((...a) => { if (!noisy(a)) errors.push(a); });
    vi.spyOn(console, 'warn').mockImplementation((...a) => { if (!noisy(a)) warns.push(a); });
    vi.spyOn(console, 'info').mockImplementation(() => {});

    renderPage();
    // Wait for the ROWS, not the page chrome: the header renders while the
    // query is still loading, and an empty list emits no key diagnostic.
    await waitFor(() => expect(screen.getAllByText('lite').length).toBeGreaterThan(0));

    const fmt = (calls: unknown[][]) => calls.map((c) => c.map(String).join(' ')).join('\n');
    expect(errors, `console.error:\n${fmt(errors)}`).toHaveLength(0);
    expect(warns, `console.warn:\n${fmt(warns)}`).toHaveLength(0);
  });
});

describe.each(LOCALES)('the platform plans screen in %s', (lng) => {
  it('lists every mocked plan exactly once, named in that locale', async () => {
    await i18n.changeLanguage(lng);
    vi.spyOn(console, 'error').mockImplementation(() => {});
    vi.spyOn(console, 'info').mockImplementation(() => {});
    const { data } = await api.get<{ items: PlanRow[] }>('/platform/plans');

    renderPage(lng);
    await waitFor(() => expect(screen.getAllByText(data.items[0]!.code).length).toBeGreaterThan(0));
    for (const p of data.items) {
      const name = lng === 'ru' ? p.name_ru : p.name_uz;
      // Once in the desktop table and once in the mobile card — never more.
      expect(screen.getAllByText(name), `${name} appears once per layout`).toHaveLength(2);
    }
  });
});

// ── 2. the mock fixture carries the identity the list keys on ───────────────

describe('the devMock plan fixture matches the shipped contract', () => {
  it('serves the fields GET /platform/plans serializes, including a unique id', async () => {
    const { data } = await api.get<{ items: PlanRow[] }>('/platform/plans');
    const items = data.items;
    expect(items.length).toBeGreaterThan(1);
    const ids = items.map((p) => p.id);
    expect(ids.every((id) => typeof id === 'string' && id.length > 0), `ids: ${JSON.stringify(ids)}`).toBe(true);
    expect(new Set(ids).size, 'ids are unique').toBe(items.length);
    // The screen reads these directly; a missing one renders as `undefined`.
    for (const p of items) {
      for (const key of ['code', 'name_uz', 'name_ru'] as const) expect(typeof p[key]).toBe('string');
      expect(typeof p.price_monthly).toBe('number');
      expect(typeof p.is_active).toBe('boolean');
      expect(p.max_active_installments === null || typeof p.max_active_installments === 'number').toBe(true);
      expect(p.max_staff === null || typeof p.max_staff === 'number').toBe(true);
    }
  });
});

// ── 3. reconciliation follows the plan, not the slot ────────────────────────

const plan = (id: string, code: string, name: string): PlanRow => ({
  id, code, name_uz: name, name_ru: name,
  description_uz: null, description_ru: null,
  price_monthly: 100_000, max_active_installments: 10,
  features: {}, is_active: true,
  bot_mode: 'shared', allow_custom_domain: false, max_custom_fields: null, max_staff: 3,
  broadcasts_per_month: null, max_branches: null, overage: {},
  is_popular: false, is_public: true, is_contact_sales: false,
  allow_pdf_template_upload: false, allow_api_keys: false, allow_outbound_webhooks: false,
  sms_included_per_month: 0, commission_management: false, multi_branch: false,
  risk_network_access: false, reports_advanced: false, badge: null,
  price_per_installment: null, sort_order: 0,
});
const A = plan('id-a', 'alpha', 'Alpha');
const B = plan('id-b', 'bravo', 'Bravo');
const C = plan('id-c', 'charlie', 'Charlie');

/**
 * One stable element type at the root, so `rerender` UPDATES the mounted tree
 * instead of remounting it — a remount would discard the DOM nodes whose
 * survival is the whole measurement.
 */
let harnessClient = new QueryClient();
/** `rev` only makes each render a distinct element; the tree it returns is
 *  identical, which is what keeps the mounted page mounted. */
function Harness({ rev: _rev }: { rev: number }) {
  return (
    <QueryClientProvider client={harnessClient}>
      <MemoryRouter initialEntries={['/uz/platform/plans']}>
        <Routes><Route path="/:locale/platform/plans" element={<PlatformPlansPage />} /></Routes>
      </MemoryRouter>
    </QueryClientProvider>
  );
}

/**
 * Every `<li>` currently showing `name` — the desktop table row AND the mobile
 * card, which are keyed independently. Stamping only one would leave the other
 * list's key free to regress unnoticed.
 */
const rowsFor = (name: string): HTMLElement[] => {
  const lis = screen.getAllByText(name)
    .map((el) => el.closest('li'))
    .filter((li): li is HTMLLIElement => !!li);
  expect(new Set(lis).size, `${name} renders in both layouts`).toBe(2);
  return Array.from(new Set(lis));
};
const stamp = (name: string, probe: string) =>
  rowsFor(name).forEach((li, i) => li.setAttribute('data-probe', `${probe}-${i}`));
const probed = (probe: string) =>
  Array.from(document.querySelectorAll(`[data-probe^="${probe}-"]`));

describe('plan rows stay associated with their plan', () => {
  beforeEach(() => { harnessClient = new QueryClient({ defaultOptions: { queries: { retry: false } } }); });

  it('carries a stamped node with its plan through a reorder', () => {
    CONTROLLED = [A, B, C];
    const { rerender } = render(<Harness rev={0} />);
    stamp('Bravo', 'bravo');

    CONTROLLED = [C, B, A];
    rerender(<Harness rev={1} />);

    const nodes = probed('bravo');
    expect(nodes, 'the stamped nodes survived the reorder').toHaveLength(2);
    nodes.forEach((n) => {
      expect(n.textContent, 'still belongs to Bravo').toContain('Bravo');
      expect(n.textContent).not.toContain('Charlie');
    });
  });

  it('keeps a stamped node with its plan when another plan is added first', () => {
    CONTROLLED = [B, C];
    const { rerender } = render(<Harness rev={0} />);
    stamp('Bravo', 'bravo');

    CONTROLLED = [A, B, C];
    rerender(<Harness rev={1} />);

    const nodes = probed('bravo');
    expect(nodes).toHaveLength(2);
    nodes.forEach((n) =>
      expect(n.textContent, 'the new first row did not steal Bravo\'s node').toContain('Bravo'));
  });

  it('drops the removed plan and leaves the survivors on their own nodes', () => {
    CONTROLLED = [A, B, C];
    const { rerender } = render(<Harness rev={0} />);
    stamp('Charlie', 'charlie');

    CONTROLLED = [A, C];
    rerender(<Harness rev={1} />);

    expect(screen.queryByText('Bravo')).toBeNull();
    const nodes = probed('charlie');
    expect(nodes).toHaveLength(2);
    nodes.forEach((n) => expect(n.textContent).toContain('Charlie'));
  });
});
