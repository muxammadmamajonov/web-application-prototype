/**
 * No new hand-rolled blob downloads (#10).
 *
 * Every download in the product used to fetch bytes with the auth header into a
 * `blob:` URL and hand that to `Telegram.WebApp.openLink` or an `<a download>`.
 * Telegram refuses a `blob:` origin, so for the Mini App — the primary way this
 * product is used — those buttons did nothing at all, silently. There was no
 * error, no toast, no console entry: the user pressed "Download contract" and
 * the app simply ignored them.
 *
 * `lib/download.ts` is now the only place allowed to touch `createObjectURL`,
 * and it prefers a server-signed https URL that Telegram can actually open.
 * This test stops the pattern coming back one page at a time, which is exactly
 * how it spread: each call site was three plausible lines.
 *
 * As with the other guards here, the allowlist only shrinks.
 */
import { describe, expect, it } from 'vitest';
import { readdirSync, readFileSync } from 'node:fs';
import { resolve } from 'node:path';

const SRC = resolve(__dirname, '..');

/** Object URLs that are a PREVIEW, not a delivery — these are correct. */
const PREVIEW_OK = new Set([
  // The one legitimate owner: it builds the object URL AND revokes it safely,
  // and only as the desktop fallback after a signed URL was unavailable.
  'lib/download.ts',
  // Client-side image handling before upload — never handed to the OS.
  'lib/image.ts',
  'routes/client/ReceiptUploadPage.tsx',
]);

/**
 * Still hand-rolling delivery. Each should move to `downloadGranted` /
 * `saveBlob`, then be deleted from here. Do not add to it.
 */
const NOT_YET_MIGRATED = new Set([
  // Reports are withdrawn by #15 (`REPORTS_ENABLED === false`) and the section
  // is unrouted, so this is unreachable today. It keeps its blob until Reports
  // return, at which point it needs a signed `report` kind on the backend.
  'lib/queries/tenant.ts',
  // An inline <iframe src> preview of an unsaved template draft. Not a
  // download, and there is no persisted object for the server to sign.
  'routes/tenant/SettingsPage.tsx',
]);

function sourceFiles(dir = SRC, prefix = ''): Array<{ rel: string; text: string }> {
  const out: Array<{ rel: string; text: string }> = [];
  for (const e of readdirSync(dir, { withFileTypes: true })) {
    const rel = prefix ? `${prefix}/${e.name}` : e.name;
    if (e.isDirectory()) { out.push(...sourceFiles(resolve(dir, e.name), rel)); continue; }
    if (!/\.(ts|tsx)$/.test(e.name) || e.name.includes('.test.')) continue;
    out.push({ rel, text: readFileSync(resolve(dir, e.name), 'utf8') });
  }
  return out;
}

const FILES = sourceFiles();
const withObjectUrl = FILES.filter((f) => f.text.includes('createObjectURL')).map((f) => f.rel);

describe('the sweep reaches the source', () => {
  it('scans a meaningful tree', () => {
    expect(FILES.length).toBeGreaterThan(100);
    expect(FILES.some((f) => f.rel.startsWith('routes/'))).toBe(true);
  });
});

describe('object URLs are confined', () => {
  it('no file builds one outside the helper without being tracked', () => {
    const fresh = withObjectUrl.filter((r) => !PREVIEW_OK.has(r) && !NOT_YET_MIGRATED.has(r));
    expect(fresh,
      `these build a blob: URL by hand:\n${fresh.join('\n')}\n` +
      'Use downloadGranted() for anything the server can sign, or saveBlob() for ' +
      'bytes generated on the client. A blob: URL is silently discarded by Telegram.',
    ).toEqual([]);
  });

  it('the migration list does not outlive the files on it', () => {
    const stale = [...NOT_YET_MIGRATED].filter((r) => !withObjectUrl.includes(r));
    expect(stale, `already migrated — remove from NOT_YET_MIGRATED:\n${stale.join('\n')}`)
      .toEqual([]);
  });
});

describe('the customer-facing downloads are migrated', () => {
  const file = (rel: string) => FILES.find((f) => f.rel === rel)?.text ?? '';

  it.each([
    ['routes/client/InstallmentDetailPage.tsx', 'contract + payment receipt PDFs'],
    ['lib/contractPdf.ts', 'the tenant contract PDF'],
  ])('%s asks for a signed URL (%s)', (rel) => {
    expect(file(rel)).toContain('downloadGranted');
    expect(file(rel)).not.toContain('createObjectURL');
  });

  it('the GDPR export uses saveBlob rather than a detached anchor', () => {
    // It has no server-side id to sign, so a blob is correct here — but the
    // hand-rolled version never appended the anchor and revoked the URL on the
    // next line, racing the download.
    const t = file('routes/client/PrivacyPage.tsx');
    expect(t).toContain('saveBlob');
    expect(t).not.toContain('createObjectURL');
    expect(t).not.toContain('document.createElement');
  });
});
