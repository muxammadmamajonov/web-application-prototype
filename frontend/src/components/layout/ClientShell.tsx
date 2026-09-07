import { useEffect, type ReactNode } from 'react';
import { useNavigate } from 'react-router';
import { Logo } from './Logo';
import { MobileBottomNav, type BottomNavItem } from './MobileBottomNav';
import { MiniAppHeader } from './MiniAppHeader';
import { SkipLink } from '@/components/a11y/SkipLink';
import { isInsideTelegramClient } from '@/lib/telegram';
import { useSettingsButton } from '@/lib/telegram/buttons';
import { requestWriteAccessOnce } from '@/lib/telegram/permissions';

/**
 * Customer (mijoz) shell — a centered mobile-app frame, NOT the admin AppShell.
 * The client is a Telegram Mini-App: phone-first, no admin sidebar. On a wide
 * screen it presents as a centered column with a slim logo bar + bottom tabs,
 * so a customer never sees an admin console. Inside Telegram the logo bar is
 * hidden (TG draws its own header) and top clearance is reserved for the
 * floating Close / ⋮ buttons.
 */
export function ClientShell({ navItems, children }: { navItems: BottomNavItem[]; children: ReactNode }) {
  const insideTg = isInsideTelegramClient();
  // Top-level client destinations (full paths). The bottom nav only carries
  // home + profile, but `my` (returning-customer home) and `privacy` are also
  // top-level; everything else under the persona base (/me/installments/:id …)
  // is a drill-down that gets Telegram's native Back.
  const base = (navItems[0]?.to ?? '/uz/me').replace(/\/[^/]+$/, '');
  const navRoots = ['home', 'my', 'profile', 'privacy'].map((s) => `${base}/${s}`);

  // Telegram-native niceties: header ⚙️ → Profile; ask once for the bot's write
  // permission so payment reminders land in the user's chat.
  const navigate = useNavigate();
  useSettingsButton(() => navigate(`${base}/profile`));
  useEffect(() => { requestWriteAccessOnce(); }, []);

  return (
    <div className="flex min-h-screen flex-col bg-cream text-ink">
      <SkipLink />
      <MiniAppHeader navRoots={navRoots} />
      {!insideTg && (
        <header
          className="sticky top-0 z-30 flex items-center justify-center border-b border-line bg-surface/95 backdrop-blur"
          style={{
            paddingTop: 'max(0.25rem, env(safe-area-inset-top))',
            minHeight: 'calc(3.5rem + env(safe-area-inset-top))',
          }}
        >
          <Logo size={24} />
        </header>
      )}
      <main
        id="main"
        tabIndex={-1}
        className="flex-1 px-4 pb-28 pt-6 md:px-10 focus:outline-none"
        style={{ paddingTop: insideTg ? '1rem' : '1.5rem' }}
      >
        {/* (#5) The 448px `max-w-panel` cap was unconditional, so every /me page
            stayed phone-width on a desktop browser. That is right for the
            phone-shaped flows — and they keep it, because each declares its own
            `max-w-panel` inside this wrapper (Home, Pay, InstallmentDetail,
            ReceiptUpload, Privacy), so widening here does not touch them.

            It was wrong for Profile, which delegates to `SettingsShell`. That
            shell's two-pane grid switches on the VIEWPORT at md, not on its
            container, so at 768px it opened a 264px rail inside a 448px box and
            left the detail pane about 160px wide — and `md:grid-cols-2` then
            split THAT into two ~68px columns. Hence the overlapping labels and
            fields. Widening the cap at md is what makes the shell's own
            breakpoints mean what they say. */}
        <div className="mx-auto w-full max-w-panel md:max-w-content">{children}</div>
      </main>
      <MobileBottomNav items={navItems} alwaysShow />
    </div>
  );
}
