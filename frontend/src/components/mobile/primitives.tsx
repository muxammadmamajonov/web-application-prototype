import type { ReactNode } from 'react';
import { useTranslation } from 'react-i18next';
import { ArrowLeft } from 'lucide-react';
import { cn } from '@/lib/cn';
import { isInsideTelegramClient } from '@/lib/telegram';
import { useTelegramBackButton } from '@/hooks/useTelegramBackButton';

/**
 * Compact mobile page header — optional back button, title, and an optional
 * custom `action` node.
 *
 * It no longer hosts the create action. `onAdd`/`addLabel` used to render a
 * `PrimaryAction` pill here; the create action is now `FloatingCreateAction`,
 * which sits in the same place on every page and every breakpoint, so there is
 * nothing left for the header to host. `action` stays for the genuinely
 * page-specific controls that are NOT creates (e.g. Overview's review link).
 * `createActionPattern.test.ts` keeps the pill from creeping back in.
 */
export function MobileHeader({
  title, onBack, action,
}: {
  title: string; onBack?: () => void; action?: ReactNode;
}) {
  const { t } = useTranslation();

  // (Task 1) An `onBack` (e.g. Settings' ?tab= detail→list, which TG's URL-based
  // native Back can't see) drives Telegram's NATIVE Back via the shared stack —
  // no in-page back button inside Telegram. Inert/no-op outside Telegram.
  useTelegramBackButton(!!onBack, onBack ?? (() => {}));

  // Inside Telegram the centered <MiniAppHeader> shows the title and the native
  // Back (above) handles `onBack`, so we render ONLY the page's custom action.
  if (isInsideTelegramClient()) {
    if (!action) return null;
    return <div className="mb-3 flex justify-end">{action}</div>;
  }

  return (
    <div className="mb-3 flex items-center justify-between gap-3">
      <div className="flex min-w-0 items-center gap-2.5">
        {onBack && (
          <button onClick={onBack} className="grid h-9 w-9 tap-target shrink-0 place-items-center rounded-control text-ink-soft hover:bg-ink/5" aria-label={t('common.back', 'Orqaga')}>
            <ArrowLeft size={18} />
          </button>
        )}
        <div className="min-w-0">
          <div className="h-page truncate">{title}</div>
        </div>
      </div>
      {action}
    </div>
  );
}

/**
 * Top-of-page "← Back" chip for drill-down subpages. Inside Telegram it renders
 * NOTHING — TG's native BackButton (driven by MiniAppHeader) is the back
 * affordance there, so an in-page chip would be a redundant second back control
 * (exactly the duplication we're removing). Outside Telegram it's the normal
 * chip. Use for the page-top `nav(-1)` back; leave contextual in-card "back to X"
 * actions as plain buttons.
 */
export function BackChip({ onClick, label, className }: { onClick: () => void; label: string; className?: string }) {
  if (isInsideTelegramClient()) return null;
  return (
    <button
      onClick={onClick}
      aria-label={label}
      className={cn('tap-target mb-2 -ml-2 inline-flex min-h-11 items-center gap-1 rounded-icon px-2 py-2.5 text-caption text-muted hover:bg-ink/5', className)}
    >
      <ArrowLeft size={14} aria-hidden="true" /> {label}
    </button>
  );
}

type StatTone = 'default' | 'accent' | 'success' | 'warn' | 'danger';
const STAT_DOT: Record<StatTone, string> = {
  default: 'bg-success', accent: 'bg-accent', success: 'bg-success', warn: 'bg-warn', danger: 'bg-danger',
};
const STAT_ICON: Record<StatTone, string> = {
  default: 'bg-cream text-ink-soft',
  accent:  'bg-[color:var(--c-accent-soft)] text-accent',
  success: 'bg-success-soft text-[color:var(--c-success-strong)]',
  warn:    'bg-warn-soft text-[color:var(--c-warn-strong)]',
  danger:  'bg-danger-soft text-[color:var(--c-danger-strong)]',
};

export interface StatStripItem {
  key: string;
  label: string;
  value: number | string;
  tone?: StatTone;
  icon?: ReactNode;
  loading?: boolean;
}

/**
 * Compact horizontal metric strip for phone summaries — replaces the heavy 2×2
 * KPI grid that dominated mobile screens. Cards scroll-snap horizontally and
 * bleed to the page edges, so four metrics stay glanceable without eating half
 * the viewport. Pair with a `hidden md:grid` of full `KpiCard`s for ≥tablet.
 */
