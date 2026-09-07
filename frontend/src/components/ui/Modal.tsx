import { useEffect, useRef, useId, isValidElement, cloneElement, type ReactNode, type ReactElement } from 'react';
import { X } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import { cn } from '@/lib/cn';
import { useBottomObstruction } from '@/hooks/useBottomObstruction';

interface Props {
  open: boolean;
  title: ReactNode;
  subtitle?: ReactNode;
  onClose: () => void;
  children: ReactNode;
  footer?: ReactNode;
  size?: 'sm' | 'md' | 'lg';
}

const SIZES = {
  sm: 'max-w-panel',
  md: 'max-w-lg',
  lg: 'max-w-lead',
} as const;

const FOCUSABLE =
  'a[href],button:not([disabled]),textarea:not([disabled]),input:not([disabled]),select:not([disabled]),[tabindex]:not([tabindex="-1"])';

// (#522) Stack of currently-open modals. Only the TOPMOST one responds to Esc,
// so a nested picker/sub-modal doesn't also close its parent — one Esc closes
// one layer (native <dialog> behavior). Tokens are pushed on open in mount
// order (parent first, child on top) and removed on close/unmount.
const modalStack: symbol[] = [];

/**
 * Dialog with backdrop + Esc-to-close + body-scroll lock + focus trap.
 * Mobile (`<sm`): a bottom-sheet anchored to the bottom edge (thumb-reachable,
 * keyboard-safe). Desktop: a centered card. The footer is sticky so the primary
 * action stays visible above a long scrolling body.
 */
export function Modal({ open, title, subtitle, onClose, children, footer, size = 'md' }: Props) {
  const { t } = useTranslation();
  const panelRef = useRef<HTMLDivElement>(null);
  // (#a11y) Stable ids so the dialog has a programmatic accessible name (title)
  // and, when present, a description (subtitle). Purely additive.
  const titleId = useId();
  const subtitleId = useId();
  // Keep the latest onClose without making it an effect dependency — otherwise
  // an inline-arrow onClose (the common case) re-runs this effect every render
  // and the focus() call below steals focus from whatever field is being typed.
  const onCloseRef = useRef(onClose);
  onCloseRef.current = onClose;
  // On mobile this dialog is a bottom sheet, so its sticky action footer IS the
  // bottom of the screen — the Back/Next bar a wizard's date picker has to clear.
  // It measures itself: centred on desktop the same footer floats mid-screen and
  // reports covering nothing. Declared before the early return so the hook order
  // is stable across open/closed renders.
  const footerRef = useBottomObstruction<HTMLDivElement>(open && !!footer);

  useEffect(() => {
    if (!open) return;
    const token = Symbol('modal');
    modalStack.push(token);
    const restoreTo = document.activeElement as HTMLElement | null;  // (#161) for focus-return
    const prev = document.body.style.overflow;
    document.body.style.overflow = 'hidden';
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        // (#522) Stacked modals: only the topmost layer closes on Esc.
        if (modalStack[modalStack.length - 1] === token) onCloseRef.current();
        return;
      }
      if (e.key !== 'Tab') return;
      // Focus trap — keep Tab inside the dialog.
      const nodes = panelRef.current?.querySelectorAll<HTMLElement>(FOCUSABLE);
      if (!nodes || nodes.length === 0) return;
      const first = nodes[0]!;
      const last = nodes[nodes.length - 1]!;
      const active = document.activeElement;
      if (e.shiftKey && active === first) { e.preventDefault(); last.focus(); }
      else if (!e.shiftKey && active === last) { e.preventDefault(); first.focus(); }
    };
    window.addEventListener('keydown', onKey);
    // Move focus into the dialog on open.
    const id = window.setTimeout(() => {
      panelRef.current?.querySelector<HTMLElement>(FOCUSABLE)?.focus();
    }, 0);
    return () => {
      const i = modalStack.indexOf(token);
      if (i >= 0) modalStack.splice(i, 1);
      document.body.style.overflow = prev;
      window.removeEventListener('keydown', onKey);
      window.clearTimeout(id);
      restoreTo?.focus?.();  // (#161) return focus to whatever opened the dialog
    };
  }, [open]);

  if (!open) return null;

  return (
    <div className="fixed inset-0 z-[80] flex items-end justify-center sm:items-center sm:px-4 sm:py-8">
      <button
        type="button"
        aria-label={t('common.close', 'Yopish')}
        onClick={onClose}
        className="absolute inset-0 bg-ink/55 backdrop-blur-sm"
      />
      <div
        ref={panelRef}
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        aria-describedby={subtitle ? subtitleId : undefined}
        className={cn(
          'relative flex w-full max-h-[92vh] flex-col overflow-hidden border border-line bg-surface shadow-pop',
          'rounded-t-card sm:rounded-card',          // sheet on mobile, card on desktop
          'sm:max-h-[90vh]',
          SIZES[size],
        )}
      >
        {/* grab handle (mobile sheet affordance) */}
        <div className="mx-auto mt-2 h-1 w-10 shrink-0 rounded-pill bg-line sm:hidden" />
        <div className="flex items-start justify-between gap-4 border-b border-line px-5 py-4 sm:px-6 sm:py-5">
          <div className="min-w-0">
            <h2 id={titleId} className="text-section font-semibold leading-tight">{title}</h2>
            {subtitle && <p id={subtitleId} className="mt-1 text-body text-ink-soft">{subtitle}</p>}
          </div>
          <button
            onClick={onClose}
            className="grid h-11 w-11 tap-target shrink-0 place-items-center rounded-control text-ink-soft hover:bg-ink/5"
            aria-label={t('common.close', 'Yopish')}
          >
            <X size={18} />
          </button>
        </div>
        <div className="flex-1 overflow-y-auto px-5 py-4 sm:px-6 sm:py-5 pb-[max(1rem,env(safe-area-inset-bottom))]">{children}</div>
        {footer && (
          <div
            ref={footerRef}
            data-testid="modal-footer"
            className="flex items-center justify-end gap-2 border-t border-line bg-cream/60 px-5 py-3 sm:px-6 sm:py-4 pb-[max(0.75rem,env(safe-area-inset-bottom))]">
            {footer}
          </div>
        )}
      </div>
    </div>
  );
}

