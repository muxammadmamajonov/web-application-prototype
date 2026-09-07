// @vitest-environment jsdom
/**
 * FB-007 — Enter never advances a multi-step form.
 *
 * THE DEFECT. The contract wizard was `<form onSubmit={… goNext()}>`, so Enter
 * anywhere inside a step walked the shop forward: in the customer search box
 * (before a customer was chosen), in a price field, in a date field. Enter is a
 * keyboard reflex; a wizard step is a decision.
 *
 * ONE policy, one boundary — `StepForm`. These tests exercise the boundary with
 * the field types the contract wizard actually contains (customer search,
 * product name, numeric price, date, notes textarea) rather than asserting a
 * handler exists, so a screen that adds a new field type is covered by default.
 */
import { describe, expect, it, afterEach, vi } from 'vitest';
import { render, screen, cleanup, fireEvent } from '@testing-library/react';
import { StepForm, blockImplicitSubmit } from '@/components/ui/StepForm';

afterEach(cleanup);

/** Enter on `el`, reporting whether anything let the default through. */
function pressEnter(el: Element, init: Record<string, unknown> = {}): boolean {
  return !fireEvent.keyDown(el, { key: 'Enter', ...init });
}

function Wizard({ onSubmit, onNext }: { onSubmit?: () => void; onNext?: () => void }) {
  return (
    <div>
      <StepForm>
        <input aria-label="customer" defaultValue="Ali" />
        <input aria-label="product" defaultValue="Muzlatgich" />
        <input aria-label="price" inputMode="numeric" defaultValue="1 000 000" />
        <input aria-label="date" type="date" defaultValue="2026-08-10" />
        <textarea aria-label="notes" defaultValue="izoh" />
        <button type="button" onClick={onSubmit}>inside</button>
      </StepForm>
      {/* The real wizard's Next/Create live outside the form for exactly this
          reason: they cannot be implicit-submit targets. */}
      <button type="button" onClick={onNext}>Keyingisi</button>
    </div>
  );
}

describe('an intermediate step never advances on Enter', () => {
  it.each(['customer', 'product', 'price', 'date'])(
    'Enter in the %s field does nothing', (field) => {
      const onNext = vi.fn();
      render(<Wizard onNext={onNext} />);
      expect(pressEnter(screen.getByLabelText(field))).toBe(true);   // default prevented
      expect(onNext).not.toHaveBeenCalled();
    });

  it('the form itself refuses to submit, whatever triggered it', () => {
    const onNext = vi.fn();
    const { container } = render(<Wizard onNext={onNext} />);
    const form = container.querySelector('form')!;
    // `fireEvent.submit` returns false when a handler called preventDefault.
    expect(fireEvent.submit(form)).toBe(false);
    expect(onNext).not.toHaveBeenCalled();
  });

  it('repeated Enter cannot double-anything — there is nothing to fire', () => {
    const onNext = vi.fn();
    render(<Wizard onNext={onNext} />);
    const price = screen.getByLabelText('price');
    pressEnter(price); pressEnter(price); pressEnter(price);
    expect(onNext).not.toHaveBeenCalled();
  });

  it('the final commit needs its own explicit control, outside the form', () => {
    const onNext = vi.fn();
    render(<Wizard onNext={onNext} />);
    fireEvent.click(screen.getByText('Keyingisi'));
    expect(onNext).toHaveBeenCalledTimes(1);
  });
});

describe('Enter still means what it means everywhere else', () => {
  it('a textarea gets its newline', () => {
    render(<Wizard />);
    expect(pressEnter(screen.getByLabelText('notes'))).toBe(false);  // not prevented
  });

  it('a focused button activates natively', () => {
    const onSubmit = vi.fn();
    render(<Wizard onSubmit={onSubmit} />);
    expect(pressEnter(screen.getByText('inside'))).toBe(false);
  });

  it('an IME composition commits without the form seeing an Enter', () => {
    render(<Wizard />);
    const el = screen.getByLabelText('customer');
    // Both signals: modern browsers set `isComposing`; the older WebViews
    // Telegram embeds only ever report keyCode 229.
    expect(pressEnter(el, { isComposing: true })).toBe(false);
    expect(pressEnter(el, { keyCode: 229 })).toBe(false);
  });

  it('a combobox subtree keeps its own Enter', () => {
    render(
      <StepForm>
        <div data-enter-owner>
          <input aria-label="picker" role="combobox" />
        </div>
      </StepForm>,
    );
    expect(pressEnter(screen.getByLabelText('picker'))).toBe(false);
  });

  it('keys other than Enter are untouched', () => {
    render(<Wizard />);
    const el = screen.getByLabelText('price');
    expect(!fireEvent.keyDown(el, { key: 'a' })).toBe(false);
    expect(!fireEvent.keyDown(el, { key: 'Tab' })).toBe(false);
  });
});

describe('the policy is reusable without the component', () => {
  it('blockImplicitSubmit is the same rule, callable on any form', () => {
    const prevented: string[] = [];
    const ev = (target: Partial<HTMLElement>, key = 'Enter') => ({
      key,
      nativeEvent: {},
      target,
      preventDefault: () => prevented.push(key),
    });
    // A plain input is blocked…
    blockImplicitSubmit(ev({ tagName: 'INPUT', getAttribute: () => null, closest: () => null } as never) as never);
    expect(prevented).toEqual(['Enter']);
    // …a textarea is not.
    blockImplicitSubmit(ev({ tagName: 'TEXTAREA', getAttribute: () => null, closest: () => null } as never) as never);
    expect(prevented).toEqual(['Enter']);
  });
});
