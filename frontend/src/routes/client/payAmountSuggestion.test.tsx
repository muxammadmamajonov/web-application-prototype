// @vitest-environment jsdom
/**
 * FB-017 — the suggested instalment is an opening offer, not a floor.
 *
 * REPRODUCED ON A REAL DEVICE (founder beta, 2026-08-09). The pay screen seeded
 * the amount from `monthly_amount` with
 *
 *     useEffect(() => { if (inst.data && !amount) setAmount(monthly) }, [inst.data, amount])
 *
 * — `amount` in the dependency list. So the moment somebody deleted the last
 * digit the field went empty, the effect re-fired, and the suggestion reappeared
 * under the cursor. The field could not be cleared; a customer typing a smaller
 * partial payment ended up appending to a number they thought they had erased,
 * and paid a figure they never chose.
 *
 * The second half of this file is the CONTRAST defect on the same card: the
 * `SUMMA` kicker sat at 70% white on the brand orange — 2.51:1, roughly half of
 * what WCAG AA asks of small text. The ratios below are computed from the real
 * OKLCH tokens in globals.css, so a token change re-runs the maths rather than
 * quietly invalidating a number somebody wrote down once.
 */
import { describe, expect, it, beforeEach, afterEach, vi } from 'vitest';
import { cleanup, render, screen, fireEvent } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import i18n from '@/i18n';

const DETAIL = {
  id: 'i1',
  tenant: { id: 't1', name: 'Muddato Shop', logo: null, slug: 'shop' },
  contract_number: 'C-001', reference: null, status: 'active' as const,
  currency: 'UZS',
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
    get: vi.fn(async (url: string) => (url.includes('payment-methods')
      ? { data: { methods: [{ method: 'cash', label: 'Naqd', online: false }], card_instructions: null } }
      : { data: {} })),
    post: vi.fn(async () => ({ data: {} })),
  },
}));

const PayPage = (await import('@/routes/client/PayPage')).default;

function renderPay() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={['/me/installments/i1/pay']}>
        <Routes><Route path="/me/installments/:id/*" element={<PayPage />} /></Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

const amountField = () => screen.getByLabelText('Summa') as HTMLInputElement;
const typeInto = (el: HTMLInputElement, value: string) =>
  fireEvent.change(el, { target: { value, selectionStart: value.length, selectionEnd: value.length } });

beforeEach(async () => { vi.clearAllMocks(); await i18n.changeLanguage('uz'); });
afterEach(cleanup);

describe('the suggestion', () => {
  it('appears when the screen opens', () => {
    renderPay();
    expect(amountField().value).toBe('700 000');
  });

  it('can be deleted to empty and STAYS empty', () => {
    renderPay();
    typeInto(amountField(), '');
    expect(amountField().value).toBe('');
    // The defect: the effect re-fired here and put 700 000 straight back.
    expect(amountField().value).not.toBe('700 000');
  });

  it('deleting one digit at a time reaches empty without bouncing back', () => {
    renderPay();
    const el = amountField();
    for (const step of ['70 000', '7 000', '700', '70', '7', '']) {
      typeInto(el, step);
      expect(el.value).toBe(step);
    }
  });

  it('the next digit after clearing replaces the zero, not appends to it', () => {
    renderPay();
    const el = amountField();
    typeInto(el, '');
    typeInto(el, '5');
    expect(el.value).toBe('5');
  });

  it('a smaller partial payment survives being typed', () => {
    renderPay();
    const el = amountField();
    typeInto(el, '');
    typeInto(el, '50000');
    expect(el.value).toBe('50 000');
  });

  it('only the explicit quick-pick brings the suggestion back', () => {
    renderPay();
    const el = amountField();
    typeInto(el, '');
    fireEvent.click(screen.getByText('Oylik'));
    expect(el.value).toBe('700 000');
  });

  it('a formatted paste is accepted, and cannot exceed the remaining balance', () => {
    renderPay();
    const el = amountField();
    typeInto(el, '1 000 000');
    expect(el.value).toBe('1 000 000');
    typeInto(el, '99999999999');
    expect(el.value).toBe('7 000 000');    // clamped to `remaining`
  });
});

