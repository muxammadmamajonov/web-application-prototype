/**
 * One way to get a file out of the app and onto the user's device.
 *
 * Seven call sites each hand-rolled the same dance — fetch as a blob, mint an
 * object URL, click a synthetic anchor or `window.open` it, revoke later — and
 * every one of them fails the same way inside Telegram.
 *
 * WHY TELEGRAM BREAKS IT. The Mini App runs in an embedded WebView with no
 * download manager and no visible chrome. A `blob:` URL has an origin the
 * WebView will not hand to the OS, `<a download>` is ignored because there is
 * nowhere to download *to*, and `window.open` is intercepted. Nothing throws:
 * the user taps the button, and the file silently does not arrive. That is the
 * worst possible failure for "Download my data".
 *
 * WHAT ACTUALLY WORKS. Telegram will hand a real `https:` URL to the system
 * browser via `WebApp.openLink`, and the system browser knows how to download.
 * So the fix is not a cleverer blob — it is a server-hosted URL. `signedUrl`
 * below is that seam. When even that grant fails, the user is told the actual
 * reason (bot never started vs. a genuine send failure) rather than being
 * pointed at a "open it in a browser" instruction the Mini App gives them no
 * way to act on.
 *
 * Object URLs are NOT banned outright — `URL.createObjectURL` is still the right
 * tool for previewing a picked image before upload. What this module replaces is
 * using one as a *delivery* mechanism.
 */
import i18n from '@/i18n';
import { api } from './api';
import { apiErrorMessage, errorKind } from './errors';
import { toast } from './feedback';
import { reportError } from './sentry';
import { botDeepLink, isInsideTelegramClient, openExternalLink } from './telegram';

/**
 * `toast` takes a rendered string, not a key — same as `apiErrorMessage`.
 *
 * Used only when `downloadFile` is asked to save inside Telegram with no
 * signed URL and WITHOUT going through `downloadGranted` first — so no
 * Telegram-delivery attempt was made and no specific reason is known. The
 * copy therefore stays generic rather than naming a cause it did not check.
 */
function deliveryUnreachableNotice(): void {
  toast.warning(
    i18n.t(
      'download.deliveryUnreachable',
      'Faylni hozir yuborib bo‘lmadi — birozdan so‘ng qayta urinib ko‘ring',
    ),
  );
}

/** How long a minted object URL lives before it is revoked. Long enough for a
 *  PDF viewer to finish loading, short enough not to pin the blob in memory. */
const OBJECT_URL_TTL_MS = 60_000;

export interface DownloadRequest {
  /** API path returning the file body, e.g. `/tenant/installments/x/contract.pdf`. */
  readonly path: string;
  /** Filename the user should end up with, extension included. */
  readonly filename: string;
  /**
   * Absolute `https:` URL that serves this file without an Authorization header
   * — a short-lived signed link. Supply it and the download works everywhere,
   * Telegram included, because it can be handed straight to the system browser.
   */
  readonly signedUrl?: string;
  /** `true` to view in place (PDFs) rather than save. Ignored when saving is the
   *  only option the environment offers. */
  readonly inline?: boolean;
}

/** Outcome, so callers can react without re-deriving what happened. */
export type DownloadResult = 'saved' | 'opened' | 'delegated' | 'failed';

/**
 * Save a blob already in memory. Use when the bytes were generated client-side
 * (a CSV of rejected import rows); prefer {@link downloadFile} when they come
 * from the API, so the Telegram path is available.
 */
export function saveBlob(blob: Blob, filename: string): DownloadResult {
  if (isInsideTelegramClient()) {
    // No signed URL and no download manager. Say so — do not pretend.
    deliveryUnreachableNotice();
    return 'failed';
  }
  const url = URL.createObjectURL(blob);
  try {
    const a = document.createElement('a');
    a.href = url;
    a.download = filename;
    a.rel = 'noopener';
    document.body.appendChild(a);
    a.click();
    a.remove();
    return 'saved';
  } finally {
    setTimeout(() => URL.revokeObjectURL(url), OBJECT_URL_TTL_MS);
  }
}

