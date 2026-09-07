/**
 * Global product-catalog writes.
 *
 * Cross-persona by nature: the platform console curates the catalog and a tenant
 * can propose a missing model, so this lives beside neither.
 */

import { useMutation, useQueryClient } from '@tanstack/react-query';
import { queryKeys } from '../queryKeys';
import { api } from '../api';

interface CategoryWrite {
  slug: string;
  name_uz: string;
  name_ru: string;
  icon_emoji?: string | null;
  icon_url?: string | null;
  attribute_schema?: import('../catalogSchema').AttrField[];
  identifier_schema?: import('../catalogSchema').IdField[];
  sort_order?: number;
  is_active?: boolean;
}

export function useUpdateCategory() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, body }: { id: string; body: CategoryWrite }) =>
      api.patch(`/catalog/categories/${id}`, body).then((r) => r.data),
    onSuccess: () => qc.invalidateQueries({ queryKey: queryKeys.catalog.all() }),
  });
}

export function useUpdateBrand() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, body }: { id: string; body: { category_ids: string[]; slug: string; name: string; logo_url?: string | null } }) =>
      api.patch(`/catalog/brands/${id}`, body).then((r) => r.data),
    onSuccess: () => qc.invalidateQueries({ queryKey: queryKeys.catalog.all() }),
  });
}

export function useUpdateCatalogModel() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, body }: { id: string; body: Record<string, unknown> }) =>
      api.patch(`/catalog/models/${id}`, body).then((r) => r.data),
    onSuccess: () => qc.invalidateQueries({ queryKey: queryKeys.catalog.all() }),
  });
}

// ── platform: catalog management ───────────────────────────────────────────

export function useCreateCategory() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: CategoryWrite) =>
      api.post('/catalog/categories', body).then((r) => r.data),
    onSuccess: () => qc.invalidateQueries({ queryKey: queryKeys.catalog.all() }),
  });
}

type BrandWrite = { category_ids: string[]; slug: string; name: string; logo_url?: string | null; sort_order?: number; is_active?: boolean };

export function useCreateBrand() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: BrandWrite) => api.post('/catalog/brands', body).then((r) => r.data),
    onSuccess: () => qc.invalidateQueries({ queryKey: queryKeys.catalog.all() }),
  });
}

type ModelWrite = {
  brand_id: string; category_id: string; slug: string; name: string;
  release_year?: number | null; image_url?: string | null;
  attributes_template?: Record<string, string[]>;
  sort_order?: number; is_active?: boolean;
};

export function useCreateCatalogModel() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: ModelWrite) => api.post('/catalog/models', body).then((r) => r.data),
    onSuccess: () => qc.invalidateQueries({ queryKey: queryKeys.catalog.all() }),
  });
}

interface ModelProposeBody {
  brand_id: string;
  category_id: string;
  name: string;
  release_year?: number | null;
  attributes_template?: Record<string, string[]>;
}

/** A tenant proposes a model missing from the global catalog. Tenant-scoped. */
export function useProposeModel() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: ModelProposeBody) => api.post('/catalog/models/propose', body).then((r) => r.data),
    onSuccess: () => qc.invalidateQueries({ queryKey: queryKeys.catalog.modelsAll() }),
  });
}

export function useDeleteCatalogCategory() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => api.delete(`/catalog/categories/${id}`).then((r) => r.data),
    onSuccess: () => qc.invalidateQueries({ queryKey: queryKeys.catalog.all() }),
  });
}

export function useDeleteCatalogBrand() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => api.delete(`/catalog/brands/${id}`).then((r) => r.data),
    onSuccess: () => qc.invalidateQueries({ queryKey: queryKeys.catalog.all() }),
  });
}

export function useDeleteCatalogModel() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => api.delete(`/catalog/models/${id}`).then((r) => r.data),
    onSuccess: () => qc.invalidateQueries({ queryKey: queryKeys.catalog.all() }),
  });
}
