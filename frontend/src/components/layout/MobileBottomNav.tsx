import { NavLink, useLocation } from 'react-router';
import { Menu, type LucideIcon } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import { cn } from '@/lib/cn';
import { isSubpagePath } from '@/lib/nav';
import { haptics } from '@/lib/telegram/haptics';
import { useKeyboardOpen } from '@/hooks/useKeyboardOpen';
import { useBottomObstruction } from '@/hooks/useBottomObstruction';
import type { SidebarItem } from './Sidebar';

export interface BottomNavItem {
  key: string;
  to: string;
  label: string;
  icon: LucideIcon;
  /** (#424) Alternate path that should also highlight this tab — e.g. the
   *  client Home tab targets /me/home (Welcome decides the landing) but a
   *  returning customer lives on /me/my after its redirect. */
  activeAlias?: string;
}

interface Props {
  /** The ≤4 quick-access tabs shown directly in the bar. */
  items: BottomNavItem[];
  /** The full persona nav — used only to recognise top-level roots so the bar
   *  hides on genuine drill-down subpages (never to render a second nav). */
  allItems?: SidebarItem[];
  /** Route for the "Menyu" hub — the 5th tab. When set, the tab is a real
   *  NavLink, so its active state is derived from the URL (no stale highlight). */
  menuPath?: string;
  /** Show on all widths (not just mobile) — used by the client mini-app frame,
   *  which has no desktop sidebar. Bar is centered on wide screens. */
  alwaysShow?: boolean;
}

/** One tab — identical structure for the quick tabs and the Menu tab, so all
 *  five are pixel-aligned. Active state comes from NavLink (URL-derived). */
function Tab({ to, label, icon: Icon, end, ariaCurrentAlias }: {
  to: string; label: string; icon: LucideIcon; end?: boolean; ariaCurrentAlias?: boolean;
}) {
  return (
    <NavLink
      to={to}
      end={end}
      onClick={() => haptics.selection()}
      // min-w-0 lets the column shrink to its share of the bar. The label wraps
      // rather than truncating; `.nav-label` reserves two lines so tabs stay the
      // same height whether or not a label needs the second one.
      className={({ isActive }) => cn(
        'flex min-w-0 flex-col items-center gap-1 py-0.5 font-medium tap-target',
        isActive || ariaCurrentAlias ? 'text-[color:var(--c-accent-strong)]' : 'text-muted',
      )}
    >
      {({ isActive }) => (
        <>
          <span className={cn(
            'grid h-8 w-12 max-w-full place-items-center rounded-icon transition',
            isActive || ariaCurrentAlias
              ? 'bg-[color:var(--c-accent-soft)] text-[color:var(--c-accent-strong)]'
              : 'bg-transparent',
          )}>
            <Icon size={18} />
          </span>
          <span className="nav-label w-full">{label}</span>
        </>
      )}
    </NavLink>
  );
}

/**
 * Mobile bottom-bar — up to 4 quick tabs plus a route-backed "Menyu" tab that
 * opens the navigation hub (every persona page is reachable there). Because the
 * Menu tab is a NavLink to a real route, the active tab is always derived from
 * the URL — opening Menu highlights Menu, never the page underneath it.
 */
export function MobileBottomNav({ items, allItems, menuPath, alwaysShow }: Props) {
  const { t } = useTranslation();
  const { pathname } = useLocation();
  // While the soft keyboard is up, the fixed bar floats over the content on iOS
  // and crowds the input on Android — hide it until typing finishes.
  const keyboardOpen = useKeyboardOpen();

  const quick = items.slice(0, 4);
  // The Menu hub lists every page not already a quick tab; show the tab only when
  // there's something there (and a route to point at) — e.g. the customer app,
  // with just Home + Profile, gets no Menu tab.
  const quickKeys = new Set(quick.map((i) => i.key));
  const hasMenuItems = (allItems ?? []).some((i) => !quickKeys.has(i.key));
  const showMenu = !!menuPath && hasMenuItems;

  // (#33) The tab bar belongs on top-level pages only. On drill-down subpages
  // (e.g. /me/installments/:id) hide it — the page's own Back is the nav. The
  // menu route is a top-level destination, so it counts as a root (bar stays).
  const roots = [...quick, ...(allItems ?? [])].map((i) => i.to);
  if (menuPath) roots.push(menuPath);
  const isSubpage = isSubpagePath(pathname, roots);
  // Declare how much of the bottom of the screen this bar covers, so a floating
  // panel (the calendar) can place itself in the space that is left rather than
  // underneath it. Above the early return: the hook must run every render, and
  // a bar that is not on screen correctly publishes nothing.
  const navRef = useBottomObstruction<HTMLElement>(!isSubpage && !keyboardOpen);
  if (isSubpage) return null;

  const cols = quick.length + (showMenu ? 1 : 0);

  return (
    <nav
      ref={navRef}
      className={cn(
        'fixed inset-x-0 bottom-0 z-50 mx-auto grid w-full max-w-panel gap-0.5 border-t border-line bg-surface/95 px-1 backdrop-blur pb-[max(8px,var(--tg-safe-bottom))] pt-2',
        !alwaysShow && 'md:hidden',
        keyboardOpen && 'hidden',
      )}
      style={{ gridTemplateColumns: `repeat(${cols}, minmax(0, 1fr))` }}
    >
      {quick.map(({ key, to, label, icon, activeAlias }) => (
        <Tab key={key} to={to} label={label} icon={icon} end ariaCurrentAlias={!!activeAlias && pathname === activeAlias} />
      ))}
      {showMenu && (
        <Tab to={menuPath!} label={t('common.menu', 'Menyu')} icon={Menu} end />
      )}
    </nav>
  );
}
