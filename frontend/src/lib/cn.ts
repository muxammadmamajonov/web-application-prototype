import clsx, { type ClassValue } from 'clsx';
import { extendTailwindMerge } from 'tailwind-merge';

/**
 * The product's type-scale steps, as they appear after `text-`.
 *
 * Mirrors `theme.extend.fontSize` in `tailwind.config.js`, which is the single
 * authoritative source for every font size in the product. `cn.test.ts` fails
 * if the two drift, so a step added there must be added here as well.
 *
 * Why tailwind-merge needs telling: its `font-size` group recognises only
 * Tailwind's own stock steps — the `base` keyword, t-shirt sizes and arbitrary
 * lengths. Our steps are named for their ROLE instead, so none of them match,
 * and every unmatched class under the `text-` prefix falls through to
 * `text-color`, whose validator accepts anything. Left unregistered,
 * `text-subsection` counts as a colour and merging
 * it after `text-white` DROPS the white, because tailwind-merge keeps only the
 * last class in a group. That is not hypothetical: it silently removed the
 * foreground from every variant of every button in the app.
 */
export const TYPE_SCALE_STEPS = [
  'micro',
  'caption',
  'small',
  'body',
  'subsection',
  'section',
  'title',
  'page-title',
  'display',
  'card-number',
  'hero',
  'hero-2',
  'hero-3',
] as const;

const twMerge = extendTailwindMerge({
  extend: {
    classGroups: {
      // Registered as literals, so they take precedence over the catch-all
      // `text-color` validator. Two steps still collapse to the last one, and a
      // step and a colour now coexist — which is the whole point.
      'font-size': [{ text: [...TYPE_SCALE_STEPS] }],
    },
  },
});

/** clsx + tailwind-merge — the canonical "join class names without conflicts" helper. */
export function cn(...inputs: ClassValue[]): string {
  return twMerge(clsx(inputs));
}