describe('the pay CTA never presents an unpayable amount as payable', () => {
  const cta = () => screen.getByRole('button', { name: /to'lash|to‘lash/i }) as HTMLButtonElement;

  /** The method list arrives from the API, so it has to be waited for. */
  const chooseCash = async () => fireEvent.click(await screen.findByText('Naqd'));

  it('is disabled while the amount is empty', async () => {
    renderPay();
    await chooseCash();
    typeInto(amountField(), '');
    expect(cta().disabled).toBe(true);
  });

  it('is disabled at zero', async () => {
    renderPay();
    await chooseCash();
    typeInto(amountField(), '0');
    expect(cta().disabled).toBe(true);
  });

  it('enables once a real amount and a method are chosen', async () => {
    renderPay();
    await chooseCash();
    typeInto(amountField(), '50000');
    expect(cta().disabled).toBe(false);
  });
});

// ── contrast ────────────────────────────────────────────────────────────────

const GLOBALS = readFileSync(resolve(__dirname, '../../styles/globals.css'), 'utf8');

/** Read an `oklch(L C H)` custom property out of globals.css. */
function token(name: string): [number, number, number] {
  const m = new RegExp(`${name}:\\s*oklch\\(([\\d.]+)\\s+([\\d.]+)\\s+([\\d.]+)\\)`).exec(GLOBALS);
  if (!m) throw new Error(`${name} is no longer an oklch() token in globals.css`);
  return [Number(m[1]), Number(m[2]), Number(m[3])];
}

function oklchToSrgb([L, C, H]: [number, number, number]): [number, number, number] {
  const h = (H * Math.PI) / 180;
  const a = C * Math.cos(h), b = C * Math.sin(h);
  const l = (L + 0.3963377774 * a + 0.2158037573 * b) ** 3;
  const m = (L - 0.1055613458 * a - 0.0638541728 * b) ** 3;
  const s = (L - 0.0894841775 * a - 1.2914855480 * b) ** 3;
  const enc = (x: number) => {
    const c = Math.min(1, Math.max(0, x));
    return c > 0.0031308 ? 1.055 * c ** (1 / 2.4) - 0.055 : 12.92 * c;
  };
  return [
    enc(4.0767416621 * l - 3.3077115913 * m + 0.2309699292 * s),
    enc(-1.2684380046 * l + 2.6097574011 * m - 0.3413193965 * s),
    enc(-0.0041960863 * l - 0.7034186147 * m + 1.7076147010 * s),
  ];
}

const lum = (rgb: number[]) => {
  const [r, g, b] = rgb.map((c) => (c <= 0.04045 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4));
  return 0.2126 * r + 0.7152 * g + 0.0722 * b;
};
const contrast = (fg: number[], bg: number[]) => {
  const [a, b] = [lum(fg), lum(bg)];
  return (Math.max(a, b) + 0.05) / (Math.min(a, b) + 0.05);
};
const over = (fg: number[], bg: number[], alpha: number) => fg.map((c, i) => c * alpha + bg[i] * (1 - alpha));

const WHITE = [1, 1, 1];

describe('the SUMMA card meets WCAG AA', () => {
  it('records why the brand orange could not carry a small label', () => {
    const accent = oklchToSrgb(token('--c-accent'));
    // The shipped defect: `text-white/70` on `bg-accent`.
    expect(contrast(over(WHITE, accent, 0.7), accent)).toBeLessThan(4.5);
    // Even full-strength white on the brand orange misses AA for small text.
    expect(contrast(WHITE, accent)).toBeLessThan(4.5);
  });

  it('white on --c-accent-fill passes AA for normal text', () => {
    const fill = oklchToSrgb(token('--c-accent-fill'));
    expect(contrast(WHITE, fill)).toBeGreaterThanOrEqual(4.5);
  });

  it('the amount card actually uses that token, at full-strength foreground', () => {
    renderPay();
    const label = screen.getByText('Summa');
    const card = label.closest('div')!;
    expect(card.className).toMatch(/var\(--c-accent-fill\)/);
    expect(card.className).not.toMatch(/\bbg-accent\b/);
    // The on-accent kicker variant, never plain `.kicker` (which bakes in the
    // page-background grey) and never a translucent white — the alpha is what
    // dropped the label to 2.51:1.
    expect(label.className).toMatch(/\bkicker-on-accent\b/);
    expect(label.className).not.toMatch(/\bkicker\b(?!-on-accent)/);
    expect(label.className).not.toMatch(/text-white\/\d+/);
  });

  it('no translucent-white foreground survives anywhere on the card', () => {
    renderPay();
    const card = screen.getByText('Summa').closest('div')!;
    const classes = [card, ...Array.from(card.querySelectorAll('*'))]
      .map((el) => (el as HTMLElement).className || '').join(' ');
    expect(classes).not.toMatch(/text-white\/\d+/);
  });
});
