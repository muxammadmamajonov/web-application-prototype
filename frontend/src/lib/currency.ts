/**
 * The currency a money label should carry.
 *
 * THE BUG THIS EXISTS TO FIX (found on a real device, 2026-08-08). A shop can
 * choose its currency in Biznes sozlamasi, and that choice was honoured in
 * exactly one place: the backend PDF renderer, which stamps
 * `settings_row.currency` onto generated contracts and receipts. Every screen
 * in the product instead printed the i18n string `billing.currency`, hardcoded
 * to `UZS`. So a shop set to USD saw UZS on all 102 money labels in the app
 * while its own contracts and receipts said USD — the screen and the legal
 * document disagreeing about the unit of a debt.
 *
 * The currency rides on `/auth/me` (see `MeTenant.currency`), which every
 * authenticated surface already has, so a label costs no extra request.
 *
 * SCOPE — this is per-TENANT, and that distinction is load-bearing:
 *
 *   - tenant + staff screens show the ACTIVE shop's money → its currency;
 *   - platform screens show PLATFORM money (plan prices, MRR, top-ups into the
 *     platform balance). A platform admin has no active tenant, so this
 *     resolves to the platform default — which is correct, not a fallback.
 *     During impersonation an active tenant IS set, but the persona resolver
 *     routes that session into the tenant shell, so platform screens and a
 *     tenant currency are never on screen together;
 *   - a customer (`/me`) has no membership, so they get the default. Their
 *     contracts belong to a shop that may use something else — closing that
 *     needs the currency on the per-contract payload, and is NOT done here.
 */
import { useSession } from '@/store/session';

/**
 * What money is labelled with when no shop is in scope: platform revenue, a
 * signed-out visitor, a customer with no membership. Named rather than inlined
 * so these sites read as a deliberate choice instead of a missing lookup.
 */
export const DEFAULT_CURRENCY = 'UZS';

/** Currencies a shop may choose. The Select on Biznes sozlamasi is built from
 *  this, so the options and everything that renders them cannot drift apart. */
export const SUPPORTED_CURRENCIES = ['UZS', 'USD'] as const;
export type CurrencyCode = (typeof SUPPORTED_CURRENCIES)[number];

/** Resolve a stored code to something displayable. An unrecognised value is
 *  shown as-is rather than silently replaced: it is what the PDFs will print. */
export function currencyLabel(code: string | null | undefined): string {
  return (code ?? '').trim() || DEFAULT_CURRENCY;
}

/**
 * The currency a CONTRACT- or PAYMENT-scoped label must carry.
 *
 * `useCurrency()` above answers "what is this shop's money called today", which
 * is right for a KPI, a report total or a product price and WRONG for a
 * contract: switching UZS → USD would relabel debts already agreed, including
 * PDFs already in customers' hands. A contract carries the currency it was
 * written in; read that.
 *
 * `fallback` only covers a cached response predating the field.
 */
export function rowCurrency(
  row: { currency?: string | null } | null | undefined,
  fallback: string,
): string {
  return (row?.currency ?? '').trim() || fallback;
}

/**
 * The active shop's currency, or the platform default when no shop is in scope.
 *
 * Reads the session store, so it re-renders when the active tenant changes
 * (switching shops, entering or leaving impersonation) without any page needing
 * to know that happened.
 */
export function useCurrency(): string {
  return useSession((s) => {
    const id = s.activeTenantId;
    if (!id) return DEFAULT_CURRENCY;
    // `tenants?.` and not just `me?.`: a session can legitimately carry a user
    // with no membership list yet (mid-bootstrap, and in any fixture that builds
    // a partial `me`). Guarding only `me` threw on `.find` and took down every
    // screen that renders a money label — a currency lookup must never be able
    // to fail a page.
    return currencyLabel(s.me?.tenants?.find((t) => t.id === id)?.currency);
  });
}
