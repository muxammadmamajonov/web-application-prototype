/**
 * telegramDeepLinks — bot-first entry. The bot (and any reminder / payment /
 * contract message) launches the Mini App with a `start_param`; this module is
 * the single contract for ENCODING those params (so the bot and frontend agree)
 * and DECODING them into an in-app route on launch.
 *
 * Param grammar (Telegram allows A-Z a-z 0-9 _ - only): `<kind>_<id?>`.
 *   home | my | profile                 → persona home screens
 *   inst_<id>                           → installment detail
 *   pay_<id> | payoff_<id> | receipt_<id> → installment action screens
 *   join_<token>                        → invitation join
 *   ref_<code>                          → referral landing
 * IDs are our UUIDs (hyphens are legal in start params). We sanitise the id to
 * the legal charset before routing — navigation is internal-only, so there's no
 * open-redirect surface, but we still refuse anything malformed.
 */
import { useEffect } from 'react';
import { useNavigate, useParams, useLocation } from 'react-router';
import { useTranslation } from 'react-i18next';
import { tg } from './adapter';

const ID_RE = /^[A-Za-z0-9_-]{1,64}$/;
type Locale = 'uz' | 'ru';

/**
 * An invitation code (#12): 6 characters, no prefix — `?startapp=A7K3QX`.
 *
 * Mirrors `LEGACY_ALPHABET` / `LEGACY_CODE_LEN` in backend/app/core/invite_codes.py:
 * A-Z without I or O, plus 2-9. `deepLinks.contract.test.ts` reads those
 * constants out of the backend source and fails if this drifts from them —
 * an encoder and a decoder that disagree is the exact shape of #3.
 *
 * Matched case-insensitively and normalised to upper case before routing,
 * because the recipient types whatever their keyboard gives them.
 */
const INVITE_CODE_RE = /^[A-HJ-NP-Z2-9]{6}$/i;

/** The current invitation credential: 24 random bytes, url-safe base64.
 *
 *  It can contain `_` and `-`, so it must be recognised BEFORE the
 *  `<kind>_<id>` split below — otherwise `xK3_pQ7…` is read as a kind of
 *  "xK3" and the link resolves to nothing. Length-anchored, and the known
 *  kinds are tried first, so the two shapes cannot be confused. */
const INVITE_CREDENTIAL_RE = /^[A-Za-z0-9_-]{32}$/;

/**
 * A payment-review intent (see backend `app/core/review_intent.py`).
 *
 * Exactly 43 characters: base64url of a 32-byte payload (purpose + raw UUID +
 * expiry + HMAC tag), unpadded. Distinct from the 32-character invitation
 * credential above, so the two shapes cannot be confused — which matters,
 * because sending an intent to `/i/` would try to redeem it as an invitation.
 *
 * Matching the shape does NOT mean the token is valid. It is opaque and
 * unverifiable here; the server checks the signature, the expiry AND the
 * caller's authorization. All this does is choose which screen resolves it.
 */
const REVIEW_INTENT_RE = /^[A-Za-z0-9_-]{43}$/;
/** Prefixes that mean `<kind>_<id>`, not a credential. */
const LINK_KINDS = ['inst', 'pay', 'payoff', 'receipt', 'inv', 'join', 'tinst'];

/** Build the `start_param` string the bot should embed (mirrors decode below). */
export const startParam = {
  home: () => 'home',
  my: () => 'my',
  profile: () => 'profile',
  /** SHOP-side billing screen — what a trial-expiry or dunning warning
   *  offers the owner. Deliberately not `pay`, which is the customer's own
   *  pay screen under `/me`. */
  billing: () => 'billing',
  installment: (id: string) => `inst_${id}`,
  /** SHOP-side contract screen, for an owner's copy of an overdue alert. */
  tenantInstallment: (id: string) => `tinst_${id}`,
  pay: (id: string) => `pay_${id}`,
  payoff: (id: string) => `payoff_${id}`,
  receipt: (id: string) => `receipt_${id}`,
  /** What the backend mints now (#12): the bare code, no prefix. */
  invite: (code: string) => code.toUpperCase(),
  /** Legacy alias. Still decoded, but nothing server-side emits it. */
  join: (token: string) => `join_${token}`,
  referral: (code: string) => `ref_${code}`,
};

