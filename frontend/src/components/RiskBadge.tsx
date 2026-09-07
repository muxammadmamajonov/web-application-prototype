import { useTranslation } from 'react-i18next';
import { ShieldCheck, ShieldAlert, ShieldX, Loader2 } from 'lucide-react';
import { cn } from '@/lib/cn';
import { useRiskScore, type RiskScore, type RiskTier } from '@/lib/queries';
import { useSession } from '@/store/session';
import { canManageTenant } from '@/store/persona';

const TIER_STYLE: Record<RiskTier, { cls: string; icon: typeof ShieldCheck }> = {
  low:    { cls: 'border-success/30 bg-[color:var(--c-success-soft)] text-[color:var(--c-success-strong)]', icon: ShieldCheck },
  medium: { cls: 'border-warn/30 bg-[color:var(--c-warn-soft)] text-[color:var(--c-warn-strong)]',          icon: ShieldAlert },
  high:   { cls: 'border-danger/30 bg-[color:var(--c-danger-soft)] text-[color:var(--c-danger-strong)]',    icon: ShieldX },
};

/** Render a risk score the tenant has already loaded. */
function RiskBadgeView({ score, className }: { score: RiskScore; className?: string }) {
  const { t } = useTranslation();
  const tier = score.tier;
  const { cls, icon: Icon } = TIER_STYLE[tier];
  const shops = score.external.distinct_shops;

  let label: string;
  if (tier === 'low') {
    label = t('risk.clean', 'Tarix toza');
  } else if (tier === 'medium') {
    label = score.external.signal_count > 0
      ? t('risk.mediumExternal', { count: score.external.signal_count, defaultValue: '{{count}} ta belgi: o\'rta xavf' })
      : t('risk.mediumInternal', 'O\'rta xavf');
  } else {
    label = shops > 0
      ? t('risk.highShops', { count: shops, defaultValue: 'Yuqori xavf — {{count}} ta do\'konda muammo' })
      : t('risk.high', 'Yuqori xavf');
  }

  return (
    <span className={cn('inline-flex items-center gap-1.5 rounded-pill border px-3 py-1 text-caption font-medium', cls, className)}>
      <Icon size={14} />
      {label}
    </span>
  );
}

/** Self-fetching badge keyed on a customer id. Renders nothing while idle.
 *
 * (MUDD-55 review) `GET /risk/score` is owner/manager-only — credit-risk
 * scoring is derived from the same JSHSHIR-keyed KYC data the rest of the
 * tenant surface gates. Skip the fetch for sales so the badge doesn't throw
 * a 403 into the console every time a customer modal opens for that role. */
export function RiskBadge({ customerId, className }: { customerId: string | null | undefined; className?: string }) {
  const role = useSession((s) => s.activeRole());
  const canSeeRisk = canManageTenant(role);
  const q = useRiskScore(canSeeRisk ? customerId : null);
  if (!canSeeRisk || !customerId) return null;
  if (q.isLoading) {
    return (
      <span className="inline-flex items-center gap-1.5 text-caption text-muted">
        <Loader2 size={14} className="animate-spin" />
      </span>
    );
  }
  if (!q.data) return null;
  return <RiskBadgeView score={q.data} className={className} />;
}
