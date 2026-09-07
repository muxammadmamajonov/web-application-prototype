/** Number / money / date formatters tuned for the Muddato UI. */
import i18n from '@/i18n';
import { DEFAULT_CURRENCY } from './currency';
import type { TFunction } from 'i18next';

const groupSpace = (n: number): string =>
  Math.trunc(n).toLocaleString('ru-RU').replace(/\u00A0/g, ' ');

/** "1 240 000" — what the design uses everywhere for UZS sums. */
export function money(n: number | string | null | undefined): string {
  if (n === null || n === undefined) return '—';
  const v = typeof n === 'string' ? Number(n) : n;
  if (!Number.isFinite(v)) return '—';
  return groupSpace(v);
}

/**
 * "1 240 000 UZS" — the amount with an explicit currency.
 *
 * The currency is a PARAMETER, not an ambient i18n lookup. It used to read
 * `billing.currency`, a single hardcoded string, which is how a shop trading in
 * USD ended up seeing UZS on every screen while its own contracts and receipts
 * (rendered from `settings_row.currency`) said USD. Callers inside the tenant
 * app pass `useCurrency()`; anything showing platform money passes
 * `DEFAULT_CURRENCY` explicitly, so which one applies is visible at the call
 * site rather than decided by a global.
 */
export function moneyWithCurrency(
  n: number | string | null | undefined,
  currency: string = DEFAULT_CURRENCY,
): string {
  return `${money(n)} ${currency}`;
}

