import { Link, Navigate, Outlet, Route, Routes, useLocation, useParams } from 'react-router';
import { useTranslation } from 'react-i18next';
import { Suspense, useEffect, useRef } from 'react';

import { lazyWithRetry } from '@/lib/lazyWithRetry';
import { AppShell } from '@/components/layout/AppShell';
import { ClientShell as ClientFrame } from '@/components/layout/ClientShell';
import { TrialBanner, BlockedGate } from '@/components/layout/TrialBanner';
import { LoadingSplash } from '@/components/ui/LoadingSplash';
import { RouteErrorBoundary } from '@/components/RouteErrorBoundary';
import { useMe } from '@/hooks/useMe';
import { hasAuthFlag, setSessionToken } from '@/lib/api';
import { toast } from '@/lib/feedback';
import { isInsideTelegramClient } from '@/lib/telegram';
import { useStartParamRedirect, captureReferralCode } from '@/lib/telegram/deepLinks';
import { routeTitleKeyFor, isLandingPath } from '@/lib/routeTitle';
import { sidebarFor, bottomNavFor, menuPathFor } from './_navConfig';
import { REPORTS_ENABLED, V2_SURFACES_ENABLED } from './_scope';
import { useSession } from '@/store/session';
import { personaFor, personaHome, isActiveTenantBlocked } from '@/store/persona';
import { SUPPORTED_LOCALES, type Locale } from '@/i18n';
import type { Persona } from '@/types';

// Eager — these are the critical paths the user lands on first; Suspense
// fallbacks on these would re-introduce the "Yuklanmoqda…" loop seen in
// the Telegram Mini-App in-browser webview.
import LoginPage from './auth/LoginPage';
import SignupPage from './auth/SignupPage';
import TelegramCallbackPage from './auth/TelegramCallbackPage';
const ReviewIntentPage = lazyWithRetry(() => import('./ReviewIntentPage'));
const PaymentReviewPage = lazyWithRetry(() => import('./tenant/PaymentReviewPage'));

// Marketing — fine to lazy-load (visitors are on Wi-Fi, not Telegram webview).
const LandingPage          = lazyWithRetry(() => import('./marketing/LandingPage'));
const PricingPage          = lazyWithRetry(() => import('./marketing/PricingPage'));
const FeaturesPage         = lazyWithRetry(() => import('./marketing/FeaturesPage'));
const DemoPage             = lazyWithRetry(() => import('./marketing/DemoPage'));
const ContactPage          = lazyWithRetry(() => import('./marketing/ContactPage'));
const CareersPage          = lazyWithRetry(() => import('./marketing/CareersPage'));
const RoadmapPage          = lazyWithRetry(() => import('./marketing/RoadmapPage'));
const TermsPage            = lazyWithRetry(() => import('./marketing/legal/TermsPage'));
const PrivacyPage          = lazyWithRetry(() => import('./marketing/legal/PrivacyPage'));
const RefundPage           = lazyWithRetry(() => import('./marketing/legal/RefundPage'));
const CookiesPage          = lazyWithRetry(() => import('./marketing/legal/CookiesPage'));