/**
 * Fetch a file from the API and deliver it.
 *
 * Order matters: a signed URL is tried first because it is the only branch that
 * works in every environment. Everything after it is a fallback for browsers
 * that have a download manager.
 */
/**
 * Ask the server for a short-lived signed URL for one object (#10).
 *
 * This is what finally makes `signedUrl` above reachable in production. The
 * grant is authenticated and performs the real permission check; what comes
 * back is an absolute https URL that carries its own capability, so it can be
 * handed to Telegram's `openLink` — which refuses the `blob:` URLs every one
 * of these downloads used to build.
 *
 * Returns null rather than throwing: a failed grant should fall through to the
 * ordinary browser path, not replace a download with an error. The caller
 * cannot tell the difference, and on desktop the fallback works fine.
 *
 * A genuinely unexpected failure (not a plain 4xx from a missing/expired
 * object) is still reported — previously this branch swallowed every reason
 * into a bare `null`, which is what left this call undiagnosable when it was
 * the only fallback left and it, too, was failing.
 */
export type DownloadKind = 'contract' | 'payment_receipt' | 'invoice';

export async function grantDownloadUrl(
  kind: DownloadKind,
  id: string,
): Promise<string | null> {
  try {
    const r = await api.post('/downloads/grant', { kind, id });
    const url = (r.data as { url?: string })?.url;
    return typeof url === 'string' && url.startsWith('http') ? url : null;
  } catch (err) {
    if (errorKind(err) === 'server' || errorKind(err) === 'unknown') reportError(err);
    return null;
  }
}

/** What happened when the server tried to hand the file to the caller's own
 *  Telegram chat — kept distinct rather than flattened to a boolean, because
 *  each outcome needs a DIFFERENT thing said to the user afterwards. */
export type TelegramDeliveryOutcome = 'sent' | 'unavailable' | 'rate_limited' | 'failed';

/**
 * Ask the server to send the file into the caller's Telegram chat.
 *
 * `unavailable` means the person has never opened a chat with the bot —
 * Telegram refuses to let a bot message someone who hasn't (see
 * `document_delivery.py`'s `TelegramForbiddenError` handling) — and is by far
 * the most common reason this fails. `failed` is a genuine, likely-transient
 * send failure and is reported for visibility: this branch used to swallow
 * every one of these into `catch { return false }`, which is exactly why the
 * cause was previously impossible to tell from the client side.
 */
async function deliverToTelegram(kind: DownloadKind, id: string): Promise<TelegramDeliveryOutcome> {
  try {
    const r = await api.post('/downloads/grant', { kind, id, deliver: 'telegram' });
    const data = r.data as { sent?: boolean; state?: string };
    if (data?.sent === true) return 'sent';
    if (data?.state === 'UNAVAILABLE') return 'unavailable';
    return 'failed';
  } catch (err) {
    if (errorKind(err) === 'rateLimited') return 'rate_limited';
    reportError(err);
    return 'failed';
  }
}

/**
 * Download an object by asking for a signed URL first, then falling back.
 *
 * The one entry point callers should use for anything the server can sign.
 */