export function shortMoney(n: number): string {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1).replace('.0', '')}M`;
  if (n >= 1_000) return `${(n / 1_000).toFixed(0)}K`;
  return String(n);
}

export function pct(value: number, fractionDigits = 1): string {
  return `${value.toFixed(fractionDigits)}%`;
}

/**
 * Localized payment-method name, read from the `tenantSettings.pm_*` bundle
 * that already maps cash/card/transfer/click/payme for both uz and ru. (Click
 * and Payme are brand names, so they are deliberately identical in both.)
 *
 * ONE implementation for every surface that shows a method — the tenant payment
 * ledger, the tenant contract detail and the customer's own payment history.
 * The mapping used to be duplicated per-page, and the page that lacked a copy
 * showed a Telegram customer the raw enum: "CASH" in an otherwise Uzbek screen.
 *
 * Degrading, in order:
 *  - no usable method at all (null / undefined / blank) → the em-dash
 *    placeholder the rest of the app uses, never an empty cell;
 *  - a method the bundle does not know → the token upper-cased, which is at
 *    least readable, rather than blank.
 *
 * The key is trimmed and lower-cased BEFORE lookup so a shouted enum ("CASH")
 * still resolves to "Naqd" — otherwise it would miss the bundle and fall
 * through to the raw-token fallback, reintroducing the defect it exists to fix.
 */
export function paymentMethodLabel(t: TFunction, method: string | null | undefined): string {
  const key = String(method ?? '').trim().toLowerCase();
  if (!key) return '—';
  return t(`tenantSettings.pm_${key}`, key.toUpperCase());
}

/** "15-noyabr" or "15 ноября" depending on locale. */
const MONTHS_UZ_SHORT = ['yan','fev','mar','apr','may','iyn','iyl','avg','sen','okt','noy','dek'];

export function shortDate(d: Date | string | null | undefined): string {
  if (!d) return '—';
  const date = typeof d === 'string' ? new Date(d) : d;
  if (Number.isNaN(date.getTime())) return '—';
  const locale = i18n.language?.startsWith('ru') ? 'ru' : 'uz';
  if (locale === 'uz') return `${date.getDate()}-${MONTHS_UZ_SHORT[date.getMonth()]}`;
  return date.toLocaleDateString('ru-RU', { day: 'numeric', month: 'short' });
}

/** Today's date as a `YYYY-MM-DD` string in the LOCAL timezone.
 *  Mirrors `DatePicker`'s `toISO` (which parses/formats in local time), so a
 *  birthdate `max={localToday()}` matches the local "today" even in the evening
 *  in UZT (UTC+5), where `new Date().toISOString().slice(0,10)` would still
 *  report yesterday until midnight UTC. */
export function localToday(now: Date = new Date()): string {
  const y = now.getFullYear();
  const m = String(now.getMonth() + 1).padStart(2, '0');
  const d = String(now.getDate()).padStart(2, '0');
  return `${y}-${m}-${d}`;
}

/**
 * Local wall-clock time, `HH:MM`.
 *
 * Queries hold data for up to a minute and never refetch on focus or mount, so
 * a dashboard figure can be meaningfully older than "now". Screens showing money
 * or work queues state when their figures were computed; this renders that
 * moment compactly, in the viewer's own timezone.
 */
export function timeOfDay(d: Date | string | null | undefined): string {
  if (!d) return '—';
  const date = typeof d === 'string' ? new Date(d) : d;
  if (Number.isNaN(date.getTime())) return '—';
  return `${String(date.getHours()).padStart(2, '0')}:${String(date.getMinutes()).padStart(2, '0')}`;
}

/**
 * "15-noy, 14:32" / "15 нояб., 14:32" — {@link shortDate} plus {@link timeOfDay}.
 *
 * Money movements are reviewed against a bank statement line, and a day alone
 * cannot be matched to one: two transfers from the same business on the same day
 * are indistinguishable without the clock time. Composed from the two existing
 * formatters so the date half stays byte-identical to every other date on screen.
 */
export function dateTime(d: Date | string | null | undefined): string {
  if (!d) return '—';
  const date = typeof d === 'string' ? new Date(d) : d;
  if (Number.isNaN(date.getTime())) return '—';
  return `${shortDate(date)}, ${timeOfDay(date)}`;
}

export function relativeFromNow(d: Date | string): string {
  const date = typeof d === 'string' ? new Date(d) : d;
  const diff = Math.round((Date.now() - date.getTime()) / 1000);
  // Locale-aware + pluralized via i18next ({{count}}). uz has one/other,
  // ru declines (one/few/many) — both keyed in the `time` namespace.
  if (diff < 60) return i18n.t('time.justNow', 'hozir');
  if (diff < 3600) return i18n.t('time.minutes', { count: Math.floor(diff / 60), defaultValue: '{{count}} min' });
  if (diff < 86_400) return i18n.t('time.hours', { count: Math.floor(diff / 3600), defaultValue: '{{count}} soat' });
  return shortDate(date);
}

export function initials(name: string | null | undefined, max = 2): string {
  // A display name can legitimately BE a Telegram handle ("@johndoe") when the
  // person has no first/last name (see `displayName`), and an avatar reading
  // "@" identifies nobody — take the first letter, not the sigil.
  const source = (name ?? '').replace(/^@/, '').trim();
  if (!source) return '?';
  const parts = source.split(/\s+/).slice(0, max);
  return parts.map((p) => p[0]?.toUpperCase() ?? '').join('');
}

/**
 * A person's name, pre-split into parts.
 *
 * Callers pass fields rather than a row on purpose: the same human arrives as
 * snake_case from the list DTOs (`first_name`, admins / users / staff) and as
 * camelCase from the session (`firstName`, `me`), so a row-shaped helper would
 * immediately need a second copy — which is exactly how this drifted before.
 *
 * `id` is accepted so a caller holding a Telegram row can hand over its whole
 * identity, and is deliberately NEVER read — see {@link displayName}.
 */
export interface NameParts {
  first?: string | null;
  last?: string | null;
  username?: string | null;
  id?: number | string | null;
}

/**
 * The ONE way to render a person's name.
 *
 * This lived five times — platform admins, platform users, tenant staff, the
 * mobile menu strip and the profile card. The primary expression was identical
 * in every copy; only the tails differed, so the SAME nameless Telegram user
 * was shown as `@johndoe` on one screen, `johndoe` on another, and `tg 12345`,
 * `#12345`, `—` or nothing at all elsewhere.
 *
 * The merged chain is **full name → `@username` → fallback**:
 *  - the handle keeps its `@`, because that is how Telegram writes it and how
 *    the sub-lines beside these names already write it;
 *  - the id-derived variants (`tg 12345`, `#12345`) are dropped on purpose. A
 *    Telegram user id is an internal identifier, not a name; it must never
 *    reach the UI, which is why `id` is part of {@link NameParts} yet unused.
 *
 * `fallback` is the single knob, for the one caller where the name is a
 * FRAGMENT of a composed caption ("Ism · Rol") rather than a standalone field:
 * there an em-dash reads as a missing list item, so it asks for `''`.
 */
export function displayName(
  parts: NameParts,
  { fallback = '—' }: { fallback?: string } = {},
): string {
  const full = [parts.first, parts.last].filter(Boolean).join(' ').trim();
  if (full) return full;
  const handle = parts.username?.trim();
  return handle ? `@${handle}` : fallback;
}
