import { cn } from '@/lib/cn';

interface Props {
  checked: boolean;
  onChange: (v: boolean) => void;
  disabled?: boolean;
  /** Accessible label (falls back to aria only). */
  label?: string;
  className?: string;
  /** Compact size for dense rows. */
  size?: 'sm' | 'md';
}

/**
 * The track + knob visual only — no interactivity of its own. Use this when a
 * switch-look indicator must live INSIDE another interactive element (e.g. a
 * whole clickable settings tile that is itself `role="switch"`), where a real
 * nested `<Switch>` would be an invalid button-in-button. Everywhere else, use
 * `<Switch>` directly.
 */
export function SwitchTrack({ checked, size = 'md', className }: { checked: boolean; size?: 'sm' | 'md'; className?: string }) {
  const track = size === 'sm' ? 'h-5 w-9' : 'h-6 w-11';
  const knob = size === 'sm' ? 'h-4 w-4' : 'h-5 w-5';
  const on = size === 'sm' ? 'translate-x-[18px]' : 'translate-x-[22px]';
  return (
    <span className={cn('relative inline-flex shrink-0 items-center rounded-pill transition', track, checked ? 'bg-accent' : 'bg-line', className)}>
      <span className={cn('inline-block transform rounded-pill bg-white shadow transition',
                          knob, checked ? on : 'translate-x-0.5')} />
    </span>
  );
}

/**
 * On/off toggle switch — the premium replacement for setting checkboxes.
 * It's a real <button role="switch"> so it's keyboard- and SR-accessible.
 * Use for boolean *settings* (enable/disable). Keep plain checkboxes for
 * consent and multi-select.
 */
export function Switch({ checked, onChange, disabled, label, className, size = 'md' }: Props) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={checked}
      aria-label={label}
      disabled={disabled}
      onClick={() => onChange(!checked)}
      className={cn('inline-flex shrink-0 border-0 bg-transparent p-0 disabled:opacity-50', className)}
    >
      <SwitchTrack checked={checked} size={size} />
    </button>
  );
}