/** Full t.me link that opens the Mini App at a given start param. */
export function botMiniAppLink(botUsername: string, param: string): string {
  return `https://t.me/${botUsername}?startapp=${encodeURIComponent(param)}`;
}

/** The raw start param for this launch, from initData (in TG) or the URL. */
function readStartParam(): string | null {
  const fromInit = tg()?.initDataUnsafe?.start_param;
  if (fromInit) return fromInit;
  if (typeof location === 'undefined') return null;
  const u = new URLSearchParams(location.search);
  return u.get('tgWebAppStartParam') || u.get('startapp') || null;
}

/** Decode a start param to an in-app path (locale-prefixed), or null if it
 *  doesn't map to a known customer destination. */
export function startParamToPath(raw: string | null | undefined, locale: Locale): string | null {
  if (!raw) return null;
  const me = `/${locale}/me`;
  // Shop-side destinations. The route guard still decides whether this person
  // may see them — an owner who taps a billing warning lands on billing, and a
  // customer who somehow received the link lands on their own shell instead.
  if (raw === 'billing') return `/${locale}/tenant/billing`;
  if (raw === 'home') return `${me}/home`;
  if (raw === 'my') return `${me}/my`;
  if (raw === 'profile') return `${me}/profile`;

  // (#12) A bare code carries no `_`, so it has to be recognised by shape
  // before the `<kind>_<id>` split below — which would otherwise drop it. The
  // named keywords above are checked first, so a future keyword that happened
  // to be six legal characters would still win rather than being read as a code.
  if (INVITE_CODE_RE.test(raw)) return `/i/${raw.toUpperCase()}`;

  // A current credential is passed through exactly: unlike the retired 6-char
  // code it is case-SIGNIFICANT, and upper-casing it would produce a value that
  // hashes to a different verifier and resolves to nothing.
  const sep = raw.indexOf('_');
  const maybeKind = sep === -1 ? '' : raw.slice(0, sep);
  if (!LINK_KINDS.includes(maybeKind) && INVITE_CREDENTIAL_RE.test(raw)) return `/i/${raw}`;

  // A review intent is opaque, so it cannot be decoded into a route here. It
  // goes to a resolver screen that asks the server — which is also where the
  // authorization happens, so a forwarded link lands on "nothing to see"
  // rather than on somebody else's receipt.
  if (!LINK_KINDS.includes(maybeKind) && REVIEW_INTENT_RE.test(raw)) return `/r/${raw}`;

  if (sep === -1) return null;
  const kind = raw.slice(0, sep);
  const id = raw.slice(sep + 1);
  if (!ID_RE.test(id)) return null;

  switch (kind) {
    case 'inst':
      return `${me}/installments/${id}`;
    // The shop's own view of a contract. `pay_<id>` cannot be reused for this:
    // it resolves under `/me`, so an owner tapping their overdue alert would
    // land on the customer persona for a contract that is not theirs.
    case 'tinst':
      return `/${locale}/tenant/installments?installment=${id}`;
    case 'pay':
      return `${me}/installments/${id}/pay`;
    case 'payoff':
      return `${me}/installments/${id}/payoff`;
    case 'receipt':
      return `${me}/installments/${id}/receipt`;
    // Prefixed links are LEGACY as of #12 — nothing server-side emits them any
    // more, but invitations live up to 30 days, so links already sitting in
    // people's chats must keep resolving. They stay decoded until the longest
    // possible outstanding invite has expired.
    //
    // Both prefixes are here because only `join_` used to be, while the backend
    // minted `inv_`: on a modern Telegram client (which honours `startapp=` and
    // never fires the bot's /start fallback) an owner-claim link resolved to
    // null and the invitee landed on a generic screen with the token unused.
    // Old clients worked via the bot, which is why it looked intermittent (#3).
    //
    // Only a code-shaped id is upper-cased. A legacy 10-char token is
    // mixed-case and must be passed through exactly.
    case 'inv':
    case 'join':
      return `/i/${INVITE_CODE_RE.test(id) ? id.toUpperCase() : id}`;
    case 'ref':
      return `${me}/home?ref=${encodeURIComponent(id)}`;
    default:
      return null;
  }
}

