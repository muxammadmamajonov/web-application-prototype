import type { ReactNode } from 'react';
import { useTranslation } from 'react-i18next';
import { useLocation, useNavigate, useSearchParams } from 'react-router';
import { ChevronRight, type LucideIcon } from 'lucide-react';
import { PageHeader } from '@/components/ui/PageHeader';
import { SkeletonList } from '@/components/ui/Skeleton';
import { MobileHeader } from '@/components/mobile/primitives';
import { DesktopActionBar } from '@/components/layout/TopBar';
import { ErrorCard } from '@/components/ui/ErrorCard';
import { cn } from '@/lib/cn';

export interface SettingsTab {
  key: string;
  label: string;
  sub: string;
  icon: LucideIcon;
  /** (#448) Section header this tab clusters under (consecutive tabs sharing a
   *  group render below one header). Omit for a flat list. */
  group?: string;
  /** (#448) Marks a not-yet-functional stub — renders a "Tez orada" badge so
   *  operators don't open a dead end expecting configuration. */
  soon?: boolean;
}

interface Props {
  /** Root page title — shown in the header and as the breadcrumb root. */
  title: string;
  tabs: SettingsTab[];
  /** Render the body for the active tab. Only called once a tab is resolved. */
  children: (activeKey: string) => ReactNode;
  /** Show a loading card in the detail pane (the rail/list stay visible). */
  loading?: boolean;
  /**
   * The load failure, if any. Truthy ⇒ the detail pane shows an error+retry card
   * instead of the body.
   *
   * The failure itself, not a boolean — the card distinguishes offline from a
   * server fault from a permission refusal. Truthy ⇒ the detail pane shows an
   * error+retry card, so a failed settings load is never mistaken for a
   * still-loading or blank form. */
  error?: unknown;
  /** Retry callback for the error card. */
  onRetry?: () => void;
  /** Optional block above the rail (desktop) / above the menu list (mobile) —
   *  e.g. a profile summary card. */
  railTop?: ReactNode;
  /** Optional block below the rail (desktop) / below the menu list (mobile) —
   *  e.g. a logout button. */
  footer?: ReactNode;
}

/**
 * Shared Settings chrome — URL-addressable master/detail.
 *
 * The selected sub-page lives in the `?tab=` query param, so it survives a
 * refresh, is deep-linkable, and the browser Back button steps out of a
 * sub-page (mobile) / between sub-pages (desktop) for free.
 *
 *   ≥ md  → persistent two-pane: a tab rail (left) beside the detail (right),
 *           with a "Title / Sub-page" breadcrumb above the detail. The rail
 *           always shows a selection, so the page is never a blank pane.
 *   < md  → drill-in: tap a row to open it; the header Back chevron returns to
 *           the list.
 *
 * The rail and the mobile list render regardless of data state, so the page is
 * never blank even while the settings bundle is still loading or errored — only
 * the detail pane shows the loading card.
 */