export async function downloadGranted(
  kind: DownloadKind,
  id: string,
  req: DownloadRequest,
): Promise<DownloadResult> {
  // Inside Telegram the file goes to the CHAT, not through a link. A chat
  // message persists, is searchable and survives the Mini App closing, which a
  // tab handed to the system browser does not — and it means no capability URL
  // is minted at all on the surface where one is least controllable.
  if (isInsideTelegramClient()) {
    const outcome = await deliverToTelegram(kind, id);
    if (outcome === 'sent') {
      toast.success(i18n.t('file.sentToTelegram', 'Fayl Telegramga yuborildi'));
      return 'delegated';
    }
    // `unavailable` (bot never started), `rate_limited` and `failed` all still
    // get a real shot at reaching the user: the signed-URL grant is a
    // SEPARATE authorization check with no dependency on Telegram delivery —
    // and no shared rate limiter — so it works regardless of why the chat
    // send was throttled or failed.
    const signedUrl = await grantDownloadUrl(kind, id);
    if (signedUrl) return downloadFile({ ...req, signedUrl });

    // Both paths are exhausted. What we say now has to match WHY: "open a
    // browser" (the old copy, for every reason alike) is not something the
    // user can act on from inside the Mini App — there is no browser button.
    // "open the bot" is, and it is also the actual fix the vast majority of
    // the time.
    if (outcome === 'unavailable') {
      toast.warning(
        i18n.t('download.telegramUnavailable', 'Fayl yuborish uchun avval botni ishga tushiring'),
        {
          label: i18n.t('download.openBot', 'Botni ochish'),
          onAction: () => openExternalLink(botDeepLink()),
        },
      );
    } else if (outcome === 'rate_limited') {
      toast.warning(i18n.t(
        'download.rateLimited',
        'Juda ko‘p so‘rov yuborildi — birozdan so‘ng qayta urinib ko‘ring',
      ));
    } else {
      deliveryUnreachableNotice();
    }
    return 'failed';
  }

  // On the web the authenticated client fetches the bytes and saves them — no
  // URL is handed out at all, so there is no link to leak, forward or replay.
  return downloadFile(req);
}

export async function downloadFile(req: DownloadRequest): Promise<DownloadResult> {
  // 1. Signed, server-hosted, no auth header needed → works everywhere.
  if (req.signedUrl) {
    openExternalLink(req.signedUrl);
    return 'delegated';
  }

  // 2. Inside Telegram without one, there is no path that reaches the filesystem.
  //    Fetching the bytes just to discard them would waste the user's data.
  //    (Reachable only when a caller uses `downloadFile` directly rather than
  //    `downloadGranted` above, so no Telegram-delivery attempt — and no
  //    specific reason — exists yet.)
  if (isInsideTelegramClient()) {
    deliveryUnreachableNotice();
    return 'failed';
  }

  // 3. Ordinary browser: fetch through the authenticated client and save.
  try {
    const res = await api.get(req.path, { responseType: 'blob' });
    const blob = res.data as Blob;

    if (req.inline) {
      const url = URL.createObjectURL(blob);
      window.open(url, '_blank', 'noopener');
      setTimeout(() => URL.revokeObjectURL(url), OBJECT_URL_TTL_MS);
      return 'opened';
    }
    return saveBlob(blob, req.filename);
  } catch (err) {
    toast.error(apiErrorMessage(err));
    return 'failed';
  }
}

/**
 * Build a filesystem-safe filename.
 *
 * Contract and receipt names interpolate tenant-supplied text (a shop or
 * customer name), which may contain `/`, `:` or a newline. Windows rejects
 * those, and a leading dot hides the file on macOS — so the user's download
 * either fails or vanishes.
 */
export function safeFilename(base: string, extension: string): string {
  // Codepoint filter rather than a control-character regex: eslint's
  // no-control-regex is right that such a class is almost always a mistake, and
  // spelling the intent out is clearer than an escape soup nobody re-reads.
  const stripped = [...base.normalize('NFKD')]
    .map((ch) => {
      const c = ch.codePointAt(0) ?? 0;
      // Tab, LF and CR are whitespace: fold to a space so two words do not run
      // together ("Ali\nVali" must not become "AliVali").
      if (c === 9 || c === 10 || c === 13) return ' ';
      // Every other control character is meaningless in a filename.
      if (c < 0x20 || c === 0x7f) return '';
      return ch;
    })
    .join('');

  const cleaned = stripped
    .replace(/[/\\?%*:|"<>]/g, '-')   // reserved on Windows
    .replace(/\s+/g, ' ')
    .trim()
    .replace(/^\.+/, '')              // no accidental hidden files
    .slice(0, 80)
    .trim()
    .replace(/[.\s]+$/, '');          // Windows drops a trailing dot or space

  const ext = extension.startsWith('.') ? extension : `.${extension}`;
  return `${cleaned || 'muddato'}${ext}`;
}

