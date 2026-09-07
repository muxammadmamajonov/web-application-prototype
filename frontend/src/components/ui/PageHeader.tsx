import type { ReactNode } from 'react';
import { cn } from '@/lib/cn';

interface Props {
  title: ReactNode;
  /** One short line of context under the title. Render only when it genuinely
   *  helps — not a restatement of the title or a count already shown below. */
  subtitle?: ReactNode;
  /** Right-aligned actions — primary action last (rightmost). */
  actions?: ReactNode;
  className?: string;
}

/**
 * The single page-header standard across the admin console: a title, an optional
 * one-line subtitle, and right-aligned actions, on one baseline-aligned row.
 * `items-start` keeps a wrapped subtitle from pushing the action button down.
 */
export function PageHeader({ title, subtitle, actions, className }: Props) {
  return (
    <header className={cn('mb-6 flex items-start justify-between gap-4', className)}>
      <div className="min-w-0">
        <h1 className="h-page truncate">{title}</h1>
        {subtitle && <p className="mt-1 max-w-lead text-small text-muted">{subtitle}</p>}
      </div>
      {actions && (
        <div className="flex shrink-0 items-center gap-2 pt-0.5">{actions}</div>
      )}
    </header>
  );
}
