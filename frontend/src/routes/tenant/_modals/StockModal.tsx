import { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { useQueryClient } from '@tanstack/react-query';
import { Loader2, ArrowDownCircle, ArrowUpCircle, RotateCcw, Trash2, ClipboardCheck } from 'lucide-react';
import { Modal, Field, inputCls } from '@/components/ui/Modal';
import { Button } from '@/components/ui/Button';
import { Badge } from '@/components/ui/Badge';
import {
  useProductMovements, adjustStock,
  type ProductRow, type StockAdjustBody, type MovementRow,
} from '@/lib/queries';
import { queryKeys } from '@/lib/queryKeys';
import { shortDate } from '@/lib/format';
import { toast } from '@/lib/feedback';

interface Props {
  open: boolean;
  onClose: () => void;
  product: ProductRow | null;
}

type Kind = StockAdjustBody['kind'];

// (#543) Generous upper bound — a stock count this large is a typo (e.g. a phone
// number fat-fingered into the qty field), not a real inventory total. Guards
// against absurd values without rejecting any realistic count.
const MAX_QTY = 10_000_000;

const KIND_TONE: Record<MovementRow['kind'], 'success' | 'danger' | 'neutral' | 'accent' | 'warn'> = {
  in: 'success',
  return: 'success',
  out: 'danger',
  sale: 'danger',
  writeoff: 'warn',
  inventory: 'accent',
};

/** Stock movements timeline + a manual in/out/return/write-off/count form.
 *  Kept as a standalone modal for backward-compat; the body is also embedded
 *  directly inside the product Edit form via {@link StockPanel}. */
export function StockModal({ open, onClose, product }: Props) {
  const { t } = useTranslation();
  return (
    <Modal
      open={open}
      onClose={onClose}
      title={t('stock.title', 'Ombor harakati')}
      subtitle={product ? `${product.name_uz} · ${t('products.stockShort')}: ${product.stock ?? 0}` : ''}
      size="lg"
    >
      <StockPanel product={open ? product : null} />
    </Modal>
  );
}

/** The stock adjust form + history timeline, without a Modal wrapper — embedded
 *  inside the product Edit form (and inside {@link StockModal}). */
export function StockPanel({ product }: { product: ProductRow | null }) {
  const { t } = useTranslation();
  const qc = useQueryClient();
  const movements = useProductMovements(product?.id ?? null);
  const [kind, setKind] = useState<Kind>('in');
  const [qty, setQty] = useState('');
  const [reason, setReason] = useState('');
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  const kindLabel = (k: MovementRow['kind']) => t(`stock.kind.${k}`, k);

  const submit = async () => {
    if (!product) return;
    const n = Number(qty);
    if (!qty || Number.isNaN(n) || n < 0 || n > MAX_QTY) { setErr(t('stock.errQty', 'Miqdorni kiriting')); return; }
    setErr(null); setBusy(true);
    try {
      await adjustStock(product.id, { qty: n, kind, reason: reason || undefined });
      await qc.invalidateQueries({ queryKey: queryKeys.tenant.productsAll() });
      await qc.invalidateQueries({ queryKey: queryKeys.tenant.productMovements(product.id) });
      await qc.invalidateQueries({ queryKey: queryKeys.tenant.lowStock() });
      setQty(''); setReason('');
      toast.success(t('common.saved', 'Saqlandi'));
    } catch (ex) {
      const e = ex as { response?: { data?: { detail?: string } } };
      setErr(e.response?.data?.detail ?? t('common.errorLoading'));
    } finally { setBusy(false); }
  };

  const kinds: { value: Kind; icon: typeof ArrowUpCircle }[] = [
    { value: 'in', icon: ArrowDownCircle },
    { value: 'out', icon: ArrowUpCircle },
    { value: 'return', icon: RotateCcw },
    { value: 'writeoff', icon: Trash2 },
    { value: 'inventory', icon: ClipboardCheck },
  ];

  return (
      <div className="grid gap-5">
        <div className="rounded-xl border border-line bg-cream p-4">
          <div className="mb-3 flex flex-wrap gap-2">
            {kinds.map(({ value, icon: Icon }) => (
              <button key={value} type="button" onClick={() => setKind(value)}
                      className={`flex items-center gap-1.5 rounded-pill border px-3 py-1.5 text-small ${
                        kind === value ? 'border-accent bg-white font-semibold text-accent' : 'border-line text-muted'
                      }`}>
                <Icon size={14} /> {kindLabel(value)}
              </button>
            ))}
          </div>
          <div className="grid gap-3 sm:grid-cols-2">
            <Field label={kind === 'inventory'
              ? t('stock.countedTotal', 'Sanab chiqilgan jami')
              : t('stock.qty', 'Miqdor')}>
              {/* (#543) onWheel blur: scrolling over the focused field can't silently
                  change the count (the native number-input pitfall). */}
              <input type="number" inputMode="numeric" min={0} max={MAX_QTY}
                     className={inputCls} value={qty}
                     onChange={(e) => setQty(e.target.value)}
                     onWheel={(e) => e.currentTarget.blur()} />
            </Field>
            <Field label={t('stock.reason', 'Izoh')}>
              <input className={inputCls} value={reason}
                     onChange={(e) => setReason(e.target.value)} />
            </Field>
          </div>
          {err && <div className="mt-2 text-sm text-danger">{err}</div>}
          <div className="mt-3 flex justify-end">
            <Button variant="primary" size="md" onClick={submit} loading={busy}>
              {busy && <Loader2 size={14} className="animate-spin" />}
              {t('stock.apply', 'Saqlash')}
            </Button>
          </div>
        </div>

        <div>
          <div className="mb-2 text-micro uppercase tracking-wider text-muted">
            {t('stock.history', 'Harakatlar tarixi')}
          </div>
          {movements.isLoading ? (
            <div className="flex justify-center py-6"><Loader2 size={18} className="animate-spin text-muted" /></div>
          ) : (movements.data ?? []).length === 0 ? (
            <div className="py-6 text-center text-sm text-muted">{t('stock.empty', 'Hali harakat yo‘q')}</div>
          ) : (
            <ul className="divide-y divide-line">
              {(movements.data ?? []).map((m) => (
                <li key={m.id} className="flex items-center justify-between gap-3 py-2.5">
                  <div className="flex items-center gap-2 min-w-0">
                    <Badge tone={KIND_TONE[m.kind]}>{kindLabel(m.kind)}</Badge>
                    <span className="truncate text-sm text-muted">{m.reason ?? '—'}</span>
                  </div>
                  <div className="flex items-center gap-3 shrink-0">
                    <span className={`tabular-nums font-semibold ${m.qty < 0 ? 'text-danger' : 'text-[color:var(--c-success)]'}`}>
                      {m.qty > 0 ? `+${m.qty}` : m.qty}
                    </span>
                    <span className="text-caption text-muted">{shortDate(m.created_at)}</span>
                  </div>
                </li>
              ))}
            </ul>
          )}
        </div>
      </div>
  );
}
