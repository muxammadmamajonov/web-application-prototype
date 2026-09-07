import type { ReactNode } from 'react';
import { Field, inputCls } from '@/components/ui/Modal';
import { Switch } from '@/components/ui/Switch';
import { cn } from '@/lib/cn';

interface ProviderCreds {
  merchant_id?: string;
  service_id?: string;
  secret_key?: string;
}

/**
 * Shared payment-provider credential card (Payme / Click / UzCard / Humo).
 *
 * ONE design reused by BOTH the tenant business settings (`PaymentsTab`) and the
 * platform billing settings (`BillingProvidersTab`), so "add / configure a payment
 * provider" reads identically for every persona — the same surface the customer's
 * payment rails ultimately flow through.
 *
 * Two modes, picked by whether `onToggle` is supplied:
 *  • with toggle (platform) — header carries an enable `Switch`; the credential
 *    fields collapse when disabled.
 *  • without toggle (tenant) — the method grid above already gates visibility, so
 *    the card just shows the fields.
 */
export function PaymentProviderCard({
  label,
  sublabel,
  icon,
  withService = false,
  creds,
  onChange,
  enabled,
  onToggle,
  secretHint,
  merchantPlaceholder = 'merchant_id',
  servicePlaceholder = 'service_id',
}: {
  label: string;
  sublabel?: string;
  icon?: ReactNode;
  withService?: boolean;
  creds: ProviderCreds;
  onChange: (c: ProviderCreds) => void;
  enabled?: boolean;
  onToggle?: (v: boolean) => void;
  secretHint?: string;
  merchantPlaceholder?: string;
  servicePlaceholder?: string;
}) {
  const showToggle = typeof enabled === 'boolean' && !!onToggle;
  const collapsed = showToggle && !enabled;
  return (
    <div className="rounded-card border border-line bg-surface p-4">
      <div className={cn('flex items-center justify-between gap-3', !collapsed && 'mb-4')}>
        <span className="flex min-w-0 items-center gap-3">
          {icon && (
            <span className="grid h-9 w-9 shrink-0 place-items-center rounded-control bg-[color:var(--c-accent-soft)] text-[color:var(--c-accent-strong)]">
              {icon}
            </span>
          )}
          <span className="min-w-0">
            <span className="block truncate text-body font-semibold text-ink">{label}</span>
            {sublabel && <span className="block truncate text-caption text-muted">{sublabel}</span>}
          </span>
        </span>
        {showToggle && <Switch checked={!!enabled} onChange={onToggle} />}
      </div>

      {!collapsed && (
        <div className={cn('grid gap-3', withService ? 'sm:grid-cols-3' : 'sm:grid-cols-2')}>
          <Field label="Merchant ID">
            <input
              className={cn(inputCls, 'font-mono')}
              value={creds.merchant_id ?? ''}
              onChange={(e) => onChange({ ...creds, merchant_id: e.target.value })}
              placeholder={merchantPlaceholder}
            />
          </Field>
          {withService && (
            <Field label="Service ID">
              <input
                className={cn(inputCls, 'font-mono')}
                value={creds.service_id ?? ''}
                onChange={(e) => onChange({ ...creds, service_id: e.target.value })}
                placeholder={servicePlaceholder}
              />
            </Field>
          )}
          <Field label="Secret key" hint={secretHint}>
            <input
              type="password"
              className={inputCls}
              value={creds.secret_key ?? ''}
              onChange={(e) => onChange({ ...creds, secret_key: e.target.value })}
              placeholder="••••••••"
            />
          </Field>
        </div>
      )}
    </div>
  );
}
