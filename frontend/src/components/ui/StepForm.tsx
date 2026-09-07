/* eslint-disable react-refresh/only-export-components -- `blockImplicitSubmit`
   is the same policy as `StepForm`, exported for forms that cannot use the
   component. Splitting them would let the two drift. Dev-time Fast Refresh only. */
import type { FormEvent, KeyboardEvent, ReactNode } from 'react';

/**
 * The Enter policy for every multi-step form, in one place.
 *
 * THE RULE: an intermediate step never advances on Enter, and a form never
 * submits implicitly. Enter is a keyboard reflex; a wizard step is a decision.
 * Moving on and the final irreversible write are reachable only through their
 * own controls, which live outside this element so they cannot be
 * implicit-submit targets.
 *
 * Enter still means what it means elsewhere: a textarea gets its newline; a
 * focused button, link or `<summary>` activates natively; a subtree marked
 * `data-enter-owner` (a combobox) keeps its own Enter; an IME composition
 * commits without anything else happening.
 *
 * Two guards, because either alone has a gap — `onSubmit` can be reached by a
 * stray `type="submit"` descendant, and `onKeyDown` cannot see a submit
 * triggered by other means.
 */

/** True while an IME is mid-composition — the Enter that commits it is not an
 *  Enter the form should see. `keyCode === 229` covers older WebViews that
 *  never set `isComposing`, which is exactly the browser Telegram embeds. */
function isComposing(e: KeyboardEvent): boolean {
  const native = e.nativeEvent as unknown as { isComposing?: boolean; keyCode?: number };
  return !!native.isComposing || native.keyCode === 229;
}

/** Whether this element is allowed to keep its own Enter behaviour. */
function ownsEnter(el: HTMLElement | null): boolean {
  if (!el) return false;
  if (el.isContentEditable) return true;
  const tag = el.tagName;
  if (tag === 'TEXTAREA' || tag === 'BUTTON' || tag === 'A' || tag === 'SUMMARY') return true;
  if (el.getAttribute('role') === 'button') return true;
  // A combobox/listbox marks its subtree so its own handler decides.
  return !!el.closest('[data-enter-owner]');
}

/** The keydown half of the policy. Exported so a form that cannot use
 *  `StepForm` (a different element, a third-party wrapper) still gets it. */
export function blockImplicitSubmit(e: KeyboardEvent): void {
  if (e.key !== 'Enter') return;
  // Let the IME finish. Nothing can submit anyway — `onSubmit` no-ops — so this
  // does not need to preventDefault, and doing so breaks commit in some IMEs.
  if (isComposing(e)) return;
  if (ownsEnter(e.target as HTMLElement | null)) return;
  e.preventDefault();
}

/**
 * A step container that cannot be submitted by keyboard.
 *
 * It is still a real `<form>`: browsers give grouped fields, autofill and
 * screen-reader semantics from the element, and dropping it to a `<div>` to
 * dodge implicit submit would lose all three.
 */
export function StepForm({ children, className, id }: {
  children: ReactNode; className?: string; id?: string;
}) {
  return (
    <form
      id={id}
      className={className}
      noValidate
      // Belt: any submit — implicit, or a stray `type="submit"` descendant —
      // is swallowed. There is deliberately no onSubmit prop to pass in.
      onSubmit={(e: FormEvent) => e.preventDefault()}
      // Braces: Enter never even reaches the submit machinery from a plain field.
      onKeyDown={blockImplicitSubmit}
    >
      {children}
    </form>
  );
}
