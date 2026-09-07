// @vitest-environment jsdom
/**
 * "Standart tarif" must survive a save and a reload.
 *
 * THE BUG (production, 2026-08-11). Sozlamalar → Asosiy qiymatlar saved the whole
 * group under ONE settings key, `defaults`:
 *
 *     saveGroup('defaults', { default_plan, default_locale, … })
 *       → PUT /platform/settings/defaults
 *
 * …and loaded it from four SEPARATE keys — `default_plan`, `default_locale`,
 * `default_timezone`, `trial_days`. Nothing ever wrote those rows, so nothing
 * ever read the row that was written. Pick Starter, save, reload: the Select is
 * empty. The value was never lost; it was never looked for.
 *
 * This is pinned as a genuine ROUND TRIP against a store that behaves like
 * `PUT /platform/settings/{key}` (upsert by key; non-dict values wrapped as
 * `{v: …}`), because the defect lived precisely in the seam between the write
 * and the read. A test that asserted on the request body, or on the form state
 * after a save, would have passed throughout — the PUT was always correct.
 *
 * The second case pins the legacy flat-row shape: an install configured before
 * the group existed must still show its stored plan, or "fix the round trip"
 * becomes "silently discard what production already holds".
 */
import { describe, expect, it, beforeEach, afterEach, vi } from 'vitest';
import { cleanup, configure, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import type { AxiosAdapter } from 'axios';
import { api } from '@/lib/api';

import PlatformSettingsPage from './SettingsPage';

/**
 * This file drives ten sequential `waitFor`s through a full render → select →
 * save → remount round trip. React Testing Library's default 1000ms budget per
 * wait is comfortable on an idle machine and marginal on the CI host, which runs
 * five runner slots plus Docker builds on one Mac mini — there this file failed
 * at 1715ms while passing 5/5 locally. The assertions were correct; the machine
 * was busy.
 *
 * Scoped to this file on purpose. Raising `asyncUtilTimeout` globally breaks
 * tests that assert an ABSENCE by letting a short wait lapse (e.g.
 * receiptSurfaces.test.tsx), so the budget belongs where the round trip is.
 */
configure({ asyncUtilTimeout: 5_000 });

const savedAdapter = api.defaults.adapter;

/** The platform_settings table, as far as this test is concerned. */
let STORE: Record<string, unknown> = {};

const PLANS = [
  { id: 'p1', code: 'starter',  name_uz: 'Starter',  name_ru: 'Starter',  is_active: true,  sort_order: 1 },
  { id: 'p2', code: 'business', name_uz: 'Business', name_ru: 'Business', is_active: true,  sort_order: 2 },
  { id: 'p3', code: 'pro',      name_uz: 'Pro',      name_ru: 'Pro',      is_active: true,  sort_order: 3 },
];

/** Stands in for the API: same key-addressed upsert the real endpoint performs. */
const adapter: AxiosAdapter = async (config) => {
  const url = (config.url ?? '').split('?')[0];
  const method = (config.method ?? 'get').toLowerCase();
  const ok = (data: unknown) => ({ data, status: 200, statusText: 'OK', headers: {}, config });

  if (method === 'get' && url === '/platform/settings') {
    return ok({ items: Object.entries(STORE).map(([key, value]) => ({ key, value, description: null })) });
  }
  if (method === 'get' && url === '/platform/plans') return ok({ items: PLANS });
  if (method === 'put' && url.startsWith('/platform/settings/')) {
    const key = url.slice('/platform/settings/'.length);
    const body = JSON.parse(String(config.data ?? '{}')) as { value: unknown };
    // The endpoint stores a dict as-is and wraps anything else as {v: …}.
    STORE[key] = (body.value && typeof body.value === 'object' && !Array.isArray(body.value))
      ? body.value
      : { v: body.value };
    return ok({ key, value: STORE[key] });
  }
  return ok({ items: [] });
};

function renderPage() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={['/uz/platform/settings?tab=defaults']}>
        <Routes>
          <Route path="/:locale/platform/settings" element={<PlatformSettingsPage />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

/** The default-plan combobox. Both panes render (one is CSS-hidden), so the
 *  first match is the desktop copy and the two always agree. */
async function planLabel(): Promise<string> {
  const boxes = await screen.findAllByRole('combobox');
  return boxes[0].textContent ?? '';
}

describe('platform Settings · Standart tarif persistence', () => {
  beforeEach(() => {
    STORE = {};
    api.defaults.adapter = adapter;
    // jsdom has no layout, so the listbox's scroll-into-view is a no-op here.
    Element.prototype.scrollIntoView = vi.fn();
  });
  afterEach(() => { cleanup(); api.defaults.adapter = savedAdapter; vi.restoreAllMocks(); });

  it('keeps the chosen tariff after save + reload', async () => {
    const first = renderPage();
    await waitFor(async () => expect(await planLabel()).not.toBe(''));

    // Pick Starter from the live catalogue. `Select` commits on mousedown.
    fireEvent.click((await screen.findAllByRole('combobox'))[0]);
    fireEvent.mouseDown((await screen.findAllByRole('button', { name: /Starter/i }))[0]);
    await waitFor(async () => expect(await planLabel()).toMatch(/Starter/i));

    fireEvent.click((await screen.findAllByRole('button', { name: /^(Saqlash|Сохранить)$/i }))[0]);
    await waitFor(() => expect(Object.keys(STORE)).toContain('defaults'));

    // A reload: fresh cache, fresh mount, same server-side store.
    first.unmount();
    renderPage();

    // THE ASSERTION. Before the fix this read "—": the save went to `defaults`
    // and the load looked at `default_plan`, so the Select had no value to show.
    await waitFor(async () => expect(await planLabel()).toMatch(/Starter/i));
    expect(await planLabel()).not.toMatch(/mavjud emas|не существу/i);
  });

  it('still shows a plan stored under the pre-group flat key', async () => {
    // What an install configured before the group row looks like. The scalar is
    // wrapped, because that is what PUT /platform/settings/{key} does with one.
    STORE = { default_plan: { v: 'business' } };
    renderPage();
    await waitFor(async () => expect(await planLabel()).toMatch(/Business/i));
  });

  it('falls back to real defaults when nothing is stored — not to blank and 0', async () => {
    // FOUND IN THE BROWSER, not by the tests above (2026-08-11). The first cut of
    // the group reader called `get(key, undefined)` to mean "no fallback". But
    // `get`'s fallback is a DEFAULT PARAMETER, so passing `undefined` explicitly
    // selects it — the call returned `''`, and `'' ?? fallback` is `''` because
    // `??` only catches null and undefined. On a settings page whose store is
    // empty, every Select rendered "—" and `trial_days` became `Number('')` → 0:
    // the literal "falls back to 0" symptom, reintroduced by the fix for it.
    //
    // Asserted on the SAVE PAYLOAD as well as the screen, because that 0 is what
    // would have been written back and made permanent.
    STORE = {};
    renderPage();
    await waitFor(async () => expect(await planLabel()).toBeDefined());

    const labels = async () => (await screen.findAllByRole('combobox')).map((b) => b.textContent ?? '');
    await waitFor(async () => expect((await labels())[1]).toMatch(/O'zbekcha|Русский/));
    expect((await labels())[2]).toMatch(/Asia\/Tashkent/);

    fireEvent.click((await screen.findAllByRole('button', { name: /^(Saqlash|Сохранить)$/i }))[0]);
    await waitFor(() => expect(STORE.defaults).toBeDefined());
    const saved = STORE.defaults as Record<string, unknown>;
    expect(saved.default_locale).toBe('uz');
    expect(saved.default_timezone).toBe('Asia/Tashkent');
    expect(saved.trial_days).toBe(7);
    expect(saved.trial_days).not.toBe(0);
  });

  it('treats a blank stored value as absent rather than rendering it', async () => {
    // A group row whose fields were cleared must not blank the form: the point of
    // a default is that it applies when nothing is configured.
    STORE = { defaults: { default_plan: '', default_locale: '', default_timezone: '', trial_days: '' } };
    renderPage();
    const labels = async () => (await screen.findAllByRole('combobox')).map((b) => b.textContent ?? '');
    await waitFor(async () => expect((await labels())[1]).toMatch(/O'zbekcha|Русский/));
    expect((await labels())[2]).toMatch(/Asia\/Tashkent/);
  });

  it('keeps a stored plan the catalogue no longer contains visible and marked', async () => {
    // The `lite` era: a stored code with no matching plan must stay VISIBLE and
    // be marked, never silently swapped for whichever plan happens to be first —
    // that reads as "the default is Starter" while shops are stamped `lite`.
    STORE = { defaults: { default_plan: 'lite' } };
    renderPage();
    await waitFor(async () => expect(await planLabel()).toMatch(/lite/i));
  });
});
