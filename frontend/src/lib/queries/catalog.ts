/**
 * Global product-catalog reads.
 *
 * Cross-persona by nature: the platform console curates the catalog and the
 * tenant contract wizard picks from it, so this lives beside neither.
 */

import { keepPreviousData, useQuery } from '@tanstack/react-query';
import { api } from '../api';
import { queryKeys } from '../queryKeys';

// ── catalog (public reads) ──────────────────────────────────────────────────

export interface CatalogCategoryRow {
  id: string; slug: string; name_uz: string; name_ru: string;
  icon_key: string | null; icon_emoji: string | null; icon_url: string | null;
  attribute_schema: import('../catalogSchema').AttrField[];
  identifier_schema: import('../catalogSchema').IdField[];
  business_types: string[];
  sort_order: number; is_active: boolean;
  brand_count: number;
}
export interface CatalogBrandRow {
  id: string; category_id: string | null; category_ids: string[]; slug: string; name: string;
  logo_url: string | null; sort_order: number; is_active: boolean;
  model_count: number;
}
export interface CatalogModelRow {
  id: string; brand_id: string; category_id: string | null; slug: string; name: string;
  release_year: number | null; image_url: string | null;
  attributes_template: Record<string, string[]>;
  sort_order: number; is_active: boolean;
}
export interface CatalogColorRow {
  id: string; slug: string; name_uz: string; name_ru: string;
  hex: string | null; sort_order: number; is_active: boolean;
}

export function useCatalogCategories(businessType?: string | null) {
  return useQuery({
    queryKey: queryKeys.catalog.categories(businessType ?? 'all'),
    queryFn: async ({ signal }) => (await api.get<CatalogCategoryRow[]>('/catalog/categories', {
      params: businessType ? { business_type: businessType } : undefined,
      signal,
    })).data,
    staleTime: 5 * 60_000,
  });
}

export function useCatalogBrands(categorySlug: string | null | undefined) {
  return useQuery({
    queryKey: queryKeys.catalog.brands(categorySlug),
    enabled: !!categorySlug,
    queryFn: async ({ signal }) => (await api.get<CatalogBrandRow[]>(`/catalog/categories/${categorySlug}/brands`, { signal })).data,
    staleTime: 5 * 60_000,
  });
}

export function useCatalogModels(brandId: string | null | undefined, categoryId?: string | null) {
  return useQuery({
    queryKey: queryKeys.catalog.models(brandId, categoryId ?? 'all'),
    enabled: !!brandId,
    queryFn: async ({ signal }) => (await api.get<CatalogModelRow[]>(`/catalog/brands/${brandId}/models`, {
      params: categoryId ? { category_id: categoryId } : undefined,
      signal,
    })).data,
    staleTime: 5 * 60_000,
  });
}

/** Flat model list for the platform Products subpage (filter by category/brand + search). */
export function useCatalogModelsFlat(params: { category_id?: string; brand_id?: string; q?: string }) {
  return useQuery({
    queryKey: queryKeys.catalog.modelsFlat(params),
    placeholderData: keepPreviousData,  // (#621)
    queryFn: async ({ signal }) => (await api.get<CatalogModelRow[]>('/catalog/models', {
      params: {
        category_id: params.category_id || undefined,
        brand_id: params.brand_id || undefined,
        q: params.q || undefined,
      },
      signal,
    })).data,
    staleTime: 60_000,
  });
}

export function useCatalogColors() {
  return useQuery({
    queryKey: queryKeys.catalog.colors(),
    queryFn: async ({ signal }) => (await api.get<CatalogColorRow[]>('/catalog/colors', { signal })).data,
    staleTime: 5 * 60_000,
  });
}

/** All brands across categories (for the Brands subpage + product create picker). */
export function useAllCatalogBrands() {
  return useQuery({
    queryKey: queryKeys.catalog.allBrands(),
    // (#199) One request to the batched /catalog/brands endpoint — replaces the old
    // 1 + N per-category fan-out (13+ round-trips) that made the brands page slow.
    queryFn: async ({ signal }) => (await api.get<CatalogBrandRow[]>('/catalog/brands', { signal })).data,
    staleTime: 2 * 60_000,
  });
}
