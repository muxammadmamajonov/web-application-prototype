/**
 * Which payment methods the product currently offers (#14).
 *
 * Mirrors `ALL_METHODS` / `OFFERED` in backend/app/core/payment_methods.py, and
 * `paymentMethods.contract.test.ts` reads those out of the Python source and
 * fails if the two lists disagree. They have to agree: this list decides what a
 * tenant can switch on, and the backend's decides what a customer is offered to
 * pay with. Drift means a toggle for something nobody can use, or a method
 * offered to payers that its owner can no longer configure.
 *
 * WITHDRAWING A METHOD IS NOT DELETING IT. `ALL_PAYMENT_METHODS` stays complete
 * because stored settings still reference every key — a tenant who configured
 * Click keeps that configuration untouched in the database, and this file never
 * writes. Restoring a method means adding it back to `OFFERED_PAYMENT_METHODS`,
 * and every tenant gets it back exactly as they left it.
 */

/** The complete vocabulary. Never shrinks — stored config may use any of these. */
export const ALL_PAYMENT_METHODS = ['cash', 'card', 'transfer', 'click', 'payme'] as const;

export type PaymentMethodKey = (typeof ALL_PAYMENT_METHODS)[number];

/**
 * What the product exposes right now. #14 withdrew Payme, Click and Bank
 * Transfer while the Telegram-first flow settles.
 */
export const OFFERED_PAYMENT_METHODS: readonly PaymentMethodKey[] = ['cash', 'card'];

export const WITHDRAWN_PAYMENT_METHODS: readonly PaymentMethodKey[] =
  ALL_PAYMENT_METHODS.filter((m) => !OFFERED_PAYMENT_METHODS.includes(m));

export function isOffered(method: string): method is PaymentMethodKey {
  return (OFFERED_PAYMENT_METHODS as readonly string[]).includes(method);
}

/**
 * Card is only a real option once there is somewhere for the money to go.
 *
 * Mirrors `card_is_usable` on the backend. Switching Card on with no receiving
 * card saved would offer the customer a method that resolves to an empty list
 * and no way to pay, so the card's existence is part of availability rather
 * than a warning shown after the fact.
 */
export function cardIsUsable(pm: { card?: boolean; cards?: unknown[] } | null | undefined): boolean {
  if (!pm?.card) return false;
  return Array.isArray(pm.cards) && pm.cards.length > 0;
}
