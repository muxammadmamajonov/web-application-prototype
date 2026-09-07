import { cn } from '@/lib/cn';
import { initials } from '@/lib/format';

type Size = 'xs' | 'sm' | 'md' | 'lg';

const sizes: Record<Size, string> = {
  xs: 'h-6  w-6  text-micro',
  sm: 'h-8  w-8  text-caption',
  md: 'h-10 w-10 text-body',
  lg: 'h-14 w-14 text-subsection',
};

// (#627) Tokenized swatches — see --c-avatar-* in globals.css.
const tones = [
  'bg-[color:var(--c-avatar-1)] text-white',
  'bg-[color:var(--c-avatar-2)] text-white',
  'bg-[color:var(--c-avatar-3)] text-white',
  'bg-[color:var(--c-avatar-4)] text-white',
  'bg-[color:var(--c-avatar-5)] text-white',
  'bg-[color:var(--c-avatar-6)] text-white',
];

/** Stable colour from the name — same name always gets the same swatch. */
function hueFromName(name: string): string {
  let h = 0;
  for (const ch of name) h = (h * 31 + ch.charCodeAt(0)) >>> 0;
  return tones[h % tones.length]!;
}

export function Avatar({
  name,
  photoUrl,
  size = 'md',
  className,
}: {
  name: string | null | undefined;
  photoUrl?: string | null;
  size?: Size;
  className?: string;
}) {
  const label = initials(name);
  if (photoUrl) {
    return (
      <img
        src={photoUrl}
        alt={name ?? ''}
        className={cn('inline-block rounded-control object-cover', sizes[size], className)}
        loading="lazy"
      />
    );
  }
  return (
    <span
      className={cn(
        'inline-flex items-center justify-center rounded-control font-semibold tracking-tight',
        hueFromName(name ?? '?'),
        sizes[size],
        className,
      )}
      aria-label={name ?? ''}
    >
      {label}
    </span>
  );
}
