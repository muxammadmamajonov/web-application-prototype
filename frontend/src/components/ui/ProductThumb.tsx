import { useState } from 'react';
import { cn } from '@/lib/cn';
import { categoryEmoji } from '@/lib/catalogSchema';

/** Product thumbnail: shows the first photo if present, otherwise a category
 *  emoji (📱 for phones, 🚗 for cars, …) so a card is never blank. */
export function ProductThumb({
  photo, category, brand, size = 56, rounded = 'xl', className,
}: {
  photo?: string | null;
  category?: string | null;
  brand?: string | null;
  size?: number;
  rounded?: 'xl' | 'lg' | 'full';
  className?: string;
}) {
  const [broken, setBroken] = useState(false);
  const radius = rounded === 'full' ? 'rounded-full' : rounded === 'lg' ? 'rounded-lg' : 'rounded-xl';
  const show = photo && !broken;
  return (
    <span
      className={cn('grid shrink-0 place-items-center overflow-hidden border border-line bg-cream', radius, className)}
      style={{ width: size, height: size }}
    >
      {show ? (
        <img src={photo!} alt="" loading="lazy" className="h-full w-full object-cover" onError={() => setBroken(true)} />
      ) : (
        <span style={{ fontSize: Math.round(size * 0.5) }} aria-hidden>{categoryEmoji(category, brand)}</span>
      )}
    </span>
  );
}
