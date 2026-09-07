import { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Check, Copy, CreditCard } from 'lucide-react';
import { cn } from '@/lib/cn';
import { groups, onlyDigits } from '@/lib/card';
import { SCHEME_STYLES } from '@/lib/cardNetworks';
import type { PayoutCard } from '@/components/ui/CardManager';

/**
 * One destination card, as a compact row: network, grouped number, settlement
 * currency, copy.
 *
 * Full PAN rather than a mask — this is a receiving card the shop is about to
 * transfer money to, and a masked number cannot be typed into a bank. The
 * number never leaves this component: no logging, no analytics, no URL.
 *
 * Deliberately NOT a full-size card tile: at 1.586:1 each destination is ~220 px
 * on a 360 px phone, which pushes the rest of the sheet under the sticky footer.
 */
export function DestinationCardRow({ card, selected, onSelect, currency }: {
  card: PayoutCard;
  /** Present → the row is a radio in a group; absent → read-only. */
  onSelect?: () => void;
  selected?: boolean;
  /** Settlement currency, decided by the server's compatibility matrix. */
  currency?: string;
}) {
  const { t } = useTranslation();
  const [copied, setCopied] = useState(false);
  const digits = onlyDigits(card.number);
  const network = (card.network ?? card.scheme ?? '').toString();
  const style = SCHEME_STYLES[network.toLowerCase()] ?? SCHEME_STYLES.default;
  const last4 = digits.slice(-4);

  const copy = async () => {
    try {
      await navigator.clipboard.writeText(digits);
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    } catch { /* clipboard blocked in some Telegram WebViews — the text stays select-all */ }
  };

  const label = (
    <>
      {/* Grouped, tabular and select-all: an old WebView with no clipboard
          permission still leaves the owner a number they can select by hand. */}
      <span className="block select-all truncate font-mono text-small tabular-nums tracking-wide">
        {groups(digits) || '•••• •••• •••• ••••'}
      </span>
      <span className="block truncate text-micro uppercase tracking-wider text-muted">
        {[network.toUpperCase(), card.bank, currency].filter(Boolean).join(' · ')}
      </span>
    </>
  );

  return (
    <div
      data-testid="topup-card-row"
      className={cn(
        'flex items-center gap-2.5 rounded-control border bg-surface p-2.5',
        onSelect && selected ? 'border-ink ring-1 ring-ink' : 'border-line',
      )}
    >
      {/* A small scheme chip, not a card face: enough to tell Humo from Visa at
          a glance without spending a screen on it. */}
      <span className="grid h-9 w-11 shrink-0 place-items-center overflow-hidden rounded-icon"
            style={{ background: style.bg }}>
        {style.logo
          ? <img src={style.logo} alt="" className="max-h-4 max-w-[34px] object-contain" draggable={false} />
          : <CreditCard size={16} className="text-white/80" />}
      </span>

      {onSelect ? (
        <button
          type="button"
          role="radio"
          aria-checked={!!selected}
          onClick={onSelect}
          className="min-w-0 flex-1 text-left"
        >
          {label}
        </button>
      ) : (
        <span className="min-w-0 flex-1">{label}</span>
      )}

      {/* Its own control with a real localized accessible name — never a `title`
          tooltip, which never appears on the phone this runs on. 44 px on every
          pointer type; the word only appears once there is room for it. */}
      <button
        type="button"
        onClick={() => void copy()}
        aria-label={t('cards.copyCard', {
          bank: card.bank || network.toUpperCase(), last4,
          defaultValue: 'Karta raqamini nusxalash: {{bank}} ••{{last4}}',
        })}
        className="tap-target inline-flex h-11 min-w-11 shrink-0 items-center justify-center gap-1.5 rounded-icon border border-line px-2.5 text-caption font-medium text-ink-soft hover:border-ink/30 sm:px-3"
      >
        {copied ? <Check size={16} /> : <Copy size={16} />}
        <span className="hidden sm:inline">
          {copied ? t('common.copied', 'Nusxalandi') : t('common.copy', 'Nusxa olish')}
        </span>
      </button>

      {/* Announced, not just animated — the copy is the whole point of the row. */}
      <span role="status" aria-live="polite" className="sr-only">
        {copied ? t('common.copied', 'Nusxalandi') : ''}
      </span>
    </div>
  );
}
