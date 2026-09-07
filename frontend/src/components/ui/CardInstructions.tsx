import { Copy } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import { toast } from '@/lib/feedback';

/** (#369) The tenant's card/transfer details for the cash & manual-transfer
 *  path — shown BEFORE the customer is asked to pay or upload proof, so they
 *  don't have to leave the flow to find the card number. One-tap copy; the
 *  text stays select-all for old WebViews without clipboard access. */
export function CardInstructions({ text }: { text: string }) {
  const { t } = useTranslation();
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(text);
      toast.success(t('common.copied', 'Nusxalandi'));
    } catch { /* clipboard unavailable — the select-all text remains */ }
  };
  return (
    <div className="rounded-card border border-line bg-surface p-4">
      <div className="text-micro uppercase tracking-wider text-muted">
        {t('client.payInstructions', "To'lov rekvizitlari")}
      </div>
      <div className="mt-1.5 flex items-start justify-between gap-2">
        <p className="min-w-0 flex-1 select-all whitespace-pre-wrap font-mono text-small leading-relaxed">{text}</p>
        <button
          type="button"
          onClick={() => void copy()}
          className="inline-flex h-9 shrink-0 items-center gap-1 rounded-pill border border-line bg-cream px-3 text-caption font-medium text-ink-soft hover:border-ink/30"
        >
          <Copy size={14} /> {t('common.copy', 'Nusxalash')}
        </button>
      </div>
    </div>
  );
}
