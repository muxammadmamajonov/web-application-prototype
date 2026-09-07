import { useTranslation } from 'react-i18next';
import { useLocation, useNavigate } from 'react-router';
import { routeTitleKeyFor } from '@/lib/routeTitle';
import { isDrillDownPath } from '@/lib/nav';
import { isInsideTelegramClient } from '@/lib/telegram';
import { useTelegramBackButton } from '@/hooks/useTelegramBackButton';

interface Props {
  /** Every top-level destination for THIS shell (full paths). A path that shares
   *  the persona base but isn't one of these is a drill-down subpage → native
   *  Back; the roots themselves keep TG's "Close". */
  navRoots: string[];
  /** Optional explicit title override (e.g. a detail page that wants the entity
   *  name). Falls back to the route-derived title. */
  title?: string;
}

/**
 * The Mini-App's own header strip — shown ONLY inside Telegram, where TG draws
 * its floating Close (top-left) and Minimize + ⋮ (top-right) but leaves the
 * CENTER empty. We fill that centre with the current page title so it reads like
 * a native nav bar (cf. the reference food app), and we hand back-navigation to
 * Telegram's native BackButton on drill-down subpages.
 *
 * Outside Telegram this renders nothing — the browser shells keep their logo
 * bar — but the back hook is a safe no-op there, so it's always mounted.
 *
 * CHROME, NOT A HEADING (COR-01). This strip is mounted by both shells, and the
 * routed page inside <main> already titles itself — `PageHeader` emits an <h1>,
 * and the customer pages emit their own. While this title was an <h1> too,
 * every Mini-App screen in the product served TWO competing top-level headings
 * for one page. It is now a plain element: `role="heading"`/`aria-level` would
 * have preserved exactly the duplicate semantics under a different tag.
 *
 * `aria-hidden` is correct here rather than merely convenient: the string is
 * derived from the same `routeTitleKeyFor()` the page heading uses, so it is a
 * verbatim repeat that would announce the page title twice; the strip holds no
 * interactive or focusable content, so nothing is hidden from keyboard or AT
 * users; and Telegram's real nav chrome lives outside this document anyway. A
 * caller passing an explicit `title` must therefore keep the routed page's <h1>
 * authoritative — this strip is a visual echo of it, never the only copy.
 */
export function MiniAppHeader({ navRoots, title }: Props) {
  const { t } = useTranslation();
  const { pathname } = useLocation();
  const navigate = useNavigate();

  const isSubpage = isDrillDownPath(pathname, navRoots);
  // On a drill-down, Telegram's native Back walks the SPA history; on a top-level
  // tab the button is hidden so TG shows "Close" (exit the Mini-App) instead.
  useTelegramBackButton(isSubpage, () => navigate(-1));

  if (!isInsideTelegramClient()) return null;

  const key = routeTitleKeyFor(pathname);
  const heading = title ?? (key ? t(key) : '');

  return (
    <header
      aria-hidden="true"
      className="sticky top-0 z-40 flex items-center justify-center border-b border-line bg-cream/90 backdrop-blur md:hidden"
      style={{ paddingTop: 'var(--tg-safe-top)' }}
    >
      {/* Left/right gutters clear TG's floating corner controls so a long title
          truncates in the centre instead of sliding under the Close / ⋮ pills. */}
      <div className="flex h-12 w-full items-center justify-center px-[84px]">
        <div className="truncate text-center text-section font-semibold tracking-tight text-ink">
          {heading}
        </div>
      </div>
    </header>
  );
}
