import { useTranslation } from 'react-i18next';
import { Badge, type BadgeTone } from './Badge';
import { cn } from '@/lib/cn';

/** The three situations a customer can be in, as sent by the API. */
export type TelegramStatus = 'joined' | 'known' | 'new';

/**
 * Whether this customer can be reached in Telegram.
 *
 * There are THREE states, not two. The UI used to render `telegram_id ? … : …`,
 * which told an operator "Telegram ulanmagan" about somebody who already has a
 * Muddato account from another shop — a person the platform can message right
 * now. The operator would then go hunting for a link to copy out by hand, for
 * no reason.
 *
 *   joined  linked to THIS shop; they can open their own contracts
 *   known   not linked here, but they already use the bot — an invitation is
 *           delivered to them in Telegram automatically
 *   new     nobody on the platform has this number; the link must be handed
 *           over some other way
 *
 * One component for every place this appears, so the third state cannot be
 * added in one list and forgotten in another — which is exactly how the two
 * customer pages drifted apart before.
 */
const TONE: Record<TelegramStatus, BadgeTone> = {
  joined: 'success',
  known: 'info',
  new: 'neutral',
};

export function TelegramStatusBadge({
  status, compact = false, className,
}: {
  status: TelegramStatus | undefined;
  /** Short labels for a table cell; full sentences for a detail panel. */
  compact?: boolean;
  className?: string;
}) {
  const { t } = useTranslation();
  // An older API response without the field degrades to the honest minimum
  // rather than guessing.
  const s: TelegramStatus = status ?? 'new';
  return (
    <Badge tone={TONE[s]} className={cn('whitespace-nowrap', className)}>
      {compact ? shortLabel(s, t) : fullLabel(s, t)}
    </Badge>
  );
}

/* The six keys are spelled out rather than built as
   `customers.tgStatus.${s}`, so `localeCompleteness.test.ts` can still see
   which keys this file uses. A computed key is invisible to that scan, and the
   whole point of it is to catch a label that exists in uz but not ru — the
   failure nobody notices until a Russian-speaking operator sees a raw key. */
type T = ReturnType<typeof useTranslation>['t'];

function fullLabel(s: TelegramStatus, t: T): string {
  if (s === 'joined') return t('customers.tgStatus.joined');
  if (s === 'known') return t('customers.tgStatus.known');
  return t('customers.tgStatus.new');
}

function shortLabel(s: TelegramStatus, t: T): string {
  if (s === 'joined') return t('customers.tgStatusShort.joined');
  if (s === 'known') return t('customers.tgStatusShort.known');
  return t('customers.tgStatusShort.new');
}
