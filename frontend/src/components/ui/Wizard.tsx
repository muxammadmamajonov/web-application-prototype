import { type ReactNode, useEffect, useId, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { ArrowLeft, Check, Loader2 } from 'lucide-react';
import { cn } from '@/lib/cn';
import { useFocusTrap } from '@/hooks/useFocusTrap';
import { isInsideTelegramClient } from '@/lib/telegram';
import { useTelegramBackButton } from '@/hooks/useTelegramBackButton';
import { useMainButton } from '@/lib/telegram/buttons';
import { useClosingConfirmation } from '@/lib/telegram/closing';
import { haptics } from '@/lib/telegram/haptics';

interface WizardStep {
  key: string;
  /** Short step name shown under the progress bar. */
  title: string;
  /** Gate Next/Create — when false the primary button is disabled. Default true. */
  valid?: boolean;
  render: () => ReactNode;
}

interface Props {
  open: boolean;
  /** The flow title (e.g. "Yangi kategoriya") — centered at the top. */
  title: string;
  steps: WizardStep[];
  submitting?: boolean;
  error?: string | null;
  submitLabel?: string;
  onSubmit: () => void;
  onClose: () => void;
}

/**
 * Full-screen, STEPPED create/edit flow — the platform standard that replaces
 * cramped center-modals for "Add / Create" actions. One step = one full screen
 * (the "separate page per section" the product wants), wired to Telegram natively:
 *
 *   • native Back walks to the previous step (closes on step 1) via the back-stack
 *   • native MainButton is the primary action: "Keyingi →" then "Yaratish",
 *     disabled until the step is valid, with a submit spinner + double-tap guard
 *   • a slim segmented progress bar shows where you are
 *   • closing-confirmation guards an accidental swipe-close mid-flow
 *
 * In a browser the same flow renders an in-page footer (Back + Next/Create).
 */
export function Wizard({ open, title, steps, submitting, error, submitLabel, onSubmit, onClose }: Props) {
  const { t } = useTranslation();
  const [current, setCurrent] = useState(0);

  // Reset to the first step whenever the flow (re)opens.
  useEffect(() => { if (open) setCurrent(0); }, [open]);
  // Lock background scroll while open.
  useEffect(() => {
    if (!open) return;
    const prev = document.body.style.overflow;
    document.body.style.overflow = 'hidden';
    return () => { document.body.style.overflow = prev; };
  }, [open]);

  const count = steps.length;
  const isLast = current >= count - 1;
  const step = steps[current];
  const stepValid = step?.valid !== false;

  const goBack = () => {
    if (current > 0) { haptics.selection(); setCurrent((c) => c - 1); }
    else onClose();
  };
  const goPrimary = () => {
    if (!stepValid || submitting) return;
    if (isLast) { onSubmit(); return; }
    haptics.selection();
    setCurrent((c) => Math.min(count - 1, c + 1));
  };

  // Native Back (prev step / close) + native MainButton (Next / Create).
  useTelegramBackButton(open, goBack);
  useClosingConfirmation(open);
  const { isNative } = useMainButton({
    text: isLast ? (submitLabel ?? t('common.create', 'Yaratish')) : `${t('common.next', 'Keyingi')} →`,
    visible: open,
    enabled: stepValid,
    loading: !!submitting && isLast,
    onClick: goPrimary,
  });

  const titleId = useId();
  const panelRef = useRef<HTMLDivElement>(null);
  // Keeps focus inside this full-screen flow and returns it on close.
  useFocusTrap(panelRef, open, onClose);
  if (!open) return null;
  const insideTg = isInsideTelegramClient();

  // role="dialog" + aria-modal MUST carry an accessible name, or this opens
  // silently for a screen-reader user. Named from its own visible heading.
  return (
    <div ref={panelRef} className="fixed inset-0 z-[80] flex flex-col bg-cream"
         role="dialog" aria-modal="true" aria-labelledby={titleId}>
      {/* Header — centered title (mirrors MiniAppHeader) + progress + step name. */}
      <header
        className="border-b border-line bg-cream/95 backdrop-blur"
        style={{ paddingTop: insideTg ? 'var(--tg-safe-top)' : 'max(0.5rem, env(safe-area-inset-top))' }}
      >
        <div className="relative flex h-12 items-center justify-center px-[84px]">
          {!insideTg && (
            <button onClick={goBack} aria-label={t('common.back', 'Orqaga')}
                    className="tap-target absolute left-2 grid h-11 w-11 place-items-center rounded-xl text-ink-soft hover:bg-ink/5">
              <ArrowLeft size={18} />
            </button>
          )}
          <h1 id={titleId} className="truncate text-center text-section font-semibold tracking-tight text-ink">{title}</h1>
        </div>
        {/* Segmented progress + step label. */}
        <div className="px-4 pb-3">
          <div className="flex gap-1.5">
            {steps.map((s, i) => (
              <span key={s.key} className={cn('h-1 flex-1 rounded-full transition-colors',
                i < current ? 'bg-[color:var(--c-accent-fill)]' : i === current ? 'bg-[color:var(--c-accent-fill)]' : 'bg-ink/10')} />
            ))}
          </div>
          <div className="mt-1.5 flex items-center justify-between text-micro">
            <span className="font-medium uppercase tracking-wider text-muted">{step?.title}</span>
            <span className="tabular-nums text-muted">{current + 1} / {count}</span>
          </div>
        </div>
      </header>

      {/* Step body. */}
      <div className="flex-1 overflow-y-auto px-4 py-5">
        <div className="mx-auto w-full max-w-lg">
          {step?.render()}
          {error && (
            <div role="alert" className="mt-4 rounded-xl border border-danger/30 bg-[color:var(--c-danger-soft)] px-3 py-2 text-sm text-danger">{error}</div>
          )}
        </div>
      </div>

      {/* Browser footer (inside Telegram the native MainButton drives this). */}
      {!isNative && (
        <div className="flex items-center gap-2 border-t border-line bg-cream px-4 py-3"
             style={{ paddingBottom: 'max(0.75rem, env(safe-area-inset-bottom))' }}>
          {current > 0 && (
            <button onClick={goBack}
                    className="inline-flex h-11 items-center gap-1.5 rounded-pill border border-line px-4 text-sm font-semibold text-ink-soft hover:text-ink">
              <ArrowLeft size={16} /> {t('common.back', 'Orqaga')}
            </button>
          )}
          <button onClick={goPrimary} disabled={!stepValid || submitting}
                  className="ml-auto inline-flex h-11 min-w-[8rem] items-center justify-center gap-2 rounded-pill bg-[color:var(--c-accent-fill)] px-5 text-sm font-semibold text-white shadow-sm transition hover:brightness-105 disabled:opacity-50">
            {submitting && isLast && <Loader2 size={15} className="animate-spin" />}
            {isLast ? (<><Check size={16} /> {submitLabel ?? t('common.create', 'Yaratish')}</>) : <>{t('common.next', 'Keyingi')} →</>}
          </button>
        </div>
      )}
    </div>
  );
}
