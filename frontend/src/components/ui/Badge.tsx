import { useTranslation } from 'react-i18next';
import { cn } from '@/lib/cn';

export type BadgeTone =
  | 'neutral' | 'accent' | 'success' | 'warn' | 'danger' | 'info' | 'inverse';

const tones: Record<BadgeTone, string> = {
  neutral: 'bg-line text-ink-soft',
  accent:  'bg-accent-soft text-accent-strong',
  success: 'bg-success-soft text-success-strong',
  warn:    'bg-warn-soft text-warn-strong',
  danger:  'bg-danger-soft text-danger-strong',
  info:    'bg-info-soft text-info-strong',
  inverse: 'bg-ink text-white',
};

export function Badge({
  children,
  tone = 'neutral',
  className,
}: {
  children: React.ReactNode;
  tone?: BadgeTone;
  className?: string;
}) {
  return (
    <span className={cn('pill', tones[tone], className)}>{children}</span>
  );
}

/** Localized status capsule — full words from the i18n `status.*` block with
 *  semantic tone colors. (Previously cryptic 3-letter mono codes like OVD/DONE.) */
const STATUS_TONE: Record<string, BadgeTone> = {
  active: 'success', demo: 'accent', suspended: 'warn', blocked: 'danger',
  pending: 'warn', approved: 'success', paid: 'success', sent: 'success',
  rejected: 'danger', failed: 'danger', refunded: 'danger', overdue: 'danger',
  completed: 'neutral', cancelled: 'neutral', void: 'neutral', expired: 'neutral',
  draft: 'neutral', open: 'warn', grace: 'warn', scheduled: 'accent', sending: 'accent',
};

export function StatusBadge({ status, className }: { status: string; className?: string }) {
  const { t } = useTranslation();
  const tone = STATUS_TONE[status] ?? 'neutral';
  return <Badge tone={tone} className={cn('whitespace-nowrap', className)}>{t(`status.${status}`, status)}</Badge>;
}
