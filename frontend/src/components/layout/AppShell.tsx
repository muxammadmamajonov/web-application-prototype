import type { ReactNode } from 'react';
import { useTranslation } from 'react-i18next';
import { useNavigate } from 'react-router';
import { LogOut } from 'lucide-react';
import { Sidebar, type SidebarItem } from './Sidebar';
import { SkipLink } from '@/components/a11y/SkipLink';
import { MobileBottomNav, type BottomNavItem } from './MobileBottomNav';
import { CommandPalette, type Command } from './CommandPalette';
import { TopBar } from './TopBar';
import { MiniAppHeader } from './MiniAppHeader';
import { isInsideTelegramClient } from '@/lib/telegram';
import { useSettingsButton } from '@/lib/telegram/buttons';
import { requestWriteAccessOnce } from '@/lib/telegram/permissions';
import { useEffect } from 'react';
import { useSession } from '@/store/session';

interface Props {
  sidebarItems: SidebarItem[];
  bottomNavItems: BottomNavItem[];
  /** Route for the mobile "Menyu" hub (the bottom nav's 5th tab). When set, the
   *  tab is route-backed so its active state derives from the URL. */
  menuPath?: string;
  contextCard?: ReactNode;
  children: ReactNode;
}

/**
 * Banner shown when a platform admin is viewing a tenant's dashboard.
 *
 * An operator inside a tenant is looking at another business's real data, so
 * which business it is must never be ambiguous: this announces itself on entry
 * (`role="status"` + `aria-live`), always names the tenant, and states that
 * actions are recorded. Exit returns to the tenant list.
 */
function ImpersonationBanner() {
  const { t, i18n } = useTranslation();
  const nav = useNavigate();
  const { impersonating, impersonatingName, exitTenant } = useSession();
  if (!impersonating) return null;
  const lng = i18n.language?.startsWith('ru') ? 'ru' : 'uz';
  const name = impersonatingName ?? t('impersonation.unknownTenant', 'Do‘kon');
  return (
    <div role="status" aria-live="polite"
         className="sticky top-0 z-30 flex items-center justify-between gap-3 bg-ink px-4 py-2 text-small text-white md:px-10">
      <span className="min-w-0 truncate">
        <span className="font-semibold">
          {t('impersonation.viewing', { name, defaultValue: 'Ko‘rilmoqda: {{name}}' })}
        </span>
        <span className="ml-2 hidden text-white/70 sm:inline">
          {t('impersonation.audited', 'Amallaringiz audit jurnaliga yoziladi')}
        </span>
      </span>
      <button onClick={() => { exitTenant(); nav(`/${lng}/platform/tenants`); }}
              aria-label={t('impersonation.exitNamed', { name, defaultValue: '{{name}} do‘konidan chiqish' })}
              className="tap-target inline-flex h-11 shrink-0 items-center gap-1.5 rounded-pill bg-white/15 px-3 font-medium hover:bg-white/25">
        <LogOut size={14} /> {t('impersonation.exit', 'Chiqish')}
      </button>
    </div>
  );
}

/**
 * Responsive shell:
 *  • ≥ md  → sidebar (280px) + main
 *  • <  md → topbar + main + bottom nav
 */
export function AppShell({ sidebarItems, bottomNavItems, menuPath, contextCard, children }: Props) {
  const insideTg = isInsideTelegramClient();
  // Inside Telegram the sticky <MiniAppHeader> already occupies the top strip
  // (safe-area inset + the title bar), so <main> only needs a little breathing
  // room. Outside Telegram we still clear the floating-control area.
  const mainPaddingTop = insideTg
    ? '1rem'
    : 'max(1.5rem, calc(env(safe-area-inset-top) + 0.5rem))';

  // (Cmd/Ctrl+K) Turn every unlocked nav destination into a jump-to command.
  const navigate = useNavigate();

  // Telegram-native niceties: the header ⚙️ jumps to Profile; ask once for the
  // bot's write permission so reminders/status updates deliver.
  const profileTo = sidebarItems.find((i) => i.key === 'profile')?.to;
  useSettingsButton(() => { if (profileTo) navigate(profileTo); });
  useEffect(() => { requestWriteAccessOnce(); }, []);

  const commands: Command[] = sidebarItems
    .filter((i) => !i.locked)
    .map((i) => ({ key: i.key, label: i.label, hint: i.section, icon: i.icon, keywords: i.to, perform: () => navigate(i.to) }));

  return (
    <div className="flex min-h-screen w-full bg-cream">
      <SkipLink />
      <CommandPalette commands={commands} />
      <Sidebar items={sidebarItems} contextCard={contextCard} />

      <div className="flex min-w-0 flex-1 flex-col">
        {/* Nav roots = the BOTTOM BAR's tabs + Menu, deliberately NOT the whole
            sidebar. `MiniAppHeader` renders only inside Telegram, and there the
            only top-level destinations are the four quick tabs and the Menu hub.

            Passing every sidebar item made each of them a "root", so opening
            Menu → Profil / Sozlamalar / Adminlar showed Telegram's "Close"
            instead of Back: one tap and the user was out of the app entirely,
            with no way back to the menu they came from. Those are reached
            THROUGH the Menu, so they are subpages and get Back.

            This is per-persona for free — every persona's quick tabs come from
            `QUICK_KEYS`, so client, staff, tenant and platform each get Back on
            exactly the destinations that sit behind their own Menu. */}
        <MiniAppHeader navRoots={[...bottomNavItems.map((i) => i.to), ...(menuPath ? [menuPath] : [])]} />
        <ImpersonationBanner />
        <TopBar />
        <main
          id="main"
          tabIndex={-1}
          className="flex-1 px-4 pb-24 pt-6 md:px-10 md:pb-10 md:pt-10 focus:outline-none"
          style={{ paddingTop: mainPaddingTop }}
        >
          <div className="mx-auto w-full max-w-content">{children}</div>
        </main>
      </div>

      <MobileBottomNav items={bottomNavItems} allItems={sidebarItems} menuPath={menuPath} />
    </div>
  );
}
