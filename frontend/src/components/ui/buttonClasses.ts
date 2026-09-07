import { cn } from '@/lib/cn';

export type Variant = 'primary' | 'ghost' | 'soft' | 'inverse' | 'outline' | 'danger' | 'telegram';
export type Size = 'sm' | 'md' | 'lg';

const variants: Record<Variant, string> = {
  primary: 'bg-[color:var(--c-accent-fill)] text-accent-ink hover:brightness-105 active:brightness-95',
  inverse: 'bg-ink text-white hover:bg-ink/90',
  ghost:   'bg-transparent text-ink hover:bg-ink/5',
  soft:    'bg-line text-ink hover:bg-ink/10',
  outline: 'border border-line text-ink hover:bg-ink/5',
  danger:  'bg-danger text-white hover:brightness-105',
  /** Telegram's own brand blue — for "continue/open via Telegram" CTAs only,
   *  per Telegram's branding (never substitute the platform accent color here). */
  telegram: 'bg-[color:var(--c-brand-telegram)] text-white hover:brightness-110',
};

const sizes: Record<Size, string> = {
  // Heights are the design system's: 36 / 40 / 48px on a fine pointer, and they
  // are NOT changed here — 40px is the md control and desktop density depends
  // on it.
  //
  // What changed (Batch 6) is the coarse-pointer floor. `.tap-target` sets a
  // 44px minimum inside `@media (pointer: coarse)` only, so a phone gets a
  // finger-sized hit area while a mouse keeps the compact toolbar. It used to be
  // added per call site, which meant every primary action that nobody remembered
  // to adapt shipped at 40px on a phone — under WCAG 2.5.5 — and the default was
  // the wrong one. Putting it in the primitive inverts that: a control is
  // compliant unless somebody opts out, rather than compliant only if somebody
  // opts in.
  //
  // `lg` is already 48px on every pointer, so a 44px floor would be a no-op and
  // is deliberately not applied — this is a MINIMUM, not a uniform size, and
  // controls whose design height is intentionally larger keep it.
  sm: 'h-9  px-3 text-small rounded-icon tap-target',
  md: 'h-10 px-4 text-body    rounded-control tap-target',
  lg: 'h-12 px-5 text-subsection  rounded-control',
};

/**
 * The shared button look, as classes.
 *
 * `Button` renders a `<button>`. A control that NAVIGATES must render an `<a>`
 * or a router `<Link>` — wrapping a `<Button>` in one nests a button inside a
 * link, which is invalid markup and exposes two conflicting controls for one
 * action. Style the link with this instead.
 *
 * Kept out of `Button.tsx` so that file exports only components, which is what
 * Fast Refresh requires.
 */
export function buttonClasses({ variant = 'primary', size = 'md', block, className }: {
  variant?: Variant; size?: Size; block?: boolean; className?: string;
} = {}): string {
  return cn(
    'inline-flex items-center justify-center gap-2 font-medium tracking-tight transition select-none',
    'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ink/20 focus-visible:ring-offset-1 focus-visible:ring-offset-cream',
    variants[variant],
    sizes[size],
    block && 'w-full',
    className,
  );
}
