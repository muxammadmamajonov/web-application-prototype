/** Sidebar + bottom-nav definitions per persona. */
import {
  BarChart3, Boxes, Building2, CircleUser, ClipboardList,
  CreditCard, Gift, Home, LayoutGrid, Megaphone, Receipt, Send, Settings, ShieldCheck,
  Upload, Users, Wallet, type LucideIcon,
} from 'lucide-react';
import type { SidebarItem } from '@/components/layout/Sidebar';
import type { BottomNavItem } from '@/components/layout/MobileBottomNav';
import type { Persona } from '@/types';

type Item = { key: string; label: string; icon: LucideIcon; slug: string; locked?: boolean; section?: string; shortLabel?: string;
  /** (#424) Sibling slug that should also highlight this tab (resolved-redirect target). */
  activeAliasSlug?: string;
  /**
   * `false` marks a destination whose capability is deferred. The definition is
   * kept deliberately — re-enabling is a flag flip — but `mvpItems()` filters it
   * out of every navigation surface, so no sidebar, bottom bar or Menu hub can
   * present a destination that does not ship. Absent ⇒ retained.
   */
  mvp?: false };

/** Per-call navigation state. Absent ⇒ the ordinary, unrestricted product. */
export interface NavOptions {
  /**
   * The ACTIVE tenant membership is blocked. `TenantShell` then routes every
   * path except `billing` to `BlockedGate`, so any other destination offered
   * here is a control that leads back to the gate the user is already looking
   * at.
   */
  blocked?: boolean;
}

/**
 * Destinations that stay routable while a tenant is blocked — the recovery path
 * and nothing else. Kept beside the nav arrays because the sidebar, the bottom
 * bar and the Menu hub must not be able to disagree about it.
 */
const BLOCKED_TENANT_KEYS = ['billing'];

/** The single gate every navigation surface passes through. */
const mvpItems = (items: Item[]): Item[] => items.filter((i) => i.mvp !== false);

/** `mvpItems` plus the status gate. Only the tenant app has a blocked state. */
const visibleItems = (items: Item[], persona: Persona, opts?: NavOptions): Item[] => {
  const base = mvpItems(items);
  if (!opts?.blocked || persona !== 'tenant') return base;
  return base.filter((i) => BLOCKED_TENANT_KEYS.includes(i.key));
};

// Grouped into sections (rendered as headers in the sidebar). Order is kept
// contiguous per section; the mobile bottom-nav picks items by key, so this
// ordering is free to change without breaking the tabs.
const PLATFORM: Item[] = [
  { key: 'overview',  label: 'nav.overview',  icon: LayoutGrid,    slug: 'overview',  section: 'nav.secOperate' },
  { key: 'tenants',   label: 'nav.tenants',   icon: Building2,     slug: 'tenants',   section: 'nav.secOperate' },
  { key: 'users',     label: 'nav.users',     icon: Users,         slug: 'users',     section: 'nav.secOperate', mvp: false },
  { key: 'payments',  label: 'nav.payments',  icon: Wallet,        slug: 'payments',  section: 'nav.secOperate' },
  { key: 'broadcasts', label: 'nav.broadcasts', icon: Send,        slug: 'broadcasts', section: 'nav.secGrow',   mvp: false },
  { key: 'ads',       label: 'nav.ads',       icon: Megaphone,     slug: 'ads',       section: 'nav.secGrow',    mvp: false },
  { key: 'referrals', label: 'nav.referrals', icon: Gift,          slug: 'referrals', section: 'nav.secGrow',    mvp: false },
  { key: 'catalog',   label: 'nav.catalog',   icon: Boxes,         slug: 'catalog',   section: 'nav.secManage',  mvp: false },
  { key: 'plans',     label: 'nav.plans',     icon: CreditCard,    slug: 'plans',     section: 'nav.secManage'  },
  { key: 'admins',    label: 'nav.admins',    icon: ShieldCheck,   slug: 'admins',    section: 'nav.secManage'  },
  { key: 'audit',     label: 'nav.audit',     icon: ClipboardList, slug: 'audit',     section: 'nav.secManage'  },
  { key: 'settings',  label: 'nav.settings',  icon: Settings,      slug: 'settings',  section: 'nav.secManage'  },
  { key: 'profile',   label: 'nav.profile',   icon: CircleUser,    slug: 'profile',   section: 'nav.secManage'  },
];

