import { money } from './format';
import type { MoneyByCurrency } from './queries/tenant';

/**
 * The part of a money total that the screen's single figure does NOT cover.
 *
 * (MUDD-114) A shop's `settings.currency` decides only what the NEXT contract
 * is written in, so a tenant can hold both UZS and USD contracts. The server
 * returns a per-currency map plus a scalar for the primary currency; this turns
 * everything OUTSIDE that scalar into one short string for a `hint` slot —
 * `"+ 4 500 USD"` — so a second currency is stated rather than silently added
 * in or silently dropped.
 *
 * Returns `undefined` when there is nothing LEFT OVER, which is the
 * overwhelmingly common single-currency case: the hint disappears entirely
 * rather than rendering an empty row, and those dashboards look exactly as
 * they did. Exactly zero is nothing left over — a "+ 0 USD" row is noise, and
 * suppressing it loses no figure.
 *
 * (MUDD-121) A NON-zero amount is stated with its own sign, never suppressed.
 * `gross_profit_by_currency` is the first signed map to reach here (revenue
 * minus cost, unfloored), so a USD line sold below cost is negative — and a
 * `> 0` filter would have dropped that currency off the card while the CSV
 * export, which explodes the same map by KEY, still printed it. One report
 * answering two ways is worse than the wrong-number bug this helper exists to
 * fix. Every other caller (`revenue`, `outstanding`, `commission`, `paid`,
 * `lifetime_value`, `debt_total`, `due_today`, `overdue`, `received_today`) is
 * a sum of non-negative money, so their behaviour is unchanged.
 *
 * Sorted by code so the string is stable across renders — an unordered object
 * would let two identical datasets produce two different hints.
 */
export function secondaryCurrencyHint(
  byCurrency: MoneyByCurrency | undefined,
  primary: string | undefined,
): string | undefined {
  if (!byCurrency || !primary) return undefined;
  const rest = Object.entries(byCurrency)
    .filter(([code, amount]) => code !== primary && amount !== 0)
    .sort(([a], [b]) => a.localeCompare(b));
  if (rest.length === 0) return undefined;
  // The sign is carried by the leading operator, not by `money()`, so the
  // spacing matches the positive case ("- 50 USD", never "+ -50 USD").
  return rest
    .map(([code, amount]) => `${amount < 0 ? '-' : '+'} ${money(Math.abs(amount))} ${code}`)
    .join(' · ');
}
