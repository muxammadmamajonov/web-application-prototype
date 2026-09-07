// @vitest-environment jsdom
/**
 * MUDD-147 — the plan-delete button in `PlanDetailDrawer`'s danger zone fed
 * `Button`'s `loading={del.isPending}` correctly, but its children ALSO
 * rendered a manual `{del.isPending ? <Loader2/> : <Trash2/>}` — so a real
 * delete-in-flight showed two spinners (`Button`'s own plus the manual one)
 * instead of one.
 */
import { describe, expect, it, vi, beforeEach, afterEach } from 'vitest';
import { cleanup, render, screen } from '@testing-library/react';
import '@testing-library/jest-dom/vitest';
import { MemoryRouter } from 'react-router';
import i18n from '@/i18n';
import type { PlanRow } from '@/lib/queries';

let deleteIsPending = false;
vi.mock('@/lib/mutations', async (orig) => ({
  ...(await orig<Record<string, unknown>>()),
  useDeletePlan: () => ({ mutateAsync: vi.fn(), isPending: deleteIsPending }),
}));

const { PlanDetailDrawer } = await import('./PlanDetailModal');

const PLAN: PlanRow = {
  code: 'pro', name_uz: 'Pro', name_ru: 'Про', description_uz: null, description_ru: null,
  price_monthly: 500000, is_active: true, is_popular: false, is_contact_sales: false,
  is_public: true, max_active_installments: null, max_staff: 5, broadcasts_per_month: 10,
  sms_included_per_month: 0, bot_mode: 'shared', allow_custom_domain: false,
  multi_branch: false, reports_advanced: false, features: {},
} as PlanRow;

function renderDrawer() {
  return render(
    <MemoryRouter initialEntries={['/uz/platform/plans']}>
      <PlanDetailDrawer plan={PLAN} onClose={vi.fn()} onEdit={vi.fn()} />
    </MemoryRouter>,
  );
}

const deleteBtn = () => screen.getByRole('button', { name: /tarifni o.chirish/i }) as HTMLButtonElement;

beforeEach(async () => { deleteIsPending = false; await i18n.changeLanguage('uz'); });
afterEach(cleanup);

describe('plan delete button', () => {
  it('shows no spinner while idle', () => {
    renderDrawer();
    expect(deleteBtn().querySelectorAll('.animate-spin').length).toBe(0);
  });

  it('shows exactly one spinner while the delete mutation is pending', () => {
    deleteIsPending = true;
    renderDrawer();
    const btn = deleteBtn();
    expect(btn).toHaveAttribute('aria-busy', 'true');
    expect(btn.querySelectorAll('.animate-spin').length).toBe(1);
  });
});
