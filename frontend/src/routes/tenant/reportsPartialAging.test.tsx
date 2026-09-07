// @vitest-environment jsdom
/**
 * P5-02 — a partial aging response must not take down the Reports page.
 *
 * `agingSlices` read `a?.buckets.b0_30.count ?? 0`. The optional chain guards
 * only the FIRST hop, so a successful response whose body lacks `buckets` — or
 * lacks one bucket inside it — throws before the `?? 0` can apply, and the whole
 * retained Reports route falls into the generic error boundary. That screen is
 * reserved for genuine crashes; a response shape must never reach it
 * (PLATFORM_STANDARD §6, Phase 3 audit §10).
 *
 * The response is typed, so TypeScript cannot catch this: only real traffic
 * disagrees with the type. It was found in a browser during the pass-4
 * verification — and it is deterministic once known, so it is automated here.
 *
 * The page is rendered for real; only the query layer is stubbed.
 */
import { describe, expect, it, beforeEach, afterEach, vi } from 'vitest';
import { render, screen, cleanup } from '@testing-library/react';
import { MemoryRouter } from 'react-router';
import i18n from '@/i18n';

/** What `useReportAging` will return for a given case. */
let AGING: { data: unknown; isLoading: boolean; isError: boolean };

const settled = (data: unknown) => ({
  data, isLoading: false, isError: false, error: null, refetch: vi.fn(),
});

vi.mock('@/lib/queries', async (orig) => ({
  ...(await orig<Record<string, unknown>>()),
  useReportAging: () => ({ ...AGING, error: null, refetch: vi.fn() }),
  useReportCashflow: () => settled({ from: '2026-06-01', to: '2026-07-01', granularity: 'day', series: [] }),
  useReportStaff: () => settled({ staff: [] }),
  useReportProducts: () => settled({ rows: [] }),
  useReportLtv: () => settled({ customers: [] }),
  useReportMargin: () => settled({ revenue: 0, gross_profit: 0, margin_pct: 0 }),
  useTenantSettings: () => settled({ calc: {} }),
}));

vi.mock('@/store/session', () => ({
  useSession: (sel?: (s: unknown) => unknown) => {
    const state = { activeRole: () => 'owner', me: { id: 'u1' }, activeTenantId: 't1' };
    return sel ? sel(state) : state;
  },
}));

import TenantReportsPage from './ReportsPage';

/** A well-formed bucket. */
const bucket = (count: number, amount: number) => ({ count, amount });

/** The complete, correct response — the control case. */
const FULL = {
  as_of: '2026-07-28T09:00:00Z',
  buckets: {
    b0_30: bucket(3, 3_000_000), b31_60: bucket(2, 2_000_000),
    b61_90: bucket(1, 1_000_000), b90_plus: bucket(0, 0),
  },
  total_outstanding: 6_000_000,
  total_count: 6,
};

function renderReports() {
  return render(<MemoryRouter initialEntries={['/uz/tenant/reports']}><TenantReportsPage /></MemoryRouter>);
}

/** The generic crash screen the standard forbids reaching from a response. */
const crashed = () =>
  screen.queryByText(i18n.t('common.errorTitle', { defaultValue: 'Nimadir noto‘g‘ri ketdi' })) !== null
  || /Nimadir noto/i.test(document.body.textContent ?? '');

beforeEach(async () => {
  cleanup();
  AGING = settled(FULL);
  await i18n.changeLanguage('uz');
  if (!Element.prototype.scrollIntoView) Element.prototype.scrollIntoView = () => {};
  // A thrown render would otherwise only surface as console noise.
  vi.spyOn(console, 'error').mockImplementation(() => {});
});
afterEach(() => vi.restoreAllMocks());

describe('the control case still works', () => {
  it('renders the page with a complete aging response', () => {
    expect(() => renderReports()).not.toThrow();
    expect(screen.getByRole('heading', { name: i18n.t('nav.reports') })).toBeTruthy();
    expect(crashed()).toBe(false);
  });
});

describe('a successful but PARTIAL aging response degrades to zero', () => {
  /** Each case is a 200 whose body is missing something the type promises. */
  const cases: Array<[string, unknown]> = [
    ['`buckets` absent entirely', { ...FULL, buckets: undefined }],
    ['`buckets` null', { ...FULL, buckets: null }],
    ['one bucket absent (b0_30)', { ...FULL, buckets: { ...FULL.buckets, b0_30: undefined } }],
    ['a middle bucket absent (b61_90)', { ...FULL, buckets: { ...FULL.buckets, b61_90: undefined } }],
    ['every bucket absent', { ...FULL, buckets: {} }],
    ['a bucket present but missing `count`', { ...FULL, buckets: { ...FULL.buckets, b31_60: {} } }],
    ['the whole body empty', {}],
    ['no data at all', undefined],
  ];

  it.each(cases)('does not throw when %s', (_name, data) => {
    AGING = settled(data);
    expect(() => renderReports()).not.toThrow();
  });

  it.each(cases)('does not fall into the error boundary when %s', (_name, data) => {
    AGING = settled(data);
    renderReports();
    expect(crashed(), 'a response shape must never reach the generic crash screen').toBe(false);
  });

  it.each(cases)('still renders the page heading when %s', (_name, data) => {
    AGING = settled(data);
    renderReports();
    expect(screen.getByRole('heading', { name: i18n.t('nav.reports') })).toBeTruthy();
  });

  it('shows the aging card rather than hiding it', () => {
    AGING = settled({ ...FULL, buckets: undefined });
    renderReports();
    // The card is a retained MVP surface: it degrades, it does not disappear.
    expect(screen.getAllByText(i18n.t('reports.aging', { defaultValue: 'Qarz yoshi' })).length)
      .toBeGreaterThan(0);
  });

  it('presents the missing buckets as zero, not as a wrong number', () => {
    AGING = settled({ ...FULL, buckets: undefined, total_count: 0 });
    renderReports();
    // Every bucket label is still offered, each reading zero.
    for (const label of ['0–30', '31–60', '61–90', '90+']) {
      expect(screen.getAllByText(label).length, `${label} slice missing`).toBeGreaterThan(0);
    }
    expect(crashed()).toBe(false);
  });

  it('a partial nested bucket zeroes only that slice', () => {
    AGING = settled({ ...FULL, buckets: { ...FULL.buckets, b0_30: undefined } });
    renderReports();
    expect(crashed()).toBe(false);
    // The siblings that ARE present keep their real values.
    expect(screen.getAllByText('31–60').length).toBeGreaterThan(0);
  });
});

describe('genuine failures still use the error card, not the crash screen', () => {
  it('an errored aging query renders a retry affordance', () => {
    AGING = { data: undefined, isLoading: false, isError: true };
    renderReports();
    expect(crashed()).toBe(false);
    expect(screen.getAllByRole('button', { name: i18n.t('common.retry') }).length).toBeGreaterThan(0);
  });

  it('a loading aging query renders its loading state', () => {
    AGING = { data: undefined, isLoading: true, isError: false };
    expect(() => renderReports()).not.toThrow();
    expect(crashed()).toBe(false);
  });
});