// Persona dashboards
const PlatformOverviewPage  = lazyWithRetry(() => import('./platform/OverviewPage'));
const PlatformTenantsPage   = lazyWithRetry(() => import('./platform/TenantsPage'));
const PlatformPaymentsPage  = lazyWithRetry(() => import('./platform/PaymentsPage'));
const PlatformPlansPage     = lazyWithRetry(() => import('./platform/PlansPage'));
const PlatformAdminsPage    = lazyWithRetry(() => import('./platform/AdminsPage'));
const PlatformAuditPage     = lazyWithRetry(() => import('./platform/AuditPage'));
const PlatformPromosPage    = lazyWithRetry(() => import('./platform/PromosPage'));
const PlatformAdsPage       = lazyWithRetry(() => import('./platform/AdsPage'));
const PlatformReferralsPage = lazyWithRetry(() => import('./platform/ReferralsPage'));
const PlatformSettingsPage  = lazyWithRetry(() => import('./platform/SettingsPage'));
const PlatformCatalogPage   = lazyWithRetry(() => import('./platform/CatalogPage'));
const PlatformUsersPage     = lazyWithRetry(() => import('./platform/UsersPage'));
const PlatformBroadcastsPage = lazyWithRetry(() => import('./platform/BroadcastsPage'));
const TenantOverviewPage    = lazyWithRetry(() => import('./tenant/OverviewPage'));
const TenantCustomersPage   = lazyWithRetry(() => import('./tenant/CustomersPage'));
const TenantInstallmentsPage= lazyWithRetry(() => import('./tenant/InstallmentsPage'));
const TenantPaymentsPage    = lazyWithRetry(() => import('./tenant/PaymentsPage'));
const TenantBroadcastsPage  = lazyWithRetry(() => import('./tenant/BroadcastsPage'));
const TenantSettingsPage    = lazyWithRetry(() => import('./tenant/SettingsPage'));
const TenantStaffPage       = lazyWithRetry(() => import('./tenant/StaffPage'));
const TenantReportsPage     = lazyWithRetry(() => import('./tenant/ReportsPage'));
const TenantBillingPage     = lazyWithRetry(() => import('./tenant/BillingPage'));
const TenantImportPage      = lazyWithRetry(() => import('./tenant/ImportPage'));
const JoinPage              = lazyWithRetry(() => import('./m/JoinPage'));
const ClientWelcomePage     = lazyWithRetry(() => import('./client/WelcomePage'));
const StaffTodayPage        = lazyWithRetry(() => import('./staff/TodayPage'));
const StaffCustomersPage    = lazyWithRetry(() => import('./staff/CustomersPage'));
const StaffSalesPage        = lazyWithRetry(() => import('./staff/SalesPage'));
const StaffPaymentsPage     = lazyWithRetry(() => import('./staff/PaymentsPage'));
const ClientHomePage        = lazyWithRetry(() => import('./client/HomePage'));
const ClientInstallmentDetailPage = lazyWithRetry(() => import('./client/InstallmentDetailPage'));
const ClientPayPage         = lazyWithRetry(() => import('./client/PayPage'));
const ClientReceiptUploadPage = lazyWithRetry(() => import('./client/ReceiptUploadPage'));
const ClientPayoffPage      = lazyWithRetry(() => import('./client/PayoffPage'));
const ClientPrivacyPage     = lazyWithRetry(() => import('./client/PrivacyPage'));
// Mobile "Menyu" hub — a real route so the bottom-nav Menu tab is highlighted from
// the URL (fixes the stale active-tab bug) and survives refresh / deep-link.
const MenuPage              = lazyWithRetry(() => import('./shared/MenuPage'));
const ProfilePage           = lazyWithRetry(() => import('./shared/ProfilePage'));

/** Inline loader for inner Suspense boundaries (e.g. lazy page swap inside
 *  an AppShell). The first-paint splash uses <LoadingSplash /> instead. */
const PageLoading = ({ label }: { label?: string } = {}) => (
  <div className="grid min-h-[40vh] place-items-center text-body text-muted">
    <span className="inline-flex items-center gap-2">
      <span className="h-2 w-2 animate-pulse rounded-pill bg-accent" />
      {label ?? '…'}
    </span>
  </div>
);

// ── Auth guard (no global bootstrap — never blocks the router) ─────────────

function RequireAuth() {
  const { i18n } = useTranslation();
  const locale = i18n.language?.startsWith('ru') ? 'ru' : 'uz';
  // Cookie-only auth: gate on the non-sensitive breadcrumb, not a JS-readable
  // token. The HttpOnly session cookie is the real credential; useMe confirms
  // it and a stale flag is cleared by the 401 path.
  const authed = hasAuthFlag();
  const meQuery = useMe();
  const location = useLocation();
  const activeTenantId = useSession((s) => s.activeTenantId);
  const impersonating = useSession((s) => s.impersonating);

  if (!authed) {
    return <Navigate
      to={`/auth/login?next=${encodeURIComponent(location.pathname)}`}
      replace
    />;
  }

  // Branded fullscreen splash instead of bouncing through /auth/login while
  // /auth/me is in-flight on the first dashboard hit.
  if (meQuery.isLoading) return <LoadingSplash />;

  // /me failed → the session is invalid. Clear the local authed breadcrumb
  // explicitly here (don't rely on the axios interceptor racing with React's
  // render) and send the user to /auth/login. LoginPage has no auto-redirect
  // any more, so this is a terminal state — no loop possible.
  if (meQuery.isError) {
    setSessionToken(null);
    return <Navigate to="/auth/login" replace />;
  }

  if (meQuery.data) {
    if (location.pathname === '/app' || location.pathname === `/${locale}/app`) {
      // Land via the SAME persona logic the per-route PersonaGuard uses, so the
      // /app redirect and the guards can never disagree (#58). personaFor falls
      // back to the user's first tenant when the persisted activeTenantId is
      // null/stale, so a fresh-login or just-provisioned-demo owner isn't bounced
      // to the non-client page.
      const persona = personaFor(meQuery.data, activeTenantId, impersonating);
      return <Navigate to={personaHome(persona, locale)} replace />;
    }
  }
  return <Outlet />;
}

