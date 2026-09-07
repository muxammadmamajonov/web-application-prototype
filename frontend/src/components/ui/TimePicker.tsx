import { useEffect, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { useTranslation } from 'react-i18next';
import { Clock } from 'lucide-react';
import { cn } from '@/lib/cn';
import { inputCls } from './Modal';
import { pad } from './dateGrid';
import { useAnchoredOverlay } from '@/hooks/useAnchoredOverlay';

/** Two 40px columns (`w-40`) plus padding — the panel's fixed width. */
const PANEL_WIDTH = 160;

/** Device-independent time picker (HH:MM, 24h) — column scrollers for hour and
 *  minute so the UI is identical everywhere instead of the OS time wheel.
 *
 *  The panel is rendered in a PORTAL and positioned against the visual
 *  viewport (see `useAnchoredOverlay`, shared with `DatePicker`) — laid out in
 *  place it belongs to whatever `overflow-hidden`/`overflow-y-auto` ancestor
 *  holds the field (a Modal's dialog card), which clips it. */
export function TimePicker({
  value, onChange, minuteStep = 5, id, placeholder,
}: {
  value: string;
  onChange: (hhmm: string) => void;
  minuteStep?: number;
  id?: string;
  placeholder?: string;
}) {
  const { t } = useTranslation();
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);
  const panelRef = useRef<HTMLDivElement>(null);
  const { triggerRef: anchorRef, pos } = useAnchoredOverlay(open, PANEL_WIDTH);

  useEffect(() => {
    if (!open) return;
    const onDoc = (e: MouseEvent) => {
      const target = e.target as Node;
      // The panel is portalled out of `ref`, so both roots count as "inside".
      if (ref.current?.contains(target) || panelRef.current?.contains(target)) return;
      setOpen(false);
    };
    document.addEventListener('mousedown', onDoc);
    return () => document.removeEventListener('mousedown', onDoc);
  }, [open]);

  const [hh, mm] = (value || '').split(':');
  const hour = hh ?? '';
  const minute = mm ?? '';

  const hours = Array.from({ length: 24 }, (_, i) => pad(i));
  const minutes = Array.from({ length: Math.ceil(60 / minuteStep) }, (_, i) => pad(i * minuteStep));

  const setPart = (h: string, m: string) => {
    if (h && m) onChange(`${h}:${m}`);
    else if (h) onChange(`${h}:${minute || '00'}`);
    else if (m) onChange(`${hour || '00'}:${m}`);
  };

  return (
    <div ref={ref} className="relative">
      <button
        ref={(el) => { anchorRef.current = el; }}
        type="button"
        id={id}
        onClick={() => setOpen((v) => !v)}
        className={cn(inputCls, 'flex items-center gap-2 text-left', !value && 'text-muted')}
      >
        <Clock size={16} className="text-muted" />
        {value || placeholder || '--:--'}
      </button>

      {open && pos && createPortal(
        <div
          ref={panelRef}
          data-testid="time-picker-panel"
          style={{ left: pos.left, top: pos.top, width: pos.width, maxHeight: pos.maxHeight, transform: pos.transform }}
          // z-111 is the shared layer for portalled popovers (see DatePicker).
          className="fixed z-[111] flex gap-1 overflow-y-auto overscroll-contain rounded-card border border-line bg-surface p-2 shadow-pop">
          <div className="max-h-52 flex-1 overflow-y-auto">
            <div className="mb-1 text-center text-micro uppercase tracking-wider text-muted">{t('common.hour', 'soat')}</div>
            {hours.map((h) => (
              <button key={h} type="button"
                      onClick={() => setPart(h, minute)}
                      className={cn('block w-full rounded-icon py-1.5 text-center text-body', h === hour ? 'bg-ink text-white' : 'hover:bg-ink/5')}>
                {h}
              </button>
            ))}
          </div>
          <div className="max-h-52 flex-1 overflow-y-auto">
            <div className="mb-1 text-center text-micro uppercase tracking-wider text-muted">{t('common.minute', 'daqiqa')}</div>
            {minutes.map((m) => (
              <button key={m} type="button"
                      onClick={() => setPart(hour, m)}
                      className={cn('block w-full rounded-icon py-1.5 text-center text-body', m === minute ? 'bg-ink text-white' : 'hover:bg-ink/5')}>
                {m}
              </button>
            ))}
          </div>
        </div>,
        document.body,
      )}
    </div>
  );
}