/**
 * Consume the launch start param ONCE and deep-link to the right screen.
 *
 * MOUNTED AT THE ROOT (`AppRouter`), not in the customer shell. An invite link
 * is opened by someone who is not a customer and often not yet a member of
 * anything — a business owner claiming their shop — so a hook that only ran
 * inside `ClientFrame` never fired for them. `/i/:token` is a public route, so
 * the redirect works before any persona is resolved.
 *
 * Mount it in exactly ONE place. Two call sites would race on the
 * sessionStorage guard below: whichever ran first would mark the param consumed
 * and the other would bail, silently breaking whichever deep link it owned.
 *
 * The guard makes this fire only on the initial open, never on later in-app
 * navigation (which would trap the user on the deep-linked screen).
 */
export function useStartParamRedirect(): void {
  const navigate = useNavigate();
  // At the root there is no `:locale` segment to read, so fall back to the
  // active i18n language — otherwise every customer deep link would resolve to
  // /uz and bounce a Russian user out of their language.
  const { locale } = useParams<{ locale: string }>();
  const { i18n } = useTranslation();
  const { pathname } = useLocation();

  useEffect(() => {
    const KEY = 'muddato.startParamConsumed';
    if (sessionStorage.getItem(KEY)) return;
    const raw = readStartParam();
    if (!raw) {
      sessionStorage.setItem(KEY, '1');
      return;
    }
    const active = locale ?? i18n.language;
    const lng: Locale = active?.startsWith('ru') ? 'ru' : 'uz';
    const target = startParamToPath(raw, lng);
    sessionStorage.setItem(KEY, '1');
    // `startParamToPath` is the single source of truth for which start params
    // are navigable at all — it returns null for anything it doesn't
    // recognise, so any non-null target is by construction a legitimate
    // destination for SOME kind the decoder knows about. Re-deriving that set
    // here as a hardcoded prefix allow-list is exactly what went stale: the
    // decoder gained `tenant_installment` and `billing` kinds and this list
    // was never widened, so they decoded correctly and then were silently
    // dropped. The only thing this hook still needs to enforce itself is the
    // loop guard — never bounce within the same path.
    //
    // This does NOT skip authorization. A `/tenant/*` or `/platform/*` target
    // reaches `PersonaGuard` (router.tsx) exactly like a manually-typed URL
    // would, and a customer-role session is redirected to their own persona
    // home before the tenant shell ever renders — the guard is the actual
    // authorization boundary, not this hook.
    if (target && target.split('?')[0] !== pathname) {
      navigate(target, { replace: true });
    }
    // run once on mount
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);
}

/** Where a captured `ref_<code>` (M-20) is held until something consumes it. */
const REFERRAL_STORAGE_KEY = 'muddato.referralCode';

/**
 * Capture the `?ref=` query param carried by a `ref_<code>` deep link
 * (`startParamToPath` above resolves it to `${me}/home?ref=<code>`) into
 * storage, so the code is not silently lost once the `home` route redirects
 * on to `my` and strips the query string.
 *
 * Best-effort like the sessionStorage guard above: storage can throw
 * (SecurityError) in a partitioned/blocked WebView, and a referral code is
 * not worth failing navigation over.
 */
export function captureReferralCode(search: string): void {
  const code = new URLSearchParams(search).get('ref');
  if (!code || !ID_RE.test(code)) return;
  try { localStorage.setItem(REFERRAL_STORAGE_KEY, code); } catch { /* storage blocked */ }
}

/** Read back a referral code captured above, if any. Does not clear it —
 *  the consumer decides when the code has actually been used. */
export function readReferralCode(): string | null {
  try { return localStorage.getItem(REFERRAL_STORAGE_KEY); } catch { return null; }
}