export function SettingsShell({ title, tabs, children, loading, error, onRetry, railTop, footer }: Props) {
  const { t } = useTranslation();
  const [params, setParams] = useSearchParams();

  const raw = params.get('tab');
  // Ignore an unknown ?tab= value (stale link, typo) — fall back to the list.
  const selected = tabs.some((tt) => tt.key === raw) ? raw! : null;

  const navigate = useNavigate();
  const location = useLocation();
  /** Set on the entry this shell pushed, so leaving it can POP instead of push. */
  const OWNED = 'settingsDetail';
  const ownsEntry = !!(location.state as { [OWNED]?: boolean } | null)?.[OWNED];

  /**
   * Move between the list and a sub-page, with history that can be walked BACK.
   *
   * THE BUG THIS FIXES (production, 2026-08-11: "Main Settings reopens on the
   * second Back"). Every transition used to be a push — including the one the
   * back affordance itself performs. So leaving a sub-page did not undo entering
   * it, it appended a third entry:
   *
   *     /settings            (A)
   *     /settings?tab=x      (B)   ← opened a tab
   *     /settings            (C)   ← pressed Back; a PUSH, not a pop
   *
   * Inside Telegram this became a trap rather than merely untidy. Two owners sit
   * on the native BackButton stack: `MiniAppHeader` (`navigate(-1)`) and, above
   * it, `MobileHeader`'s `onBack` for the open sub-page. Pressing Back ran the
   * top one and landed on C; the sub-page unmounted, its handler popped, so the
   * next press ran `navigate(-1)` — straight back to B, the sub-page again.
   * Press again and the cycle repeats, forever, between the same two URLs.
   *
   * The rule is therefore about which transition each move IS, not about who
   * asked for it:
   *
   *   list → tab   a navigation into a sub-page   → push (stamped as ours)
   *   tab  → tab   a lateral move within one      → replace (no new entry: five
   *                                                 tab taps must not cost five
   *                                                 Back presses to undo)
   *   tab  → list  the inverse of the push        → pop, when the entry is ours
   *
   * A deep link straight to `?tab=x` was pushed by nobody here, so its entry is
   * not ours to pop — popping would leave the app entirely, past the list the
   * user has not seen yet. That case replaces, which lands on the list and keeps
   * Back meaning "the screen before Settings".
   */
  const open = (key: string | null) => {
    if (key === null && ownsEntry) { navigate(-1); return; }
    const next = new URLSearchParams(params);
    if (key) next.set('tab', key);
    else next.delete('tab');
    const entering = key !== null && selected === null;
    setParams(next, entering
      ? { replace: false, state: { ...(location.state as object | null), [OWNED]: true } }
      : { replace: true, state: location.state });
  };

  // Desktop always shows a detail pane (master-detail), so default to the
  // first tab when nothing is selected yet.
  const desktopKey = selected ?? tabs[0]?.key ?? '';
  const current = tabs.find((tt) => tt.key === selected);

  const detail = (key: string) =>
    loading
      ? <div className="card p-6"><SkeletonList rows={4} /></div>
      : error
        ? <ErrorCard error={error} onRetry={onRetry} />
        : children(key);

  // (#448) Cluster tabs in declaration order: consecutive tabs sharing a group
  // sit under one header; ungrouped tabs form unlabeled clusters, so pages
  // that pass no `group` metadata render exactly as before.
  const clusters: { group?: string; items: SettingsTab[] }[] = [];
  for (const tab of tabs) {
    const last = clusters[clusters.length - 1];
    if (last && last.group === tab.group) last.items.push(tab);
    else clusters.push({ group: tab.group, items: [tab] });
  }

  const soonBadge = (tt: SettingsTab) => tt.soon && (
    <span className="shrink-0 rounded-pill bg-line px-1.5 py-0.5 text-micro font-medium text-ink-soft">
      {t('common.soon', 'Tez orada')}
    </span>
  );

  return (
    <>
      {/* Desktop header — root title only; the rail carries the sub-page
          context (the active row is highlighted and marked aria-current), so
          the H1 stays stable and nothing restates it. */}
      <div className="hidden md:block">
        <PageHeader title={title} actions={<DesktopActionBar />} />
      </div>
      {/* Mobile header — sub-page title with a Back chevron; root when listing. */}
      <div className="md:hidden">
        <MobileHeader
          title={current ? current.label : title}
          onBack={current ? () => open(null) : undefined}
        />
      </div>

      {/* ── Desktop: persistent two-pane master-detail ─────────────────── */}
      <div className="hidden gap-6 md:grid md:grid-cols-[264px_minmax(0,1fr)]">
        <div className="sticky top-6 flex h-max flex-col gap-4">
        {railTop}
        <nav aria-label={title} className="card overflow-hidden p-0">
          {clusters.map((c, ci) => (
            <div key={ci} className={ci > 0 ? 'border-t border-line' : undefined}>
              {c.group && (
                <div className="bg-cream/60 px-4 pb-1.5 pt-2.5 text-micro font-medium uppercase tracking-wider text-muted">{c.group}</div>
              )}
              <ul className="divide-y divide-line">
                {c.items.map((tt) => {
                  const active = tt.key === desktopKey;
                  return (
                    <li key={tt.key}>
                      <button
                        onClick={() => open(tt.key)}
                        aria-current={active ? 'page' : undefined}
                        className={cn(
                          'flex w-full items-center gap-3 px-4 py-3 text-left transition',
                          active ? 'bg-[color:var(--c-accent-soft)]' : 'hover:bg-ink/5',
                        )}
                      >
                        <span className={cn(
                          'grid h-9 w-9 shrink-0 place-items-center rounded-control',
                          active ? 'bg-accent text-white' : 'bg-[color:var(--c-accent-soft)] text-accent',
                        )}>
                          <tt.icon size={16} />
                        </span>
                        <span className="min-w-0 flex-1">
                          <span className="flex items-center gap-1.5">
                            <span className={cn('truncate text-body', active ? 'font-semibold text-ink' : 'font-medium')}>{tt.label}</span>
                            {soonBadge(tt)}
                          </span>
                          <span className="block truncate text-caption text-muted">{tt.sub}</span>
                        </span>
                      </button>
                    </li>
                  );
                })}
              </ul>
            </div>
          ))}
        </nav>
        {footer}
        </div>

        <div className="min-w-0">
          {detail(desktopKey)}
        </div>
      </div>

      {/* ── Mobile: drill-in list → detail ─────────────────────────────── */}
      <div className="md:hidden">
        {!selected && (
          <div className="space-y-4">
            {railTop}
            <section className="card overflow-hidden p-0">
              {clusters.map((c, ci) => (
                <div key={ci} className={ci > 0 ? 'border-t border-line' : undefined}>
                  {c.group && (
                    <div className="bg-cream/60 px-5 pb-1.5 pt-2.5 text-micro font-medium uppercase tracking-wider text-muted">{c.group}</div>
                  )}
                  <ul className="divide-y divide-line">
                    {c.items.map((tt) => (
                      <li key={tt.key}>
                        <button onClick={() => open(tt.key)} className="flex w-full items-center gap-3 px-5 py-4 text-left hover:bg-ink/5">
                          <span className="grid h-9 w-9 place-items-center rounded-control bg-[color:var(--c-accent-soft)] text-accent"><tt.icon size={16} /></span>
                          <span className="min-w-0 flex-1">
                            <span className="flex items-center gap-1.5">
                              <span className="truncate text-body font-medium">{tt.label}</span>
                              {soonBadge(tt)}
                            </span>
                            <span className="block truncate text-caption text-muted">{tt.sub}</span>
                          </span>
                          <ChevronRight size={16} className="text-muted" />
                        </button>
                      </li>
                    ))}
                  </ul>
                </div>
              ))}
            </section>
            {footer}
          </div>
        )}
        {selected && detail(selected)}
      </div>
    </>
  );
}
