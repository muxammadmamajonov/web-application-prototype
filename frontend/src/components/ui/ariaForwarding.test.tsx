// @vitest-environment jsdom
/**
 * H20 — six typed inputs in FormFields.tsx accepted no `aria-*` props and
 * spread nothing onto their `<input>`. `Field` (Modal.tsx) injects
 * `aria-invalid`/`aria-errormessage`/`aria-labelledby`/`aria-describedby` via
 * `cloneElement` when a form field is in error, but a component that doesn't
 * destructure and forward those props drops them on the floor: the red border
 * still renders (that's driven by `className`, which WAS forwarded), so the
 * defect is invisible by eye. A screen-reader user gets no indication the
 * field is invalid or what the error says. Real callers hit this path:
 * `CustomerModal`, `ProductModal`, `InstallmentModal`, `TopUpModal`, `PayPage`.
 *
 * These tests render each input inside the real `Field` wrapper with an error,
 * exactly like a form does, and assert the injected attributes land on the
 * actual `<input>` in the DOM.
 */
import { describe, expect, it, afterEach } from 'vitest';
import { render, cleanup } from '@testing-library/react';
import { Field } from '@/components/ui/Modal';
import {
  PhoneInput, EmailInput, MoneyInput, PassportInput, TelegramInput, PercentInput,
} from '@/components/ui/FormFields';

afterEach(cleanup);

const CASES: Array<[string, React.ReactElement]> = [
  ['PhoneInput', <PhoneInput value="+998 90 123 45 67" onChange={() => {}} />],
  ['EmailInput', <EmailInput value="a@b.com" onChange={() => {}} />],
  ['MoneyInput', <MoneyInput value={1000} onChange={() => {}} />],
  ['PassportInput', <PassportInput value="AA1234567" onChange={() => {}} />],
  ['TelegramInput', <TelegramInput value="user" onChange={() => {}} />],
  ['PercentInput', <PercentInput value={10} onChange={() => {}} />],
];

describe.each(CASES)('%s forwards Field-injected aria props (H20)', (name, element) => {
  it('carries aria-invalid="true" and aria-errormessage in the DOM when Field reports an error', () => {
    const { container } = render(
      <Field label="Test" error="Xato xabari">{element}</Field>,
    );
    const input = container.querySelector('input') as HTMLInputElement;
    expect(input, `${name} did not render an <input>`).toBeTruthy();
    expect(input.getAttribute('aria-invalid'), `${name} dropped aria-invalid`).toBe('true');
    const errId = input.getAttribute('aria-errormessage');
    expect(errId, `${name} dropped aria-errormessage`).toBeTruthy();
    // The id actually resolves to the visible error text — not a dangling ref.
    const errEl = errId ? container.querySelector(`[id="${errId}"]`) : null;
    expect(errEl?.textContent).toBe('Xato xabari');
  });

  it('carries aria-describedby pointing at the error when in error', () => {
    const { container } = render(
      <Field label="Test" error="Xato xabari">{element}</Field>,
    );
    const input = container.querySelector('input') as HTMLInputElement;
    expect(input.getAttribute('aria-describedby'), `${name} dropped aria-describedby`).toBeTruthy();
  });

  it('has no aria-invalid when there is no error', () => {
    const { container } = render(<Field label="Test">{element}</Field>);
    const input = container.querySelector('input') as HTMLInputElement;
    expect(input.getAttribute('aria-invalid')).toBeNull();
  });
});
