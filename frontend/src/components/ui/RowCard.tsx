import { cn } from '@/lib/cn';
import { Avatar } from './Avatar';

interface Props {
  initial?: string;
  avatarTone?: string;
  /** Replace the default avatar with a custom node (thumbnail, icon…). Pass `null` to omit the leading slot. */
  leading?: React.ReactNode;
  title: React.ReactNode;
  subtitle?: React.ReactNode;
  leftBadge?: React.ReactNode;
  trailing?: React.ReactNode;
  onClick?: () => void;
  href?: string;
  className?: string;
}

/**
 * The repeating list-row layout used across the dashboards:
 * leading (avatar/thumb/icon)  |  title + subtitle  |  badge column  |  trailing.
 */
export function RowCard({
  initial, leading, title, subtitle, leftBadge, trailing, onClick, href, className,
}: Props) {
  const lead = leading !== undefined ? leading : <Avatar name={initial ?? '?'} size="md" />;
  const inner = (
    <div className={cn(
      'flex items-center gap-3 rounded-card border border-line bg-surface px-4 py-3 transition',
      (onClick || href) && 'cursor-pointer hover:border-ink/15 hover:shadow-card',
      className,
    )}>
      {lead}
      {/* These lines must NOT be `truncate`: at narrow widths that clipped the
          customer name and the money figure the row exists to show. They reflow
          instead; `break-words` stops one long unbroken token (a contract
          number, a pasted phone) from pushing the row past the viewport. */}
      <div className="min-w-0 flex-1">
        <div className="break-words font-medium">{title}</div>
        {subtitle && <div className="break-words text-caption text-muted">{subtitle}</div>}
      </div>
      {leftBadge}
      {trailing && <div className="ml-2 shrink-0">{trailing}</div>}
    </div>
  );

  if (href) return <a href={href}>{inner}</a>;
  if (onClick) return <button onClick={onClick} className="block w-full text-left">{inner}</button>;
  return inner;
}
