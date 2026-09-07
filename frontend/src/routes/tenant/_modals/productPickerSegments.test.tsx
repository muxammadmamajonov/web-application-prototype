// @vitest-environment jsdom
/**
 * MUDD-83 — a tenant outside a category's segment could never reach it.
 *
 * `list_categories` filters to the tenant's `business_type` when one is
 * passed; the frontend always passed it, so an electronics shop could never
 * select "Avtomobillar" (auto) or "Mebel" (furniture) even from a picker
 * that already had a "show all" toggle wired up. Two things had to be true
 * before the picker fixed this, neither of which held:
 *
 *   1. Toggling "show all" must expose out-of-segment categories, not just
 *      re-fetch the same segmented list.
 *   2. Those out-of-segment categories must be visually distinguished (a
 *      group header) so the shop knows what it is picking, and — the part a
 *      DOM-less read could not catch — must still be a real, clickable path
 *      to the brand step, not a decorative list.
 *
 * RED (before this fix): the picker rendered one flat grid with no segment
 * concept beyond the toggle itself; "Avtomobillar" was unreachable while
 * segmented, and reachable-but-unlabelled once "show all" was on.
 * GREEN (after): default view shows only the tenant's segment; "show all"
 * splits the grid into "Mening soham" / "Boshqa sohalar", and clicking a
 * category in either group advances to the brand step for that category.
 */
import { describe, expect, it, beforeEach, afterEach, vi } from 'vitest';
import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import i18n from '@/i18n';

const CATEGORIES = [
  { id: 'cat-phone', slug: 'smartfonlar', name_uz: 'Smartfonlar', name_ru: 'Смартфоны',
    icon_key: null, icon_emoji: null, icon_url: null, attribute_schema: [], identifier_schema: [],
    business_types: ['electronics'], sort_order: 1, is_active: true, brand_count: 23 },
  { id: 'cat-auto', slug: 'avtomobillar', name_uz: 'Avtomobillar', name_ru: 'Автомобили',
    icon_key: null, icon_emoji: null, icon_url: null, attribute_schema: [], identifier_schema: [],
    business_types: ['auto'], sort_order: 2, is_active: true, brand_count: 45 },
];

vi.mock('@/lib/queries', async (orig) => ({
  ...(await orig<Record<string, unknown>>()),
  useTenantSettings: () => ({
    data: { profile: { business_type: 'electronics' } }, isLoading: false, isError: false,
  }),
  // Mirrors the real endpoint: a business_type filters to that segment +
  // untagged "universal" rows; `null` (the "show all" case) returns everyone.
  useCatalogCategories: (businessType?: string | null) => ({
    data: businessType
      ? CATEGORIES.filter((c) => !c.business_types.length || c.business_types.includes(businessType))
      : CATEGORIES,
    isLoading: false, isError: false,
  }),
  useCatalogBrands: () => ({ data: [], isLoading: false, isError: false }),
  useCatalogModels: () => ({ data: [], isLoading: false, isError: false }),
}));

const { ProductPickerModal } = await import('@/routes/tenant/_modals/ProductPickerModal');

function open() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <ProductPickerModal open onClose={vi.fn()} onPick={vi.fn()} />
    </QueryClientProvider>,
  );
}

beforeEach(async () => { await i18n.changeLanguage('uz'); });
afterEach(cleanup);

describe('catalog picker respects and can cross the tenant segment', () => {
  it('shows only the segmented categories by default', () => {
    open();
    expect(screen.getByText('Smartfonlar')).toBeTruthy();
    expect(screen.queryByText('Avtomobillar')).toBeNull();
  });

  it('"show all" reveals the out-of-segment category, grouped under its own header', () => {
    open();
    fireEvent.click(screen.getByText(i18n.t('catalog.showAll')));
    expect(screen.getByText(i18n.t('catalog.mySegment'))).toBeTruthy();
    expect(screen.getByText(i18n.t('catalog.otherSegments'))).toBeTruthy();
    expect(screen.getByText('Smartfonlar')).toBeTruthy();
    expect(screen.getByText('Avtomobillar')).toBeTruthy();
  });

  it('an out-of-segment category is a real pick, not a decorative label', () => {
    open();
    fireEvent.click(screen.getByText(i18n.t('catalog.showAll')));
    fireEvent.click(screen.getByText('Avtomobillar'));
    // Advancing to the brand step swaps the search hint's context; the
    // category leaves a trace in the breadcrumb once picked.
    expect(screen.getAllByText('Avtomobillar').length).toBeGreaterThan(0);
    expect(screen.getByText(`2 / 4 · ${i18n.t('catalog.stepBrand')}`)).toBeTruthy();
  });

  it('toggling back to segment-only hides the out-of-segment category again', () => {
    open();
    const toggle = () => screen.getByText(new RegExp(`${i18n.t('catalog.showAll')}|${i18n.t('catalog.showSegment')}`));
    fireEvent.click(toggle());
    expect(screen.getByText('Avtomobillar')).toBeTruthy();
    fireEvent.click(toggle());
    expect(screen.queryByText('Avtomobillar')).toBeNull();
    expect(screen.queryByText(i18n.t('catalog.otherSegments'))).toBeNull();
  });
});
