import { useTranslation } from 'react-i18next';
import { RotateCw, WifiOff } from 'lucide-react';
import { apiErrorMessage, errorKind, isRetryable } from '@/lib/errors';

/**
 * Shared "fetch failed" state with an optional retry button. Mirrors the error
 * branch baked into DataTable so admin list pages that build their own list
 * markup (RowCard lists, ItemList, …) render a *failure* state instead of the
 * empty state — otherwise a 401/5xx looks like "there's no data here" and
 * misleads admins.
 *
 * Given the actual `error`, this says something different about each failure:
 * offline on shop-floor mobile data reads as "check your connection", a
 * permission failure names the role required, and a validation failure does not
 * offer a Retry that cannot possibly help.
 */
export function ErrorCard({ onRetry, message, error }: {
  onRetry?: () => void;
  message?: string;
  /**
   * The failure itself — **required**, and deliberately so.
   *
   * Required, not optional: an optional prop is omitted, and every failure then
   * renders the same generic line. A call site must either hand over the real
   * error or say `error={null}` on purpose.
   */
  error: unknown;
}) {
  const { t } = useTranslation();
  const kind = error != null ? errorKind(error) : undefined;
  const offline = kind === 'offline';
  // Retry is offered only where repeating the identical request could succeed:
  // a 403 or a 422 will fail the same way however many times it is pressed.
  const canRetry = !!onRetry && (error == null || isRetryable(error));

  const text = message
    ?? (error != null ? apiErrorMessage(error) : t('common.loadFailed', "Ma'lumot olinmadi."));

  return (
    <div role="alert" aria-live="assertive"
         className="card flex flex-col items-center gap-3 px-6 py-14 text-center">
      {offline && <WifiOff size={20} className="text-muted" aria-hidden="true" />}
      <p className="text-body text-danger">{text}</p>
      {offline && (
        <p className="text-caption text-muted">
          {t('common.offlineAutoRetry', 'Aloqa tiklanganda maʼlumot avtomatik yangilanadi.')}
        </p>
      )}
      {canRetry && (
        <button
          onClick={onRetry}
          className="tap-target inline-flex min-h-11 items-center gap-1.5 rounded-control border border-line bg-surface px-3 py-2 text-body font-medium text-ink-soft hover:bg-ink/5"
        >
          <RotateCw size={14} /> {t('common.retry', 'Qayta urinish')}
        </button>
      )}
    </div>
  );
}
