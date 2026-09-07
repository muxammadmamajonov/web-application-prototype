import { Logo } from './Logo';
import { isInsideTelegramClient } from '@/lib/telegram';

/**
 * Mobile topbar — desktop sidebar already shows the logo & search.
 *
 * Inside the Telegram WebApp we render NOTHING here: Telegram already provides
 * its own header bar with the Close (top-left) and Minimize + ⋮ (top-right)
 * controls, so our logo strip would just be a redundant second bar stealing the
 * scarcest space. The required top clearance for those floating buttons is
 * applied as padding on <main> in AppShell instead. Outside Telegram (plain
 * browser) we show a centered logo so nothing sits in the corners.
 *
 * Notification bell removed deliberately: every alert in Muddato (payment
 * received, installment due, broadcast, etc.) is delivered straight into the
 * user's Telegram chat with @MuddatoBot — that's the platform's whole point.
 */
export function TopBar() {
  if (isInsideTelegramClient()) return null;
  return (
    <header
      className="sticky top-0 z-30 flex items-center justify-center border-b border-line bg-cream/95 px-4 backdrop-blur md:hidden"
      style={{
        paddingTop: 'max(0.25rem, env(safe-area-inset-top))',
        minHeight: 'calc(3.5rem + env(safe-area-inset-top))',
      }}
    >
      <Logo size={24} />
    </header>
  );
}

/**
 * Right-side action cluster for per-page actions on desktop.
 *
 * Logout is intentionally NOT here — it lives only on the Profile page, so the
 * navbar doesn't duplicate it (#60).
 */
export function DesktopActionBar({ children }: { children?: React.ReactNode }) {
  return <div className="flex items-center gap-2">{children}</div>;
}
