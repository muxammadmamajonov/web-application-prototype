import { Link, NavLink, useLocation, useParams } from 'react-router';
import { Lock, type LucideIcon } from 'lucide-react';
import { Logo } from './Logo';
import { useTranslation } from 'react-i18next';
import { cn } from '@/lib/cn';

export interface SidebarItem {
  key: string;
  to: string;
  label: string;
  icon: LucideIcon;
  locked?: boolean;
  /** Section label — a header is rendered above the first item of each group. */
  section?: string;
}

interface Props {
  items: SidebarItem[];
  /** Card just under the logo — typically the tenant switcher or role badge. */
  contextCard?: React.ReactNode;
}

/** Desktop / tablet sidebar. Hidden on `< md`; pair with `<MobileBottomNav>` for phones. */
export function Sidebar({ items, contextCard }: Props) {
  const { t } = useTranslation();
  const { locale } = useParams();
  const { pathname } = useLocation();

  // The logo inside a dashboard shell points to the persona's home (first nav
  // item). On the active page it's a no-op so it never bumps to the landing.
  const homeHref = items[0]?.to ?? `/${locale ?? 'uz'}`;
  const onHome = pathname === homeHref;

  return (
    <aside className="sticky top-0 hidden h-screen w-[280px] shrink-0 self-start flex-col overflow-y-auto border-r border-line bg-surface md:flex">
      <div className="px-6 pt-6 pb-4">
        {onHome ? (
          <Logo />
        ) : (
          <Link to={homeHref} aria-label={t('nav.home', 'Bosh sahifa')}><Logo /></Link>
        )}
      </div>

      {contextCard && <div className="px-3 pb-3">{contextCard}</div>}

      <nav className="flex-1 overflow-y-auto px-3 pb-4">
        <ul className="space-y-0.5">
          {items.map((it, i) => {
            const { key, to, label, icon: Icon, locked, section } = it;
            const showHeader = !!section && section !== items[i - 1]?.section;
            return (
            <li key={key}>
              {showHeader && <div className="kicker px-3 pb-1 pt-3 first:pt-0">{section}</div>}
              {locked ? (
                <div className="flex h-10 items-center gap-3 rounded-control px-3 text-body text-muted">
                  <Icon size={18} />
                  <span className="flex-1">{label}</span>
                  <Lock size={14} />
                </div>
              ) : (
                <NavLink
                  to={to}
                  end
                  className={({ isActive }) => cn(
                    'flex h-10 items-center gap-3 rounded-control px-3 text-body font-medium transition',
                    isActive
                      ? 'bg-ink text-white'
                      : 'text-ink-soft hover:bg-ink/5 hover:text-ink',
                  )}
                >
                  <Icon size={18} />
                  <span>{label}</span>
                </NavLink>
              )}
            </li>
            );
          })}
        </ul>
      </nav>

    </aside>
  );
}
