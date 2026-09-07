/* eslint-disable react-refresh/only-export-components -- lib module: the
   module-level toast/confirmDialog API is colocated with its <FeedbackHost/>.
   The rule only guards dev-time Fast Refresh. */
import { useEffect, useId, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { AlertCircle, AlertTriangle, CheckCircle2, Info, X } from 'lucide-react';
import i18n from '@/i18n';
import { Button } from '@/components/ui/Button';
import { cn } from '@/lib/cn';
import { useFocusTrap } from '@/hooks/useFocusTrap';
import { nativeConfirm } from '@/lib/telegram/dialogs';

/**
 * App-wide branded feedback: toasts + confirm dialog. Module-level stores so
 * any module can call `toast.error(...)` / `await confirmDialog(...)` without
 * threading context — a single <FeedbackHost/> (mounted in App) renders them.
 * Replaces native alert()/confirm() which looked off-brand and unstyled.
 */

// ── Toasts ───────────────────────────────────────────────────────────────────
/**
 * One toast design for the whole product.
 *
 * Every toast renders on the SAME neutral surface with the same ink, border and
 * shadow; the only thing status changes is the icon and the accent bar. Tinting
 * the whole panel per status — the previous design — meant four different
 * background/foreground pairs to keep legible, and the danger pairing in
 * particular sat at the bottom of the contrast range. Colour here is a signal
 * layered onto readable text, not the thing you have to read through.
 */
export type ToastKind = 'success' | 'error' | 'warning' | 'info';

/** Icon + accent per status. Accent drives the bar AND the icon, nothing else. */
const TOAST_TONE: Record<ToastKind, { Icon: typeof Info; accent: string }> = {
  success: { Icon: CheckCircle2,  accent: 'var(--c-success-strong)' },
  error:   { Icon: AlertTriangle, accent: 'var(--c-danger-strong)' },
  warning: { Icon: AlertCircle,   accent: 'var(--c-warn-strong)' },
  info:    { Icon: Info,          accent: 'var(--c-info-strong)' },
};

/**
 * Only errors interrupt. Assertive yanks a screen-reader user out of whatever
 * they are reading, so it is reserved for the case where continuing without
 * knowing is actually harmful — a failed money action. A warning still matters,
 * but it can wait for a pause in speech.
 */
const ASSERTIVE: ReadonlySet<ToastKind> = new Set<ToastKind>(['error']);

interface Toast {
  id: number;
  kind: ToastKind;
  msg: string;
  action?: { label: string; onAction: () => void | Promise<void> };
}

let toasts: Toast[] = [];
const toastSubs = new Set<() => void>();
let seq = 1;
const emitToasts = () => toastSubs.forEach((f) => f());

function push(kind: ToastKind, msg: string, action?: { label: string; onAction: () => void | Promise<void> }) {
  const id = seq++;
  toasts = [...toasts, { id, kind, msg, action }];
  emitToasts();
  // Undo toasts get 5s, others get 4.2s
  const timeout = action ? 5000 : 4200;
  setTimeout(() => {
    toasts = toasts.filter((t) => t.id !== id);
    emitToasts();
  }, timeout);
}

function dismissToast(id: number) {
  toasts = toasts.filter((t) => t.id !== id);
  emitToasts();
}

type ToastAction = { label: string; onAction: () => void | Promise<void> };

export const toast = {
  success: (m: string, action?: ToastAction) => push('success', m, action),
  error:   (m: string, action?: ToastAction) => push('error', m, action),
  warning: (m: string, action?: ToastAction) => push('warning', m, action),
  info:    (m: string, action?: ToastAction) => push('info', m, action),
};

// ── Confirm ──────────────────────────────────────────────────────────────────
interface ConfirmOpts {
  message: string;
  title?: string;
  confirmLabel?: string;
  cancelLabel?: string;
  danger?: boolean;
}
interface ConfirmReq extends ConfirmOpts { resolve: (ok: boolean) => void }
let confirmReq: ConfirmReq | null = null;
const confirmSubs = new Set<() => void>();
const emitConfirm = () => confirmSubs.forEach((f) => f());

export function confirmDialog(opts: string | ConfirmOpts): Promise<boolean> {
  const o: ConfirmOpts = typeof opts === 'string' ? { message: opts } : opts;
  // Inside Telegram, a native popup (with a real destructive button) reads as a
  // deliberate OS-level decision — better for critical BNPL actions than a div.
  // Falls through to the branded in-app dialog in a browser / on old clients.
  const native = nativeConfirm({
    title: o.title,
    message: o.message,
    confirmLabel: o.confirmLabel,
    cancelLabel: o.cancelLabel,
    destructive: o.danger,
  });
  if (native) return native;
  return new Promise((resolve) => {
    confirmReq = { ...o, resolve };
    emitConfirm();
  });
}

// ── Host ─────────────────────────────────────────────────────────────────────
export function FeedbackHost() {
  const [, force] = useState(0);
  useEffect(() => {
    const f = () => force((n) => n + 1);
    toastSubs.add(f);
    confirmSubs.add(f);
    return () => { toastSubs.delete(f); confirmSubs.delete(f); };
  }, []);

  const settle = (ok: boolean) => {
    const req = confirmReq;
    confirmReq = null;
    force((n) => n + 1);
    req?.resolve(ok);
  };

  const req = confirmReq;
  const confirmPanelRef = useRef<HTMLDivElement>(null);
  const confirmTitleId = useId();
  const confirmMsgId = useId();
  // (#161) Esc-to-cancel + focus-trap + focus-return on the confirm dialog.
  useFocusTrap(confirmPanelRef, !!req, () => settle(false));

  return createPortal(
    <>
      <div className="pointer-events-none fixed inset-x-0 top-0 z-[130] flex flex-col items-center gap-2 px-4 pt-[max(12px,env(safe-area-inset-top))]">
        {toasts.map((t) => {
          const { Icon, accent } = TOAST_TONE[t.kind];
          const assertive = ASSERTIVE.has(t.kind);
          return (
          <div
            key={t.id}
            data-toast-kind={t.kind}
            // `role` alone leaves politeness to the reader's mapping, so state
            // it explicitly rather than relying on the implicit default.
            role={assertive ? 'alert' : 'status'}
            aria-live={assertive ? 'assertive' : 'polite'}
            aria-atomic="true"
            // The accent bar is the ONLY place status changes the panel. Set as
            // an inline custom property so the same class list serves all four
            // states — Tailwind cannot generate a class per runtime token.
            style={{ borderInlineStartColor: accent }}
            className={cn(
              'pointer-events-auto flex max-w-panel items-start gap-2.5 rounded-control',
              'border border-line border-s-[3px] bg-surface px-4 py-3',
              'text-body text-ink shadow-pop animate-in slide-in-from-top',
            )}
          >
            <Icon size={16} className="mt-0.5 shrink-0" style={{ color: accent }} aria-hidden="true" />
            <span className="min-w-0 flex-1">{t.msg}</span>
            {t.action && (
              <button
                type="button"
                onClick={async () => {
                  await t.action?.onAction();
                  dismissToast(t.id);
                }}
                className="ml-2 whitespace-nowrap rounded-micro px-2 py-1 text-caption font-medium opacity-80 hover:opacity-100"
              >
                {t.action?.label}
              </button>
            )}
            <button
              type="button"
              onClick={() => dismissToast(t.id)}
              aria-label={i18n.t('common.close', 'Yopish')}
              className="tap-target -mr-1 -mt-0.5 grid h-11 w-11 shrink-0 place-items-center rounded-micro opacity-60 hover:opacity-100"
            >
              <X size={14} />
            </button>
          </div>
          );
        })}
      </div>

      {req && (
        /* This dialog stands between a user and every confirmed money action,
           so it MUST carry an accessible name — from its own heading, or from
           the message when there is no heading — and a description either way.
           Without one it opens silently for a screen-reader user about to
           approve a payment. */
        <div className="fixed inset-0 z-[130] grid place-items-center px-4"
             role="alertdialog" aria-modal="true"
             aria-labelledby={req.title ? confirmTitleId : confirmMsgId}
             aria-describedby={confirmMsgId}>
          <button type="button" aria-label={req.cancelLabel ?? i18n.t('common.cancel')} onClick={() => settle(false)} className="absolute inset-0 bg-ink/55 backdrop-blur-sm" />
          <div ref={confirmPanelRef} className="relative w-full max-w-narrow rounded-card border border-line bg-surface p-6 shadow-pop">
            {req.title && <h2 id={confirmTitleId} className="text-section font-semibold leading-tight">{req.title}</h2>}
            <p id={confirmMsgId} className={cn('text-body text-ink-soft whitespace-pre-line', req.title && 'mt-2')}>{req.message}</p>
            <div className="mt-5 flex justify-end gap-2">
              <Button variant="outline" size="md" onClick={() => settle(false)}>
                {req.cancelLabel ?? i18n.t('common.cancel')}
              </Button>
              <Button
                variant="primary"
                size="md"
                onClick={() => settle(true)}
                className={req.danger ? 'bg-danger hover:brightness-110' : undefined}
              >
                {req.confirmLabel ?? i18n.t('common.confirm')}
              </Button>
            </div>
          </div>
        </div>
      )}
    </>,
    document.body,
  );
}