/** Standardised form field — label + control + optional hint, with an inline
 *  error slot rendered directly under the control (close to the offending input). */
export function Field({
  label, required, hint, error, children,
}: { label: string; required?: boolean; hint?: string; error?: string | null; children: ReactNode }) {
  // The wrapping <label> associates the label with its control — but ONLY for
  // labelable elements. Muddato's custom controls (Select, DatePicker,
  // TimePicker, ImageUploadField, NumberChips) render a <button>, which a
  // wrapping label cannot name, so 42 controls across the product had a visible
  // label and no accessible name.
  //
  // The label span gets an id and every Field child is given `aria-labelledby`
  // pointing at it — correct for native inputs too, since the referenced text is
  // the same visible label — so one change here names every wrapped control,
  // including ones added later.
  const descId = useId();
  const labelId = useId();
  // (#561) When in error, additionally mark the control aria-invalid and point
  // aria-errormessage at the message; the message <span> is a role="alert" live
  // region (below) so a validation error that appears after submit is announced.
  type AriaProps = {
    'aria-labelledby'?: string;
    'aria-describedby'?: string;
    'aria-invalid'?: boolean | 'true' | 'false';
    'aria-errormessage'?: string;
  };
  const control = isValidElement(children)
    ? cloneElement(children as ReactElement<AriaProps & { className?: string }>, {
        // Name the control from the visible label. A child that already declares
        // its own name keeps it.
        'aria-labelledby': (children as ReactElement<AriaProps>).props['aria-labelledby']
          ?? ((children as ReactElement<{ 'aria-label'?: string }>).props['aria-label'] ? undefined : labelId),
        'aria-describedby': (error || hint)
          ? [
              (children as ReactElement<AriaProps>).props['aria-describedby'],
              descId,
            ].filter(Boolean).join(' ')
          : (children as ReactElement<AriaProps>).props['aria-describedby'],
        'aria-invalid': error ? 'true' : undefined,
        'aria-errormessage': error ? descId : undefined,
        // (#284) Auto-apply the shared error border so callers needn't hand-wire
        // inputErrCls. Merge with (not replace) the child's existing className;
        // the FormFields inputs append it via cn(inputCls, …, className) so the
        // danger border wins. Untouched when there's no error.
        className: error
          ? cn((children as ReactElement<{ className?: string }>).props.className, inputErrCls)
          : (children as ReactElement<{ className?: string }>).props.className,
      })
    : children;
  return (
    <label className="flex flex-col gap-1.5 text-body font-medium">
      <span id={labelId}>{label} {required && <span className="text-accent">*</span>}</span>
      {control}
      {error
        ? <span id={descId} role="alert" className="text-micro font-medium text-danger">{error}</span>
        : hint && <span id={descId} className="text-micro font-normal text-muted">{hint}</span>}
    </label>
  );
}

/**
 * A labelled group of fields inside a modal — small-caps kicker above a
 * bordered card.
 *
 * Lived twice, in `CreatePlanModal` and `EditPlanModal`, with a single
 * character of drift: `gap-4` in create, `gap-3` in edit. The create and edit
 * dialogs for the same entity must not space their fields differently, so the
 * roomier `gap-4` is now the one spacing for both.
 */
export function FormSection({ title, hint, children }: { title: string; hint?: string; children: ReactNode }) {
  return (
    <fieldset className="grid gap-4 rounded-card border border-line p-4">
      <legend className="px-1 text-micro font-medium uppercase tracking-kicker text-muted">{title}</legend>
      {hint && <p className="-mt-1 text-caption text-muted">{hint}</p>}
      {children}
    </fieldset>
  );
}

export const inputCls =
  'h-11 w-full rounded-control border border-line bg-cream px-3 text-subsection placeholder:text-muted focus:border-ink/30 focus:outline-none focus:ring-2 focus:ring-ink/20 focus:bg-white';

/** Apply to an input when its field is in an error state. */
export const inputErrCls = 'border-danger/60 focus:border-danger focus:ring-danger/20';
