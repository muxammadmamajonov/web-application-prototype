/**
 * Single source of truth for "what is the title of the page at this path?".
 *
 * Used by both the document-<title> manager (router.tsx) and the in-app
 * MiniAppHeader, so the browser tab title and the centered Telegram header
 * title can never drift apart.
 *
 * The deepest recognised path segment wins, so dynamic ids
 * (e.g. /me/installments/:id) are skipped in favour of their parent
 * ("installments"). Returns the i18n KEY — callers run it through t().
 */
const ROUTE_TITLE_KEYS: Record<string, string> = {
  overview: 'nav.overview', tenants: 'nav.tenants', users: 'nav.users', customers: 'nav.customers',
  payments: 'nav.payments', broadcasts: 'nav.broadcasts', catalog: 'nav.catalog', plans: 'nav.plans',
  ads: 'nav.ads', promos: 'nav.promos', referrals: 'nav.referrals', admins: 'nav.admins', audit: 'nav.audit',
  settings: 'nav.settings', profile: 'nav.profile', billing: 'nav.billing', import: 'nav.import',
  reports: 'nav.reports', staff: 'nav.staff', installments: 'nav.installments', menu: 'common.menu',
  today: 'routeTitle.today', my: 'routeTitle.my', home: 'routeTitle.home', pay: 'routeTitle.pay',
  receipt: 'routeTitle.receipt', payoff: 'routeTitle.payoff', privacy: 'routeTitle.privacy',
  pricing: 'routeTitle.pricing', features: 'routeTitle.features', demo: 'routeTitle.demo',
  contact: 'routeTitle.contact', careers: 'routeTitle.careers', roadmap: 'routeTitle.roadmap',
  terms: 'routeTitle.terms', refund: 'routeTitle.refund', cookies: 'routeTitle.cookies',
  login: 'routeTitle.login', callback: 'routeTitle.login', 'telegram-callback': 'routeTitle.login',
  signup: 'routeTitle.signup', i: 'routeTitle.join',
};

/** Resolve the i18n title key for a pathname, scanning from the deepest segment
 *  so dynamic ids are skipped. Returns null when nothing matches. */
export function routeTitleKeyFor(pathname: string): string | null {
  const segs = pathname.split('/').filter(Boolean);
  for (let i = segs.length - 1; i >= 0; i--) {
    if (ROUTE_TITLE_KEYS[segs[i]]) return ROUTE_TITLE_KEYS[segs[i]];
  }
  return null;
}

/** True when the path is the locale-only landing root (/, /uz, /ru). */
export function isLandingPath(pathname: string): boolean {
  const segs = pathname.split('/').filter(Boolean);
  return segs.length === 0 || (segs.length === 1 && (segs[0] === 'uz' || segs[0] === 'ru'));
}