// ── Persona gate ─────────────────────────────────────────────────────────────
// The backend enforces authorization (403s), but the SPA must not *render* a
// persona's control surface to the wrong user. Each privileged shell is wrapped
// so a user whose real role doesn't match the URL persona is redirected to
// their own home instead of seeing (and firing queries for) another role's UI.

/**
 * A staff session hitting the OWNER's contract screen
 * (`/tenant/installments?installment=<id>`, e.g. from a `tinst_<id>` deep
 * link forwarded into a shop channel staff also read) has a real destination
 * that shows the same contract: `/staff/installments` (`SalesPage`, wired to
 * the same `?installment=` query param as the tenant page). Bouncing to
 * `personaHome` — the generic fallback below — throws that destination away
 * even though it exists and is theirs to see. Same defect shape as MUDD-148,
 * one persona over.
 */
function staffInstallmentsRedirect(locale: string, pathname: string, search: string): string | null {
  const tenantInstallments = `/${locale}/tenant/installments`;
  return pathname.startsWith(tenantInstallments) ? `/${locale}/staff/installments${search}` : null;
}

function PersonaGuard({ allow, children }: { allow: Persona[]; children: React.ReactNode }) {
  const { i18n, t } = useTranslation();
  const locale = i18n.language?.startsWith('ru') ? 'ru' : 'uz';
  const { data: me } = useMe();
  const storeMe = useSession((s) => s.me);
  // On a cold reload (e.g. Telegram ⋮ → Reload) the zustand store lags
  // react-query by one effect tick. resolvePersona() would briefly see me=null,
  // return 'client', and bounce a real owner to the non-client page. Wait for
  // the store to catch up before resolving the persona. (#36)
  // Resolve the persona unconditionally — useSession is a hook and must run on
  // every render (Rules of Hooks), before any early return below.
  const persona = useSession((s) => s.resolvePersona());
  const { pathname, search } = useLocation();
  const denied = !allow.includes(persona);
  // Billing has no staff-role equivalent at all — unlike installments above,
  // there is nowhere honest to send them, so at least say so instead of
  // letting the bounce look like the tap did nothing.
  const deniedBilling = denied && persona === 'staff' && pathname === `/${locale}/tenant/billing`;
  useEffect(() => {
    if (deniedBilling) toast.info(t('billing.staffLinkOwnerOnly'));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [deniedBilling]);
  if (me && !storeMe) return <LoadingSplash />;
  if (denied) {
    const staffTarget = persona === 'staff' ? staffInstallmentsRedirect(locale, pathname, search) : null;
    return <Navigate to={staffTarget ?? personaHome(persona, locale)} replace />;
  }
  return <>{children}</>;
}

// ── Persona shells ──────────────────────────────────────────────────────────

function PlatformShell() {
  const { t, i18n } = useTranslation();
  const locale = (useParams<{ locale: Locale }>().locale ?? i18n.language) as Locale;
  return (
    <AppShell
      sidebarItems={sidebarFor('platform', locale, t)}
      bottomNavItems={bottomNavFor('platform', locale, t)}
      menuPath={menuPathFor('platform', locale)}
    >
      <RouteErrorBoundary>
        <Suspense fallback={<PageLoading />}>
          <Routes>
          {/* The operator's landing is the Dashboard — same as every other
              persona, and the order the sidebar has always shown. */}
          <Route index element={<Navigate to="overview" replace />} />
          <Route path="tenants"   element={<PlatformTenantsPage />} />
          <Route path="payments"  element={<PlatformPaymentsPage />} />
          <Route path="plans"     element={<PlatformPlansPage />} />
          <Route path="admins"    element={<PlatformAdminsPage />} />
          <Route path="audit"     element={<PlatformAuditPage />} />
          <Route path="settings"  element={<PlatformSettingsPage />} />
          <Route path="profile"   element={<ProfilePage />} />
          <Route path="menu"      element={<MenuPage persona="platform" />} />
          {/* The Dashboard is the operator's overview screen and is routed
              unconditionally. It was gated with the other deferred surfaces in
              the beta-candidate prep (0d9085a), but unlike them it is a
              finished page backed by live queries — restoring it is what the
              rest of this list is still waiting on. */}
          <Route path="overview"   element={<PlatformOverviewPage />} />
          {/* Deferred operator surfaces — unreachable, source retained.
              See src/routes/_scope.ts. */}
          {V2_SURFACES_ENABLED && <Route path="users"      element={<PlatformUsersPage />} />}
          {V2_SURFACES_ENABLED && <Route path="broadcasts" element={<PlatformBroadcastsPage />} />}
          {V2_SURFACES_ENABLED && <Route path="catalog"    element={<PlatformCatalogPage />} />}
          {V2_SURFACES_ENABLED && <Route path="ads"        element={<PlatformAdsPage />} />}
          {V2_SURFACES_ENABLED && <Route path="promos"     element={<PlatformPromosPage />} />}
          {V2_SURFACES_ENABLED && <Route path="referrals"  element={<PlatformReferralsPage />} />}
          <Route path="*" element={<Navigate to="overview" replace />} />
        </Routes>
        </Suspense>
      </RouteErrorBoundary>
    </AppShell>
  );
}

/** Exported so the blocked-gate routing can be tested against the real shell
 *  rather than a re-implementation of its branching. */
export function TenantShell() {
  const { t, i18n } = useTranslation();
  const locale = (useParams<{ locale: Locale }>().locale ?? i18n.language) as Locale;
  const { data: me } = useMe();
  // Blocking is per-tenant, so it must be read from the ACTIVE membership.
  // `tenants[0]` would gate a user working in a healthy shop because some other
  // shop they belong to is blocked, and would let a selected blocked shop
  // through whenever it was not first in the list.
  const activeTenantId = useSession((s) => s.activeTenantId);
  const blocked = isActiveTenantBlocked(me ?? null, activeTenantId ?? null);
  return (
    <AppShell
      // While blocked, every path but `billing` renders the gate below, so the
      // navigation is narrowed by the SAME fact rather than by a second copy of
      // the rule — including the Menu hub, whose own route is gated too.
      sidebarItems={sidebarFor('tenant', locale, t, { blocked })}
      bottomNavItems={bottomNavFor('tenant', locale, t, { blocked })}
      menuPath={blocked ? undefined : menuPathFor('tenant', locale)}
    >
      {/* The trial banner announces a trial the MVP does not assume. The
          blocked gate is retained — it states the cause and the fix. */}
      {V2_SURFACES_ENABLED && <TrialBanner />}
      {blocked ? (
        /*
         * A blocked shop must still reach the one thing that clears the block,
         * so billing stays routable and the gate's "top up" CTA is not a dead
         * link. Everything else shows the gate — no ordinary tenant work resumes
         * while blocked. Role rules are unaffected: BillingPage itself keeps
         * top-up owner-only and shows a manager balance and invoices read-only.
         */
        <RouteErrorBoundary>
          <Suspense fallback={<PageLoading />}>
            <Routes>
              <Route path="billing" element={<TenantBillingPage />} />
              <Route path="*" element={<BlockedGate />} />
            </Routes>
          </Suspense>
        </RouteErrorBoundary>
      ) : (
      <RouteErrorBoundary>
        <Suspense fallback={<PageLoading />}>
          <Routes>
          <Route index element={<Navigate to="overview" replace />} />
          <Route path="overview"     element={<TenantOverviewPage />} />
          <Route path="customers"    element={<TenantCustomersPage />} />
          <Route path="installments" element={<TenantInstallmentsPage />} />
          <Route path="payments"     element={<TenantPaymentsPage />} />
          {/* Broadcasts is deferred — source retained. */}
          {V2_SURFACES_ENABLED && <Route path="broadcasts" element={<TenantBroadcastsPage />} />}
          <Route path="staff"        element={<TenantStaffPage />} />
          {/* (#15) Reports is deferred — source and endpoints retained. With no
              route registered the wildcard below sends /tenant/reports (typed,
              bookmarked, or reached from a stale link) to Overview, which is
              where its numbers now live. */}
          {REPORTS_ENABLED && <Route path="reports" element={<TenantReportsPage />} />}
          <Route path="billing"      element={<TenantBillingPage />} />
          <Route path="import"       element={<TenantImportPage />} />
          <Route path="settings"     element={<TenantSettingsPage />} />
          <Route path="payments/:paymentId/review" element={<PaymentReviewPage />} />
          <Route path="profile"      element={<ProfilePage />} />
          <Route path="menu"         element={<MenuPage persona="tenant" />} />
          <Route path="*" element={<Navigate to="overview" replace />} />
        </Routes>
        </Suspense>
      </RouteErrorBoundary>
      )}
    </AppShell>
  );
}

function StaffShell() {
  const { t, i18n } = useTranslation();
  const locale = (useParams<{ locale: Locale }>().locale ?? i18n.language) as Locale;
  return (
    <AppShell
      sidebarItems={sidebarFor('staff', locale, t)}
      bottomNavItems={bottomNavFor('staff', locale, t)}
      menuPath={menuPathFor('staff', locale)}
    >
      <RouteErrorBoundary>
        <Suspense fallback={<PageLoading />}>
          <Routes>
            <Route index element={<Navigate to="today" replace />} />
            <Route path="today"        element={<StaffTodayPage />} />
            <Route path="customers"    element={<StaffCustomersPage />} />
            <Route path="installments" element={<StaffSalesPage />} />
            <Route path="payments"     element={<StaffPaymentsPage />} />
            {/* Staff had no profile route at all, so a clerk could not change
                their language, edit their own details, or log out on web — the
                logout button lives in this page's footer. `ProfilePage`'s own
                docstring already claimed staff used it. */}
            <Route path="profile"      element={<ProfilePage />} />
            <Route path="menu"         element={<MenuPage persona="staff" />} />
            <Route path="*" element={<Navigate to="today" replace />} />
          </Routes>
        </Suspense>
      </RouteErrorBoundary>
    </AppShell>
  );
}

/**
 * `home` is a legacy path (`ref_<code>` deep links still resolve to
 * `home?ref=<code>` — see `startParamToPath`). A bare `<Navigate to="../my" />`
 * would drop `location.search` on the way, silently losing the referral code
 * (M-20). This captures it into storage first — `../my` never reads
 * query params itself — then carries the search string along anyway, so the
 * code isn't lost even if nothing downstream has consumed it yet.
 */
export function ClientHomeRedirect() {
  const location = useLocation();
  captureReferralCode(location.search);
  return <Navigate to={{ pathname: '../my', search: location.search }} replace />;
}

function ClientShell() {
  const { t, i18n } = useTranslation();
  const locale = (useParams<{ locale: Locale }>().locale ?? i18n.language) as Locale;
  return (
    <ClientFrame navItems={bottomNavFor('client', locale, t)}>
      <RouteErrorBoundary>
        <Suspense fallback={<PageLoading />}>
          <Routes>
            {/* The customer lands on the retained home. The deferred welcome
                hub keeps its path as a redirect so existing deep links and bot
                buttons still resolve. */}
            <Route index element={<Navigate to="my" replace />} />
            <Route path="home"     element={<ClientHomeRedirect />} />
            <Route path="my"       element={<ClientHomePage />} />
            <Route path="installments/:id" element={<ClientInstallmentDetailPage />} />
            <Route path="installments/:id/pay" element={<ClientPayPage />} />
            <Route path="installments/:id/receipt" element={<ClientReceiptUploadPage />} />
            {/* Early payoff is deferred — source retained for V2. */}
            {V2_SURFACES_ENABLED && <Route path="installments/:id/payoff" element={<ClientPayoffPage />} />}
            {V2_SURFACES_ENABLED && <Route path="welcome" element={<ClientWelcomePage />} />}
            <Route path="privacy"  element={<ClientPrivacyPage />} />
            <Route path="profile"  element={<ProfilePage />} />
            <Route path="*" element={<Navigate to="my" replace />} />
          </Routes>
        </Suspense>
      </RouteErrorBoundary>
    </ClientFrame>
  );
}

// ── Route-change focus + scroll management (#559) ───────────────────────────
// SPAs swap content without the native page-load focus reset, so SR users never
// hear the new page and sighted users inherit the old scroll position. On every
// pathname change (after the initial mount, so we don't steal focus on load) we
// scroll to top and move focus to the <main> landmark. tabindex=-1 makes <main>
// programmatically focusable without adding it to the tab order; the scroll
// respects prefers-reduced-motion.
function RouteFocusManager() {
  const { pathname } = useLocation();
  const firstRender = useRef(true);
  useEffect(() => {
    if (firstRender.current) { firstRender.current = false; return; }
    const reduce = window.matchMedia?.('(prefers-reduced-motion: reduce)').matches;
    window.scrollTo({ top: 0, left: 0, behavior: reduce ? 'auto' : 'smooth' });
    const main = document.querySelector('main');
    if (main instanceof HTMLElement) {
      if (!main.hasAttribute('tabindex')) main.setAttribute('tabindex', '-1');
      main.focus({ preventScroll: true });
    }
  }, [pathname]);
  return null;
}

// ── Per-route document title (#555) ─────────────────────────────────────────
// SPAs don't reset <title> on navigation. The route→title map + scan logic now
// live in @/lib/routeTitle (shared with MiniAppHeader so the browser tab title
// and the centered Telegram header title stay in lockstep). Landing roots reset
// to the brand; truly-unmatched paths are left for NotFound's own title.
function RouteTitleManager() {
  const { pathname } = useLocation();
  const { t } = useTranslation();
  useEffect(() => {
    const key = routeTitleKeyFor(pathname);
    if (key) document.title = `${t(key)} · Muddato`;
    else if (isLandingPath(pathname)) document.title = 'Muddato';
    // else: unmatched path → leave it for NotFound to title.
  }, [pathname, t]);
  return null;
}

// ── Locale-preserving public redirect ───────────────────────────────────────
//
// These marketing paths are TOP-LEVEL routes with the locale in the pattern
// (`/:locale/features`), so a relative `<Navigate to="../x">` resolves `..`
// against the route hierarchy — up to `/` — and drops the locale, landing on a
// path that matches nothing. Destinations are built absolutely from the matched
// `:locale` instead; an unrecognised segment falls back to the default rather
// than forwarding a prefix no route can match.
const DEFAULT_LOCALE: Locale = 'uz';

function LocaleRedirect({ to }: { to: string }) {
  const { locale } = useParams();
  const lang = (SUPPORTED_LOCALES as readonly string[]).includes(locale ?? '')
    ? (locale as Locale)
    : DEFAULT_LOCALE;
  return <Navigate to={`/${lang}/${to}`} replace />;
}

// ── Top-level router ───────────────────────────────────────────────────────

export function AppRouter() {
  // No global bootstrap! Login is a per-page concern handled by LoginPage.
  const tgEntry = isInsideTelegramClient();

  // Bot-first entry: consume the launch start_param once and deep-link into the
  // right screen (installment / pay / receipt / referral / INVITE).
  //
  // Mounted here rather than in the customer shell because an invite is opened
  // by someone who is not a customer — a business owner claiming their shop has
  // no membership at all, so a hook inside `ClientFrame` never ran for them and
  // their `inv_` token was silently dropped. `/i/:token` is public, so this
  // resolves before any persona gate. Exactly one mount: see the hook's note on
  // the sessionStorage guard.
  useStartParamRedirect();

  return (
    <Suspense fallback={<PageLoading />}>
      <RouteFocusManager />
      <RouteTitleManager />
      <Routes>
        {/* Inside a Mini-App the page launches at "/" → straight to /app via login bootstrap */}
        <Route path="/" element={<Navigate to={tgEntry ? '/auth/login' : '/uz'} replace />} />

        {/* Public marketing */}
        <Route path="/uz" element={<LandingPage />} />
        <Route path="/ru" element={<LandingPage />} />

        {/* Per-locale marketing extras (English slugs) */}
        <Route path="/:locale/pricing"  element={<PricingPage />} />
        <Route path="/:locale/demo"     element={<DemoPage />} />
        <Route path="/:locale/contact"  element={<ContactPage />} />
        {/* Features / Careers / Roadmap are deferred — their paths redirect to
            the retained acquisition surface rather than dead-ending. */}
        {V2_SURFACES_ENABLED && <Route path="/:locale/features" element={<FeaturesPage />} />}
        {V2_SURFACES_ENABLED && <Route path="/:locale/careers"  element={<CareersPage />} />}
        {V2_SURFACES_ENABLED && <Route path="/:locale/roadmap"  element={<RoadmapPage />} />}
        <Route path="/:locale/features" element={<LocaleRedirect to="contact" />} />
        <Route path="/:locale/careers"  element={<LocaleRedirect to="contact" />} />
        <Route path="/:locale/roadmap"  element={<LocaleRedirect to="contact" />} />
        <Route path="/:locale/terms"    element={<TermsPage />} />
        <Route path="/:locale/privacy"  element={<PrivacyPage />} />
        <Route path="/:locale/refund"   element={<RefundPage />} />
        <Route path="/:locale/cookies"  element={<CookiesPage />} />
        {/* Backwards-compat for the old Uzbek slugs */}
        <Route path="/:locale/tariflar"              element={<LocaleRedirect to="pricing" />} />
        <Route path="/:locale/imkoniyatlar"          element={<LocaleRedirect to="features" />} />
        <Route path="/:locale/mahsulot"              element={<LocaleRedirect to="features" />} />
        <Route path="/:locale/bog-lanish"            element={<LocaleRedirect to="contact" />} />
        <Route path="/:locale/vakansiyalar"          element={<LocaleRedirect to="careers" />} />
        <Route path="/:locale/yo-l-xaritasi"         element={<LocaleRedirect to="roadmap" />} />
        <Route path="/:locale/foydalanish-shartlari" element={<LocaleRedirect to="terms" />} />
        <Route path="/:locale/maxfiylik"             element={<LocaleRedirect to="privacy" />} />
        <Route path="/:locale/pul-qaytarish"         element={<LocaleRedirect to="refund" />} />
        <Route path="/:locale/cookielar"             element={<LocaleRedirect to="cookies" />} />

        {/* Public Mini-App: invitation join (customer + staff) */}
        {/* Short slug-less URL is the canonical form; we lookup tenant via token */}
        <Route path="/i/:token"               element={<JoinPage />} />
        {/* A Telegram deep link lands here with an opaque intent; the page asks
            the server where — and whether — it resolves. */}
        <Route path="/r/:intent"              element={<ReviewIntentPage />} />
        <Route path="/m/:slug/i/:token"       element={<JoinPage />} />

        {/* Self-service signup (Round 26) — per-locale.
            Deferred: a shop is onboarded through the demo/contact request an
            operator fulfils, so existing links redirect there. */}
        {V2_SURFACES_ENABLED && <Route path="/:locale/signup" element={<SignupPage />} />}
        <Route path="/:locale/signup"         element={<LocaleRedirect to="demo" />} />

        {/* Auth */}
        <Route path="/auth/login"             element={<LoginPage />} />
        {/* Modern OIDC redirect target — registered in @BotFather → Web Login. */}
        <Route path="/auth/callback"          element={<TelegramCallbackPage />} />
        {/* Backwards-compat alias for the legacy widget redirect path. */}
        <Route path="/auth/telegram-callback" element={<TelegramCallbackPage />} />

        {/* Authenticated app */}
        <Route element={<RequireAuth />}>
          <Route path="/app" element={<PageLoading />} />
          <Route path="/:locale/app" element={<PageLoading />} />
          <Route path="/:locale/platform/*" element={<PersonaGuard allow={['platform']}><PlatformShell /></PersonaGuard>} />
          <Route path="/:locale/tenant/*"   element={<PersonaGuard allow={['tenant']}><TenantShell /></PersonaGuard>} />
          <Route path="/:locale/staff/*"    element={<PersonaGuard allow={['staff', 'tenant']}><StaffShell /></PersonaGuard>} />
          <Route path="/:locale/me/*"       element={<PersonaGuard allow={['client']}><ClientShell /></PersonaGuard>} />
        </Route>

        <Route path="*" element={<NotFound />} />
      </Routes>
    </Suspense>
  );
}

function NotFound() {
  const { t } = useTranslation();
  useEffect(() => { document.title = `${t('common.notFound')} · Muddato`; }, [t]);
  return (
    <main id="main" tabIndex={-1} className="grid min-h-screen place-items-center focus:outline-none">
      <div className="text-center">
        <div className="kicker">404</div>
        <h1 className="mt-2 text-page-title font-semibold">{t('common.notFound')}</h1>
        {/* (QA M-4) A way out. This page used to be a dead end — heading only,
            no link — and inside the Telegram Mini App there is no address bar,
            so a mistyped or stale route left the user with nothing to do but
            close the app. `/` re-runs the role routing and lands each persona
            on their own home. */}
        <Link
          to="/"
          className="mt-6 inline-flex items-center gap-1.5 rounded-pill bg-accent px-5 py-2.5 text-small font-medium text-white hover:opacity-90"
        >
          {t('common.backHome')}
        </Link>
      </div>
    </main>
  );
}