// Products page intentionally absent (the MVP sells free-text items).
// Grouped: Operatsiya / O'sish / Boshqaruv.
const TENANT: Item[] = [
  { key: 'overview',     label: 'nav.overview',         icon: LayoutGrid, slug: 'overview',     section: 'nav.secOperate' },
  { key: 'customers',    label: 'nav.customers',        icon: Users,      slug: 'customers',    section: 'nav.secOperate' },
  { key: 'installments', label: 'nav.installments',     icon: CreditCard, slug: 'installments', section: 'nav.secOperate', shortLabel: 'nav.installmentsShort' },
  { key: 'payments',     label: 'nav.payments',         icon: Wallet,     slug: 'payments',     section: 'nav.secOperate' },
  { key: 'broadcasts',   label: 'nav.broadcasts',       icon: Send,       slug: 'broadcasts',   section: 'nav.secGrow',    mvp: false },
  // (#15) Deferred: the Overview dashboard already carries the numbers a shop
  // needs day to day, and a second place to read them splits attention without
  // adding an answer. The PAGE and every query behind it are retained — the
  // aging / cashflow / staff / product / LTV / margin endpoints stay live and
  // `DateRangePicker`'s `reports.*` copy is shared infrastructure — so this is a
  // reachability decision, not a deletion. Drop `mvp: false` to bring it back.
  { key: 'reports',      label: 'nav.reports',          icon: BarChart3,  slug: 'reports',      section: 'nav.secGrow',    mvp: false },
  { key: 'staff',        label: 'nav.staff',            icon: Users,      slug: 'staff',        section: 'nav.secManage'  },
  { key: 'billing',      label: 'nav.billing',          icon: Receipt,    slug: 'billing',      section: 'nav.secManage'  },
  { key: 'import',       label: 'nav.import',           icon: Upload,     slug: 'import',       section: 'nav.secManage'  },
  { key: 'settings',     label: 'nav.businessSettings', icon: Settings,   slug: 'settings',     section: 'nav.secManage'  },
  { key: 'profile',      label: 'nav.profile',          icon: CircleUser, slug: 'profile',      section: 'nav.secManage'  },
];

const STAFF_OPEN: Item[] = [
  { key: 'today',        label: 'nav.overview',     icon: Home,        slug: 'today'        },
  { key: 'customers',    label: 'nav.customers',    icon: Users,       slug: 'customers'    },
  { key: 'installments', label: 'nav.installments', icon: CreditCard,  slug: 'installments' },
  { key: 'payments',     label: 'nav.payments',     icon: Wallet,      slug: 'payments'     },
];
// Deferred capabilities with no route behind them for the sales persona — not
// presented, rather than rendered permanently locked on that persona's primary
// daily surface.
// Reachable only through the Menu — staff's four quick-tab slots are spent on
// their daily work, and Profile is where language, personal details and (on web)
// logout live. Without this entry a clerk had no route to any of them.
const STAFF_ACCOUNT: Item[] = [
  { key: 'profile',      label: 'nav.profile',      icon: CircleUser,  slug: 'profile'      },
];

const STAFF_LOCKED: Item[] = [
  { key: 'broadcasts', label: 'nav.broadcasts',       icon: Send,        slug: 'broadcasts', locked: true, mvp: false },
  { key: 'settings',   label: 'nav.businessSettings', icon: Settings,    slug: 'settings',   locked: true, mvp: false },
];

// Products/Plans browsing isn't part of the customer model (the shop creates
// sales) — so the customer app is just their installments + profile.
const CLIENT: Item[] = [
  // Home points at the retained customer home (/me/my). The deferred welcome hub
  // (/me/home) is not a navigation destination; it stays aliased so an existing
  // deep link still highlights this tab.
  { key: 'home',     label: 'nav.overview', icon: Home,       slug: 'my', activeAliasSlug: 'home' },
  { key: 'profile',  label: 'nav.profile',  icon: CircleUser, slug: 'profile' },
];

function buildSidebar(locale: string, persona: string, items: Item[], t: (k: string) => string): SidebarItem[] {
  return items.map((it) => ({
    key: it.key,
    label: t(it.label),
    icon: it.icon,
    to: `/${locale}/${persona}/${it.slug}`,
    locked: it.locked,
    section: it.section ? t(it.section) : undefined,
  }));
}

function buildBottom(locale: string, persona: string, items: Item[], t: (k: string) => string): BottomNavItem[] {
  return items
    .filter((it) => !it.locked)
    .slice(0, 4)
    .map((it) => ({
      key: it.key,
      label: t(it.shortLabel ?? it.label),   // (#33) short bottom-nav label to avoid overflow
      icon: it.icon,
      to: `/${locale}/${persona}/${it.slug}`,
      activeAlias: it.activeAliasSlug ? `/${locale}/${persona}/${it.activeAliasSlug}` : undefined,
    }));
}

