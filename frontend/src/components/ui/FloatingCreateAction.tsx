import { Plus, type LucideIcon } from 'lucide-react';
import { cn } from '@/lib/cn';

interface Props {
  onClick: () => void;
  /** What gets created. Becomes the accessible name AND the hover tooltip. */
  label: string;
  icon?: LucideIcon;
  /**
   * Unavailable but still visible — a plan cap reached, a card list still
   * empty. Prefer this to hiding: a control that vanishes leaves the user
   * hunting, while a dimmed one plus {@link title} says why. Hide it only when
   * the user's ROLE means it will never apply to them.
   */
  disabled?: boolean;
  /** Why it is unavailable. Set whenever `disabled` is set. */
  title?: string;
  className?: string;
}

/**
 * The ONE create action across the platform: a floating round `+`.
 *
 * Replaces the header pill (`PrimaryAction`) as the standard for every
 * "Add / Create / New" action, so the control lives in the same place on every
 * page and the user never hunts for it.
 *
 * POSITIONING is the part that is easy to get wrong, so it is solved once here:
 *
 *   - `bottom-24` on phones clears `MobileBottomNav`, which is `fixed bottom-0`
 *     and about 64px tall plus its safe-area padding. `md:bottom-8` drops back
 *     to a normal margin because that bar is `md:hidden` in `AppShell`.
 *   - `--tg-safe-bottom` is added on top, so inside Telegram the button clears
 *     the home indicator rather than sitting under it.
 *   - `z-40` is deliberately BELOW the bottom bar's `z-50`: if the two ever do
 *     overlap, the navigation must win. It is above page content but under
 *     modals and drawers, so opening the form it triggers covers it.
 *
 * ACCESSIBILITY: the glyph alone says nothing, so `label` is required and is
 * used as both `aria-label` and `title`. That is the one thing a bare `+` costs
 * over a labelled pill, and it is not optional.
 */
export function FloatingCreateAction({
  onClick, label, icon: Icon = Plus, disabled, title, className,
}: Props) {
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={disabled}
      aria-label={label}
      title={title ?? label}
      className={cn(
        'fixed right-4 z-40 grid h-14 w-14 place-items-center rounded-pill md:right-8',
        // Bottom offset as CLASSES, not an inline style: an inline `bottom`
        // wins at every width, so the desktop step below would never apply.
        'bottom-[calc(6rem+var(--tg-safe-bottom,0px))]',
        'md:bottom-[calc(2rem+var(--tg-safe-bottom,0px))]',
        'bg-[color:var(--c-accent-fill)] text-white shadow-pop',
        'transition hover:brightness-105 active:scale-95',
        'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ink focus-visible:ring-offset-2',
        'disabled:cursor-not-allowed disabled:opacity-50 disabled:hover:brightness-100 disabled:active:scale-100',
        className,
      )}
    >
      <Icon size={24} />
    </button>
  );
}
