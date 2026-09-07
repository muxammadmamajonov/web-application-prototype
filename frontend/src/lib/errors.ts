import i18n from '@/i18n';

/**
 * Turn an axios/API error into a clean, localized message.
 *
 * Every surface funnels through here, and three properties must hold:
 *
 *  1. **No raw backend text ever reaches a user** — neither a snake_case code
 *     nor server-written prose. Both are untranslated English inside a uz/ru
 *     product, and prose can carry internal detail. An unmapped code falls
 *     through to a localized message chosen by HTTP status.
 *
 *  2. **Failures are classified.** `errorKind()` separates offline from
 *     server-unavailable from validation/permission/conflict/not-found, so a
 *     caller can tell whether a retry is even meaningful.
 *
 *  3. **Permission failures name the role required**, so "nothing happened"
 *     becomes "only the shop owner can do this".
 */

/** What kind of failure this is — drives copy, retry affordance and recovery. */
type ErrorKind =
  | 'offline'      // no network / request never left the device
  | 'timeout'      // request sent, no answer in time
  | 'server'       // 5xx — the server is unwell; retrying may help
  | 'validation'   // 400/422 — the input is wrong; retrying as-is will not help
  | 'permission'   // 401/403 — the actor may not do this
  | 'notFound'     // 404 — the thing is gone
  | 'conflict'     // 409 — state moved under us
  | 'rateLimited'  // 429
  | 'unknown';

interface ApiErrorShape {
  code?: string;
  response?: {
    status?: number;
    data?: { detail?: unknown };
    /** Axios gives either a plain lowercased map or an `AxiosHeaders` instance. */
    headers?: Record<string, unknown> | { get?: (name: string) => unknown };
  };
  message?: string;
}

/**
 * The server's correlation id for a failed request, short enough to read aloud.
 *
 * Every response carries `X-Request-Id` (backend `observability.py`), and until
 * now the frontend threw it away — so a 5xx reached the user as one untraceable
 * sentence and reached support as "it said temporary error". With no id there is
 * no way to find the matching server log, which is exactly how #4 (Create
 * Contract fails with "temporary server error") stayed un-diagnosed: the string
 * covers 500, 503 and 504 alike, and nothing distinguishes them client-side.
 *
 * Truncated to 8 characters: enough to locate one request in a day of logs,
 * short enough for a shop owner to read over the phone.
 */
export function errorRequestId(err: unknown): string | undefined {
  const headers = (err as ApiErrorShape)?.response?.headers;
  if (!headers) return undefined;

  const getter = (headers as { get?: (n: string) => unknown }).get;
  const raw = typeof getter === 'function'
    ? getter.call(headers, 'x-request-id')
    // Axios lowercases response header names, but a hand-rolled mock or a proxy
    // may not — so check both rather than silently finding nothing.
    : (headers as Record<string, unknown>)['x-request-id']
      ?? (headers as Record<string, unknown>)['X-Request-Id'];

  if (typeof raw !== 'string') return undefined;
  const trimmed = raw.trim();
  return trimmed ? trimmed.slice(0, 8) : undefined;
}