export function StatStrip({ items }: { items: StatStripItem[] }) {
  return (
    <div className="-mx-4 flex snap-x snap-mandatory gap-3 overflow-x-auto px-4 pb-1">
      {items.map(({ key, label, value, tone = 'default', icon, loading }) => (
        <div
          key={key}
          className="snap-start shrink-0 basis-[44%] min-[420px]:basis-[40%] rounded-card border border-line bg-surface p-3.5"
        >
          <div className="flex items-center justify-between gap-2">
            <span className="flex min-w-0 items-center gap-1.5">
              <span className={cn('h-1.5 w-1.5 shrink-0 rounded-pill', STAT_DOT[tone])} />
              <span className="truncate text-caption text-muted">{label}</span>
            </span>
            {icon && (
              <span className={cn('grid h-7 w-7 shrink-0 place-items-center rounded-icon', STAT_ICON[tone])}>{icon}</span>
            )}
          </div>
          {loading ? (
            <div className="mt-2 h-6 w-12 animate-pulse rounded-micro bg-ink/10" />
          ) : (
            // text-section (not 2xl) so a 7-digit money string stays on ≤2 lines in the
            // narrow tile while plain counts still read as prominent metric values.
            <div className="mt-1.5 text-section font-semibold tabular-nums leading-tight">{value}</div>
          )}
        </div>
      ))}
    </div>
  );
}

/** Small stat tile with a colored status dot — used in mobile dashboard grids. */
export function StatTile({
  dot, label, value, sub,
}: { dot: string; label: string; value: string; sub?: string }) {
  return (
    <div className="rounded-card border border-line bg-surface p-4">
      <div className="flex items-center gap-1.5">
        <span className={cn('h-1.5 w-1.5 rounded-pill', dot)} />
        <span className="text-micro text-muted">{label}</span>
      </div>
      <div className="mt-1.5 font-mono text-section font-semibold tabular-nums">{value}</div>
      {sub && <div className="mt-1 text-micro text-muted">{sub}</div>}
    </div>
  );
}

/**
 * Circular term-progress dial (paid / total). Matches the mockup's payment ring.
 * `light` renders white-on-translucent for use inside dark/accent hero cards.
 */
export function ProgressRing({
  paid, total, size = 56, stroke = 5, light = false,
}: { paid: number; total: number; size?: number; stroke?: number; light?: boolean }) {
  const radius = (size - stroke) / 2 - 1;
  const circ = 2 * Math.PI * radius;
  const pct = total > 0 ? Math.min(1, Math.max(0, paid / total)) : 0;
  const track = light ? 'rgba(255,255,255,0.22)' : 'var(--c-line, #e7e2da)';
  const fill = light ? '#ffffff' : 'var(--c-chart-1)';  // (#631) tokenized; chart-1 == accent
  return (
    <div className="relative shrink-0" style={{ width: size, height: size }}>
      <svg width={size} height={size} viewBox={`0 0 ${size} ${size}`}>
        <circle cx={size / 2} cy={size / 2} r={radius} fill="none" stroke={track} strokeWidth={stroke} />
        <circle
          cx={size / 2} cy={size / 2} r={radius} fill="none" stroke={fill}
          strokeWidth={stroke} strokeLinecap="round"
          strokeDasharray={`${circ * pct} ${circ}`}
          transform={`rotate(-90 ${size / 2} ${size / 2})`}
        />
      </svg>
      <div className="absolute inset-0 grid place-items-center text-micro font-semibold tabular-nums">
        {paid}/{total}
      </div>
    </div>
  );
}

/**
 * Segmented installment progress bar — one segment per period, filled up to `paid`.
 * Falls back to a single continuous bar when there are too many periods to show
 * individual segments cleanly.
 */
export function SegmentBar({
  paid, total, tone = 'ink',
}: { paid: number; total: number; tone?: 'ink' | 'danger' }) {
  const filled = tone === 'danger' ? 'bg-danger' : 'bg-ink';
  if (total > 18 || total <= 0) {
    const pct = total > 0 ? Math.min(100, (paid / total) * 100) : 0;
    return (
      <div className="h-1.5 w-full overflow-hidden rounded-pill bg-ink/10">
        <div className={cn('h-full rounded-pill', filled)} style={{ width: `${pct}%` }} />
      </div>
    );
  }
  return (
    <div className="flex items-center gap-1">
      {Array.from({ length: total }).map((_, j) => (
        <div key={j} className={cn('h-1 flex-1 rounded-pill', j < paid ? filled : 'bg-ink/15')} />
      ))}
    </div>
  );
}
