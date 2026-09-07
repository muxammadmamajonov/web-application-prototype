import { useEffect } from 'react';
import { Link, useLocation, useParams } from 'react-router';
import { buttonClasses } from '@/components/ui/buttonClasses';
import { Button } from '@/components/ui/Button';
import { isInsideTelegramClient } from '@/lib/telegram';
import { useSupportUsername } from '@/hooks/useSupportUsername';
import { logout } from '@/lib/auth';
import { useTranslation } from 'react-i18next';
import { AlertTriangle, Clock, LogOut } from 'lucide-react';
import { useMe } from '@/hooks/useMe';
import type { Locale } from '@/i18n';

function daysLeft(iso: string | null): number | null {
  if (!iso) return null;
  const end = new Date(iso).getTime();
  if (Number.isNaN(end)) return null;
  return Math.ceil((end - Date.now()) / 86_400_000);
}

/**
 * Trial lifecycle banner shown on every tenant page.
 *  - `demo`      → countdown ("X kun qoldi") with an upgrade link.
 *  - `suspended` → read-only notice (trial elapsed, choose a plan).
 * Blocked tenants are gated out before this renders. Returns null for
 * active/paid tenants.
 */
export function TrialBanner() {
  const { t, i18n } = useTranslation();
  const locale = (useParams<{ locale: Locale }>().locale ?? i18n.language) as Locale;
  const { data } = useMe();
  const tenant = data?.tenants?.[0];
  if (!tenant) return null;

  if (tenant.status === 'suspended') {
    return (
      <div className="mb-4 flex flex-col gap-2 rounded-xl border border-danger/30 bg-[color:var(--c-danger-soft)] px-4 py-3 text-sm text-danger sm:flex-row sm:items-center sm:justify-between">
        <span className="inline-flex items-center gap-2">
          <AlertTriangle size={16} className="shrink-0" />
          {t('trial.suspended', "Sinov muddati tugadi. Hisob faqat o'qish rejimida — davom etish uchun tarif tanlang.")}
        </span>
        <Link
          to={`/${locale}/tenant/billing`}
          className="inline-flex shrink-0 items-center justify-center rounded-lg bg-danger px-3 py-1.5 text-small font-medium text-white hover:brightness-110"
        >
          {t('trial.choosePlan', 'Tarif tanlash')}
        </Link>
      </div>
    );
  }

  if (tenant.status === 'demo') {
    const d = daysLeft(tenant.trialEndsAt);
    if (d === null) return null;
    const urgent = d <= 3;
    return (
      <div
        className={
          'mb-4 flex flex-col gap-2 rounded-xl border px-4 py-3 text-sm sm:flex-row sm:items-center sm:justify-between ' +
          (urgent
            ? 'border-warn/40 bg-[color:var(--c-warn-soft)] text-warn'
            : 'border-line bg-surface text-ink-soft')
        }
      >
        <span className="inline-flex items-center gap-2">
          <Clock size={16} className="shrink-0" />
          {d > 0
            ? t('trial.daysLeft', { defaultValue: 'Sinov muddati: {{count}} kun qoldi', count: d })
            : t('trial.endsToday', 'Sinov muddati bugun tugaydi')}
        </span>
        <Link
          to={`/${locale}/tenant/billing`}
          className="inline-flex shrink-0 items-center justify-center rounded-lg bg-ink px-3 py-1.5 text-small font-medium text-white hover:brightness-110"
        >
          {t('trial.upgrade', 'Tarifni faollashtirish')}
        </Link>
      </div>
    );
  }

  return null;
}

/** Full-screen gate for `blocked` tenants — no dashboard access. */
/**
 * Shown in place of the tenant app when the account is blocked.
 *
 * A blocked owner needs three things, and all three must be stated: WHY they are
 * blocked (an unpaid balance — not an expired trial, which this product does not
 * assume), the action that clears it with a way to take it, and a way to LEAVE.
 *
 * The way out matters because sign-out lives only on `ProfilePage`, and
 * `/tenant/profile` renders this gate while blocked — so a blocked user who
 * cannot or will not pay was locked inside their own session. The escape belongs
 * here rather than in a widened route table: billing stays the only ordinary
 * tenant page routable while blocked.
 *
 * This gate REPLACES the routed page inside `<main>`, so its title is that
 * surface's page heading and must be the `h1`, not an `h2`.
 */
export function BlockedGate() {
  const { t, i18n } = useTranslation();
  const support = useSupportUsername();
  const lng = i18n.language?.startsWith('ru') ? 'ru' : 'uz';
  // The per-route title manager names the path, but while the gate is up the
  // path is not what is on screen — the destination behind it never rendered.
  // Re-applied on every pathname change, because the gate stays mounted across
  // blocked navigations and would otherwise keep a title set once on entry.
  const { pathname } = useLocation();
  useEffect(() => {
    document.title = `${t('trial.blockedTitle', 'Hisob bloklangan')} · Muddato`;
  }, [t, pathname]);
  return (
    <div role="alert" aria-live="assertive" className="card p-10 text-center">
      <AlertTriangle size={32} className="mx-auto text-danger" />
      <h1 className="mt-4 h-page">{t('trial.blockedTitle', 'Hisob bloklangan')}</h1>
      <p className="mx-auto mt-2 max-w-md text-body text-ink-soft">
        {t('trial.blockedCause', "Hisobingiz to'lanmagani uchun vaqtincha to'xtatildi. Ma'lumotlaringiz saqlanib turibdi.")}
      </p>
      <p className="mx-auto mt-2 max-w-md text-body font-medium text-ink">
        {t('trial.blockedFix', "Ochish uchun: balansni to'ldiring.")}
      </p>
      {/* Every control here is a recovery path for someone who is almost
          certainly on a phone. They get the 44px coarse-pointer minimum from
          `buttonClasses` itself now (Batch 6) rather than from a class repeated
          at each call — the per-call version was the reason a control nobody
          remembered to adapt shipped at 40px on a phone. */}
      <div className="mt-5 flex flex-wrap items-center justify-center gap-2">
        {/* These navigate, so they are links styled like buttons: a <Button>
            inside a <Link> nests a button in a link, which is invalid and
            exposes two controls for one action. */}
        <Link to={`/${lng}/tenant/billing`}
              className={buttonClasses({ variant: 'primary' })}>
          {t('topup.title', "Balansni to'ldirish")}
        </Link>
        <a href={`https://t.me/${support}`} target="_blank" rel="noreferrer"
           className={buttonClasses({ variant: 'outline' })}>
          {t('billing.contactSupport', "Qo'llab-quvvatlash bilan bog'lanish")}
        </a>
        {/* The way out. Same control, same icon and the SAME policy as the one
            on ProfilePage — including hiding it inside Telegram, where
            /auth/login auto-logs-in from initData, so signing out would bounce
            straight back to the same account. There the escape is Telegram's
            own Close plus the support contact above; this is not a second auth
            implementation, it is the existing `logout()` primitive. */}
        {!isInsideTelegramClient() && (
          <Button variant="outline" onClick={() => logout()}>
            <LogOut size={16} /> {t('common.logout')}
          </Button>
        )}
      </div>
    </div>
  );
}
