import { Boxes } from 'lucide-react';
import { cn } from '@/lib/cn';

/**
 * The ONE category-icon tile used everywhere a catalog category is listed
 * (catalog list, product picker, …) so they're identical instead of each page
 * styling its own. Brand standard: a solid accent (red) tile with a WHITE icon.
 *
 *  • An uploaded `iconUrl` (SVG/PNG) is recoloured pure white via the
 *    `brightness(0) invert(1)` filter — any opaque glyph becomes white strokes
 *    on the red tile, regardless of the source colour.
 *  • Categories with no uploaded icon fall back to a white Lucide glyph (emoji
 *    can't be recoloured to white, so we don't render it here).
 */
export function CategoryIcon({
  iconUrl,
  size = 'md',
  className,
}: {
  iconUrl?: string | null;
  size?: 'sm' | 'md';
  className?: string;
}) {
  const tile = size === 'sm' ? 'h-8 w-8' : 'h-10 w-10';
  const glyph = size === 'sm' ? 16 : 18;
  return (
    <span className={cn('grid shrink-0 place-items-center rounded-xl bg-accent', tile, className)}>
      {iconUrl ? (
        <img
          src={iconUrl}
          alt=""
          loading="lazy"
          className={size === 'sm' ? 'h-4 w-4 object-contain' : 'h-5 w-5 object-contain'}
          style={{ filter: 'brightness(0) invert(1)' }}
        />
      ) : (
        <Boxes size={glyph} className="text-white" />
      )}
    </span>
  );
}
