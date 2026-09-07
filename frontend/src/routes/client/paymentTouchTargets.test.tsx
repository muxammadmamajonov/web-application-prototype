// @vitest-environment jsdom
/**
 * The customer's payment amount controls must be tappable.
 *
 * `PayPage` and `ReceiptUploadPage` are the most touch-exclusive surfaces in the
 * product — they run inside Telegram on a phone — and both rendered the amount
 * `<input>` with `leading-none` and no padding, collapsing the hit box to the
 * text box (250×20 and 260×20 at 360×740). The quick-pick presets were 25–29 px
 * tall. WCAG 2.5.8 AA asks for 24×24; the platform standard asks for 44×44.
 *
 * The shared `.tap-target` helper is NOT sufficient on its own: globals.css
 * scopes it to `@media (pointer: coarse)`, so on any fine-pointer viewport it
 * applies nothing. Every control in this codebase that genuinely measures 44 px
 * — the row kebabs, the date-picker arrows — pairs it with an explicit `h-11`.
 * These four controls do the same with `min-h-11`, which is unconditional.
 *
 * jsdom has no layout engine, so this file cannot measure pixels. It pins the
 * three facts a layout engine would combine:
 *
 *   1. every cited control carries `min-h-11`, the unconditional floor;
 *   2. the Tailwind spacing scale is unmodified, so `11` really is 2.75rem; and
 *   3. `.tap-target` is still coarse-pointer-only, so nobody "simplifies" the
 *      controls back down to it and silently reintroduces the 20 px box.
 *
 * Real rendered rectangles at 360×740 are measured in a browser and recorded in
 * the pass report; this file is the regression net between those runs.
 */
import { describe, expect, it, beforeEach, afterEach, vi } from 'vitest';
import { cleanup, render, screen } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import i18n from '@/i18n';

const DETAIL = {
  id: 'i1',
  tenant: { id: 't1', name: 'Muddato Shop', logo: null, slug: 'shop' },
  contract_number: 'C-001',
  reference: null,
  status: 'active' as const,
  principal: '7000000', paid_amount: '0', remaining: '7000000',
  monthly_amount: '700000', total_amount: '7000000', down_payment: '0',
  months: 10, paid_count: 0,
  start_date: '2026-01-10', next_due_date: '2026-08-10',
  headline: null, items: [], schedule: [], payments: [],
};

vi.mock('@/lib/queries', async (importOriginal) => ({
  ...(await importOriginal<Record<string, unknown>>()),
  useMyInstallment: () => ({ data: DETAIL, isLoading: false, isError: false, error: null }),
}));

vi.mock('@/lib/api', () => ({
  api: {
    get: vi.fn(async (url: string) => {
      if (url.includes('payment-methods')) {
        return { data: { methods: [{ method: 'cash', label: 'Naqd', online: false }], card_instructions: null } };
      }
      return { data: {} };
    }),
    post: vi.fn(async () => ({ data: {} })),
  },
}));

const PayPage = (await import('@/routes/client/PayPage')).default;
const ReceiptUploadPage = (await import('@/routes/client/ReceiptUploadPage')).default;

// ── the primitive must actually be worth 44 px ──────────────────────────────

const GLOBALS = readFileSync(resolve(__dirname, '../../styles/globals.css'), 'utf8');
const TW_CONFIG = readFileSync(resolve(__dirname, '../../../tailwind.config.js'), 'utf8');

/** `min-h-11` is only worth 44 px while the default spacing scale is in force. */
function spacingScaleIsDefault(): boolean {
  // A `spacing:` key under theme/extend would redefine what `11` means.
  return !/\bspacing\s*:/.test(TW_CONFIG);
}

/** True when `.tap-target` is declared inside a coarse-pointer media query. */
function tapTargetIsCoarseOnly(): boolean {
  const at = GLOBALS.indexOf('.tap-target');
  if (at === -1) throw new Error('.tap-target is no longer defined in globals.css');
  const before = GLOBALS.slice(0, at);
  const lastMedia = before.lastIndexOf('@media');
  if (lastMedia === -1) return false;
  // …and that media block has not been closed before the rule.
  const between = before.slice(lastMedia);
  return /pointer:\s*coarse/.test(between) && !between.includes('\n}');
}