/** Backend codes with a hand-written, localized explanation. */
const CODE_KEYS: Record<string, string> = {
  customer_not_found: 'errors.customer_not_found',
  customer_has_open_installments: 'errors.customer_has_open_installments',
  customer_pending_approval: 'errors.customer_pending_approval',
  customer_blocked: 'errors.customer_blocked',
  installment_not_found: 'errors.installment_not_found',
  product_not_found: 'errors.product_not_found',
  amount_exceeds_remaining: 'errors.amount_exceeds_remaining',
  active_installment_cannot_be_deleted: 'errors.active_installment_cannot_be_deleted',
  only_draft_installment_can_be_deleted: 'errors.only_draft_installment_can_be_deleted',
  contract_number_conflict: 'errors.contract_number_conflict',
  plan_limit_reached: 'errors.plan_limit_reached',
  // The PLATFORM catalogue ceiling (409 from POST /platform/plans), not a
  // shop's subscription limit. Different actor, different remedy — sharing
  // `plan_limit_reached` told a super-admin to upgrade a plan they own.
  platform_plan_limit_reached: 'errors.platform_plan_limit_reached',
  // The stored "Standart tarif" names a plan the catalogue does not contain.
  // Refused on write rather than stored, because the previous dangling default
  // (`lite`) is what every shop provisioned after R36 was stamped with.
  default_plan_unknown: 'errors.default_plan_unknown',
  yearly_discount_invalid: 'errors.yearly_discount_invalid',
  yearly_discount_out_of_range: 'errors.yearly_discount_out_of_range',
  no_plan_catalogue: 'errors.no_plan_catalogue',
  // The account phone is proved, not typed. The CURRENT profile screen never
  // sends one, so this is reachable only from a Mini App build Telegram's
  // WebView still has cached — which is exactly the client that would otherwise
  // meet the generic "your input is wrong" on every save it attempts.
  phone_not_editable: 'errors.phone_not_editable',
  rate_limited: 'errors.rate_limited',
  no_tenant_membership: 'errors.no_tenant_membership',
  bad_mapping: 'errors.bad_mapping',
  not_pending: 'errors.not_pending',
  payment_not_found: 'errors.payment_not_found',
  invitation_expired: 'errors.invitation_expired',
  // The backend raises `invitation_already_used` (read) and
  // `invitation_invalid_or_used` (claim). The map held only `invitation_used`,
  // a code nothing emits — so a link that had already been used fell through
  // to the generic "something went wrong" instead of saying so.
  // `invitationCodes.contract.test.ts` now pins these against the source.
  invitation_already_used: 'errors.invitation_used',
  invitation_invalid_or_used: 'errors.invitation_used',
  invitation_used: 'errors.invitation_used',
  invitation_not_found: 'errors.invitation_not_found',
  init_data_invalid: 'errors.init_data_invalid',
  insufficient_balance: 'errors.insufficient_balance',
  tenant_blocked: 'errors.tenant_blocked',
  provider_not_configured: 'errors.provider_not_configured',
  telegram_send_failed: 'errors.telegram_send_failed',
  bad_status: 'errors.validation',
  // (r96) Unique-constraint conflicts. The backend has mapped these to named
  // codes since #87, but none were listed here, so every one of them fell
  // through to `errors.conflict` — "this data has already changed, refresh the
  // page". That is the wrong advice AND the wrong diagnosis: nothing changed
  // under the user, they typed a value someone else already holds, and
  // refreshing cannot help. Each now names the field at fault.
  customer_phone_exists: 'errors.customer_phone_exists',
  guarantor_phone_exists: 'errors.guarantor_phone_exists',
  phone_already_registered: 'errors.phone_already_registered',
  email_already_registered: 'errors.email_already_registered',
  product_sku_exists: 'errors.product_sku_exists',
  invoice_period_exists: 'errors.invoice_period_exists',
};

/**
 * Backend codes that identify WHICH role the action needs, mapped to a message
 * that names it. These are the literals the API actually raises — see
 * `_require_owner` / `_require_owner_or_manager` in backend `app/api/tenant/_shared.py`
 * and `_require_platform` in `app/api/platform/_shared.py`. Mapping them here (rather
 * than renaming the API's codes) keeps the wire contract, and its existing
 * backend test coverage, untouched.
 */
const ROLE_REQUIRED_KEYS: Record<string, string> = {
  owner_only: 'errors.permission_owner',
  owner_or_manager_only: 'errors.permission_ownerOrManager',
  platform_only: 'errors.permission_platformAdmin',
  no_tenant_membership: 'errors.no_tenant_membership',
};

/** The machine code carried by the error, if any. */
export function errorCode(err: unknown): string | undefined {
  const detail = (err as ApiErrorShape)?.response?.data?.detail;
  if (typeof detail === 'string') return detail;
  if (detail && typeof detail === 'object') {
    const inner = (detail as { error?: string; message?: string }).error
      ?? (detail as { message?: string }).message;
    if (typeof inner === 'string') return inner;
  }
  return undefined;
}

/** Classify a failure so callers can choose copy, retry and recovery. */
export function errorKind(err: unknown): ErrorKind {
  const e = err as ApiErrorShape;
  if (!e?.response) {
    if (e?.code === 'ECONNABORTED') return 'timeout';
    if (e?.code === 'ERR_NETWORK') return 'offline';
    // A request that produced neither a response nor a recognised transport
    // code is, from the user's side, indistinguishable from being offline —
    // and the browser agrees when navigator.onLine says so.
    if (typeof navigator !== 'undefined' && navigator.onLine === false) return 'offline';
    return 'unknown';
  }
  const s = e.response.status ?? 0;
  if (s === 401 || s === 403) return 'permission';
  if (s === 404) return 'notFound';
  if (s === 409) return 'conflict';
  if (s === 429) return 'rateLimited';
  if (s === 400 || s === 422) return 'validation';
  if (s >= 500) return 'server';
  return 'unknown';
}

/** True when retrying the very same request could plausibly succeed. */
export function isRetryable(err: unknown): boolean {
  return ['offline', 'timeout', 'server', 'rateLimited'].includes(errorKind(err));
}