export function sidebarFor(persona: Persona, locale: string, t: (k: string) => string, opts?: NavOptions): SidebarItem[] {
  return buildSidebar(locale, PERSONA_SEGMENT[persona], visibleItems(ITEMS_FOR[persona], persona, opts), t);
}

/** Pick items by key (in the given order), tolerant of missing keys — lets the
 *  nav arrays be reordered for sectioning without shifting the bottom-nav tabs. */
const pickByKey = (items: Item[], keys: string[]): Item[] =>
  keys.map((k) => items.find((i) => i.key === k)).filter((i): i is Item => !!i);

/** URL path segment per persona — the customer app lives under /me, not /client. */
const PERSONA_SEGMENT: Record<Persona, string> = {
  platform: 'platform', tenant: 'tenant', staff: 'staff', client: 'me',
};

/**
 * The ≤4 quick-access tabs shown directly in the bottom bar, per persona. This is
 * the SINGLE source the bottom nav AND the Menu hub both read from, so the two can
 * never disagree about which pages are "in the bar" vs "in the menu". (Order = bar
 * order.) The 5th bottom-nav slot is always the route-backed "Menyu" tab.
 */
export const QUICK_KEYS: Record<Persona, string[]> = {
  // Every quick tab must be a retained daily destination — there are only four
  // slots, and a deferred surface must not spend one.
  // Dashboard first — it is the operator's overview and the natural landing.
  // Audit drops out of the bar and into the Menu: it is a look-something-up
  // surface, not a daily one, and the four slots go to what is.
  platform: ['overview', 'tenants', 'payments', 'plans'],
  tenant:   ['overview', 'customers', 'installments', 'payments'],
  staff:    ['today', 'customers', 'installments', 'payments'],
  client:   ['home', 'profile'],
};

const ITEMS_FOR: Record<Persona, Item[]> = {
  platform: PLATFORM,
  tenant:   TENANT,
  staff:    [...STAFF_OPEN, ...STAFF_ACCOUNT, ...STAFF_LOCKED],
  client:   CLIENT,
};

export function bottomNavFor(persona: Persona, locale: string, t: (k: string) => string, opts?: NavOptions): BottomNavItem[] {
  // The quick tabs are a HAND-PICKED subset, so filtering them after the fact
  // would leave a blocked tenant with an empty bar: none of the four ordinary
  // tabs is the recovery destination. Pick from what is visible instead.
  const visible = visibleItems(ITEMS_FOR[persona], persona, opts);
  const keys = QUICK_KEYS[persona].filter((k) => visible.some((i) => i.key === k));
  const quick = keys.length > 0 ? pickByKey(visible, keys) : visible;
  return buildBottom(locale, PERSONA_SEGMENT[persona], quick, t);
}

/**
 * Canonical Menu-hub route for a persona — the target of the mobile "Menyu" tab.
 * Because it's a real route (not an overlay), the active tab is derived from the
 * URL and stays correct on refresh and deep-link.
 */
export function menuPathFor(persona: Persona, locale: string): string {
  return `/${locale}/${PERSONA_SEGMENT[persona]}/menu`;
}

/**
 * The items the Menu hub lists: every persona destination NOT already a quick tab
 * (the quick tabs are reachable from the bar itself), grouped into the same
 * sections as the sidebar so mobile + desktop nav stay unified.
 *
 * Inherits `sidebarFor`'s gate, so the hub lists exactly the destinations the
 * persona can actually open — a row here is always a working destination.
 */
export function menuItemsFor(persona: Persona, locale: string, t: (k: string) => string, opts?: NavOptions): SidebarItem[] {
  const quick = new Set(bottomNavFor(persona, locale, t, opts).map((i) => i.key));
  return sidebarFor(persona, locale, t, opts).filter((i) => !quick.has(i.key));
}

/** Short, calm descriptions for the Menu hub rows — keyed by nav-item key → i18n
 *  key. Optional: a row without an entry simply shows no description. */
export const MENU_DESC_KEY: Record<string, string> = {
  users: 'menuDesc.users', payments: 'menuDesc.payments', broadcasts: 'menuDesc.broadcasts',
  ads: 'menuDesc.ads', referrals: 'menuDesc.referrals', catalog: 'menuDesc.catalog',
  plans: 'menuDesc.plans', admins: 'menuDesc.admins', audit: 'menuDesc.audit',
  settings: 'menuDesc.settings', profile: 'menuDesc.profile', reports: 'menuDesc.reports',
  staff: 'menuDesc.staff', billing: 'menuDesc.billing', import: 'menuDesc.import',
  installments: 'menuDesc.installments', customers: 'menuDesc.customers', overview: 'menuDesc.overview',
};
