/** Form-validation helpers — field-level errors with clear, consistent Uzbek
 *  copy, plus focus+scroll to the first invalid control. The shared <Field>
 *  (components/ui/Modal) marks its control `aria-invalid="true"` when given an
 *  `error`, so `focusFirstInvalid()` can find it without per-form wiring.
 *
 *  Pattern for a form's submit handler:
 *    const errs: Record<string, string> = {};
 *    if (validate.required(name)) errs.name = validate.required(name);
 *    if (!isValidUzPhone(phone))  errs.phone = "Telefon raqam to'liq emas.";
 *    setErrors(errs);
 *    if (hasErrors(errs)) { focusFirstInvalid(); return; }   // don't submit
 *    …mutate…
 *  and render each field as  <Field label error={errors.name}> … </Field>. */

/** Focus + smooth-scroll to the first `aria-invalid` control under `root`
 *  (defaults to the document — safe, since only invalid fields carry the flag).
 *  Waits one frame so <Field> has re-rendered with the flag set. For wrapped
 *  inputs (PhoneInput/EmailInput render a div) it focuses the inner control. */
export function focusFirstInvalid(root?: HTMLElement | null): void {
  const scope: ParentNode = root ?? document;
  // setTimeout(0), not requestAnimationFrame: rAF can fire BEFORE React commits
  // the `aria-invalid` attributes (so the query finds nothing and focus is lost).
  // A macrotask runs after the commit, so the flagged controls exist by then.
  setTimeout(() => {
    const flagged = scope.querySelector<HTMLElement>('[aria-invalid="true"]');
    if (!flagged) return;
    flagged.scrollIntoView({ block: 'center', behavior: 'smooth' });
    const focusable = flagged.matches('input,textarea,select,button,[tabindex]')
      ? flagged
      : flagged.querySelector<HTMLElement>('input,textarea,select,button,[tabindex]') ?? flagged;
    try { focusable.focus({ preventScroll: true }); } catch { focusable.focus?.(); }
  }, 0);
}

/** Standard field validators — each returns an Uzbek error message, or '' when
 *  valid. Keeps validation copy consistent (spec §4/§18); callers may override
 *  the message. */
export const validate = {
  required: (val: unknown, msg = "Bu maydon to'ldirilishi shart"): string =>
    val == null || (typeof val === 'string' && val.trim() === '') ? msg : '',
  minLen: (val: string, n: number, msg?: string): string =>
    (val ?? '').trim().length < n ? (msg ?? `Kamida ${n} ta belgi kiriting`) : '',
  maxLen: (val: string, n: number, msg?: string): string =>
    (val ?? '').length > n ? (msg ?? `${n} ta belgidan oshmasligi kerak`) : '',
  positive: (n: number, msg = '0 dan katta qiymat kiriting'): string =>
    !(n > 0) ? msg : '',
  range: (n: number, min: number, max: number, msg?: string): string =>
    n < min || n > max ? (msg ?? `${min}–${max} oraligʻida boʻlishi kerak`) : '',
};

/** True if an errors object holds any non-empty message. */
export function hasErrors(errs: Record<string, string | undefined | null>): boolean {
  return Object.values(errs).some(Boolean);
}

/** A digits-only text field → a number, or `null` when the field is blank.
 *
 *  The plan editors (create/edit/demo/per-tenant overrides) all use "" to mean
 *  "no cap", which has to reach the API as JSON `null` — not `0`, and not the
 *  `NaN` that `Number('')`-style parsing would produce from stray characters.
 *  Three token-identical copies lived in those modals; this is the one. */
export function numOrNull(s: string): number | null {
  return s.trim() === '' ? null : Number(s.replace(/\D/g, ''));
}
