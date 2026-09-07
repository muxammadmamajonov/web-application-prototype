import { cn } from '@/lib/cn';

export interface StatusTabOption<T extends string> {
  value: T;
  label: string;
  /** Omitted when the real server count isn't known yet — the pill then
   *  shows no number rather than a misleading 0. */
  count?: number;
}

/**
 * A horizontally-scrolling row of status pills, first-class above a list —
 * the single place a status filter lives. Anything that also let status be
 * picked from inside a `FilterDrawer` would be a second source of truth for
 * the same state (MUDD-84): keep this the only control.
 */
export function StatusTabs<T extends string>({ value, onChange, options }: {
  value: T;
  onChange: (v: T) => void;
  options: StatusTabOption<T>[];
}) {
  return (
    <div role="tablist" className="mb-3 flex gap-1.5 overflow-x-auto pb-0.5">
      {options.map((o) => {
        const active = o.value === value;
        return (
          <button
            key={o.value}
            type="button"
            role="tab"
            aria-selected={active}
            onClick={() => onChange(o.value)}
            className={cn(
              'shrink-0 whitespace-nowrap rounded-pill border px-3 py-1.5 text-caption font-medium transition-colors',
              active
                ? 'border-ink bg-ink text-white'
                : 'border-line bg-surface text-ink-soft hover:border-ink/30 hover:text-ink',
            )}
          >
            {o.label}
            {o.count != null && (
              <span className={cn('ml-1.5 tabular-nums', active ? 'text-white/70' : 'text-muted')}>{o.count}</span>
            )}
          </button>
        );
      })}
    </div>
  );
}