function renderAt(path: string, element: React.ReactNode) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={[path]}>
        <Routes><Route path="/me/installments/:id/*" element={element} /></Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

/** Type steps below 16 px — an amount field must never use one (iOS zooms). */
const SUB_16PX_STEPS = ['text-micro', 'text-caption', 'text-small', 'text-body'];

function assertTappable(el: HTMLElement, label: string) {
  const cls = el.className.split(/\s+/);
  // The unconditional floor. `tap-target` alone would leave this at 20 px on a
  // fine pointer, which is how the defect shipped in the first place.
  expect(cls, `${label} must carry min-h-11 (44 px on every pointer type)`)
    .toContain('min-h-11');
}

// Pin the locale so the control labels below are deterministic.
beforeEach(async () => { vi.clearAllMocks(); await i18n.changeLanguage('uz'); });
afterEach(cleanup);

describe('what makes these controls 44 px', () => {
  it('the Tailwind spacing scale is unmodified, so min-h-11 is 2.75rem = 44px', () => {
    expect(spacingScaleIsDefault(),
      'a spacing override would change what `11` means — re-measure in a browser').toBe(true);
  });

  it('.tap-target is coarse-pointer-only and therefore cannot carry this alone', () => {
    // Documents WHY min-h-11 is required. If this ever becomes false, the
    // helper could carry the controls by itself and this file should be revised
    // deliberately rather than by accident.
    expect(tapTargetIsCoarseOnly()).toBe(true);
  });
});

describe('PayPage amount controls', () => {
  beforeEach(() => { renderAt('/me/installments/i1/pay', <PayPage />); });

  it('the amount input is a 44 px target, numeric, and not sub-16px type', () => {
    const input = document.getElementById('pay-amount') as HTMLInputElement;
    expect(input, 'amount input missing').toBeTruthy();
    assertTappable(input, 'PayPage amount input');
    expect(input.getAttribute('inputmode')).toBe('numeric');
    for (const step of SUB_16PX_STEPS) expect(input.className).not.toContain(step);
    expect(input.tagName).toBe('INPUT');   // never a native select
  });

  it('both quick-pick presets are 44 px targets', () => {
    for (const name of [/Oylik|Ежемесячн/i, /To.?liq|Полностью/i]) {
      const btn = screen.getByRole('button', { name });
      assertTappable(btn, `PayPage preset ${btn.textContent}`);
      expect(btn.querySelectorAll('button, a, input, select, textarea').length,
        'a target must not nest another interactive element').toBe(0);
    }
  });
});

describe('ReceiptUploadPage amount controls', () => {
  beforeEach(() => { renderAt('/me/installments/i1/receipt', <ReceiptUploadPage />); });

  it('the amount input is a 44 px target, numeric, and not sub-16px type', () => {
    const input = document.getElementById('receipt-amount') as HTMLInputElement;
    expect(input, 'amount input missing').toBeTruthy();
    assertTappable(input, 'ReceiptUploadPage amount input');
    expect(input.getAttribute('inputmode')).toBe('numeric');
    for (const step of SUB_16PX_STEPS) expect(input.className).not.toContain(step);
    expect(input.tagName).toBe('INPUT');
  });

  it('the monthly preset is a 44 px target', () => {
    const btn = screen.getByRole('button', { name: /Oylik|Ежемесячн/i });
    assertTappable(btn, 'ReceiptUploadPage monthly preset');
    expect(btn.querySelectorAll('button, a, input, select, textarea').length).toBe(0);
  });
});

describe('neither surface uses a native select', () => {
  it('PayPage', () => {
    renderAt('/me/installments/i1/pay', <PayPage />);
    expect(document.querySelectorAll('select').length).toBe(0);
  });
  it('ReceiptUploadPage', () => {
    renderAt('/me/installments/i1/receipt', <ReceiptUploadPage />);
    expect(document.querySelectorAll('select').length).toBe(0);
  });
});
