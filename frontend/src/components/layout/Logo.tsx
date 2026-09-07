import { cn } from '@/lib/cn';

/** Inline SVG of the muddato wordmark + spinning ring glyph. */
export function Logo({ size = 28, className }: { size?: number; className?: string }) {
  return (
    <span className={cn('inline-flex items-center gap-2 text-ink', className)}>
      <svg width={size} height={size} viewBox="0 0 32 32" aria-hidden>
        <circle cx="16" cy="16" r="11" fill="none" stroke="currentColor" strokeWidth="3" opacity="0.18" />
        <path d="M 16 5 A 11 11 0 1 1 6.74 21.6" fill="none" stroke="var(--c-accent)" strokeWidth="3" strokeLinecap="round" />
        <circle cx="6.74" cy="21.6" r="2.6" fill="var(--c-accent)" />
      </svg>
      <span className="text-section font-semibold tracking-tight">muddato</span>
    </span>
  );
}