/** Localized message for a failure kind, used when no specific code applies. */
function kindMessage(kind: ErrorKind): string {
  switch (kind) {
    case 'offline':     return i18n.t('errors.offline', 'Internet aloqasi yo‘q — ulanishni tekshirib, qayta urinib ko‘ring');
    case 'timeout':     return i18n.t('errors.timeout', 'Javob kelmadi — aloqa sekin. Qayta urinib ko‘ring');
    case 'server':      return i18n.t('errors.server', 'Serverda vaqtinchalik nosozlik — birozdan so‘ng qayta urinib ko‘ring');
    case 'validation':  return i18n.t('errors.validation', 'Kiritilgan ma’lumotda xatolik bor — tekshirib, qayta yuboring');
    case 'permission':  return i18n.t('errors.permission_generic', 'Bu amalni bajarishga ruxsatingiz yo‘q');
    case 'notFound':    return i18n.t('errors.notFound', 'Topilmadi — o‘chirilgan yoki ko‘chirilgan bo‘lishi mumkin');
    case 'conflict':    return i18n.t('errors.conflict', 'Bu ma’lumot allaqachon o‘zgargan — sahifani yangilab, qayta ko‘ring');
    case 'rateLimited': return i18n.t('errors.rate_limited', 'Juda tez-tez — biroz keyin urinib ko‘ring');
    default:            return i18n.t('errors.unknown', 'Nimadir noto‘g‘ri ketdi — qayta urinib ko‘ring');
  }
}

export function apiErrorMessage(err: unknown): string {
  const kind = errorKind(err);
  const code = errorCode(err);

  // A permission failure says WHO can do this, not just that you cannot. Role
  // naming wins; a specifically mapped code (e.g. an invalid Telegram initData,
  // which is a 401 but not a role problem) is preferred over the generic
  // refusal; only an unmapped 401/403 falls back to it.
  if (kind === 'permission') {
    if (code && ROLE_REQUIRED_KEYS[code]) return i18n.t(ROLE_REQUIRED_KEYS[code]);
    if (code && CODE_KEYS[code]) return i18n.t(CODE_KEYS[code]);
    return i18n.t('errors.permission_generic', 'Bu amalni bajarishga ruxsatingiz yo‘q');
  }

  // A plan limit carries the number the shop actually hit; render it here
  // rather than echoing the server's sentence.
  if (code === 'plan_limit_reached') {
    const limit = planLimit(err);
    return limit == null
      ? i18n.t('errors.plan_limit_reached')
      : i18n.t('errors.plan_limit_reached_n', { limit });
  }

  if (code && CODE_KEYS[code]) return i18n.t(CODE_KEYS[code]);

  // Backend prose is NOT shown just because it contains spaces. It is written
  // server-side, is not translated, and can carry internal detail — so an
  // unmapped code falls through to the localized message for its kind. Adding a
  // key to CODE_KEYS is how a new backend code becomes user-facing.
  const message = kindMessage(kind);

  // Only a 5xx carries the id. A validation or permission failure is the user's
  // to fix and needs no trace, but "temporary server error" is unactionable
  // without one — it is the same sentence for a 500, a 503 from a fail-closed
  // rate limiter, and a 504 from the proxy's upstream timeout. The id is what
  // tells those three apart in the log.
  //
  // Appended only when the header is actually present, so a client-side or
  // mocked failure keeps the clean sentence.
  if (kind === 'server') {
    const rid = errorRequestId(err);
    if (rid) return `${message} (#${rid})`;
  }
  return message;
}

/** The numeric cap on a 402 plan-limit response, when the server sent one. */
function planLimit(err: unknown): number | undefined {
  const detail = (err as ApiErrorShape)?.response?.data?.detail;
  if (detail && typeof detail === 'object') {
    const n = (detail as { limit?: unknown }).limit;
    if (typeof n === 'number' && Number.isFinite(n)) return n;
  }
  return undefined;
}

/**
 * An error whose `message` is already a localized, user-facing sentence.
 *
 * Thrown by client-side validation that has nothing to do with the network.
 * Only these may have their `message` shown directly: an Axios transport
 * failure also has a `message` ("Network Error"), and showing that leaks raw
 * English into a uz/ru product.
 */
export class LocalizedError extends Error {
  readonly localized = true as const;
}

/** True for an error carrying an already-localized user-facing message. */
export function isLocalizedError(err: unknown): err is LocalizedError {
  return err instanceof LocalizedError
    || (typeof err === 'object' && err !== null
        && (err as { localized?: unknown }).localized === true);
}

/** True when the error is a 404 (resource already gone / not found). */
export function isNotFound(err: unknown): boolean {
  return errorKind(err) === 'notFound';
}

/** True when the device/request never reached the server. */
export function isOffline(err: unknown): boolean {
  return errorKind(err) === 'offline';
}
