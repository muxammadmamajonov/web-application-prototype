/**
 * M-24 (MUDD-68) — three timestamps were formatted with the bare
 * `Date.prototype.toLocaleString()`, which reads the RUNTIME/ICU locale
 * (whatever the device or Telegram WebView happens to report), not the
 * app's own uz/ru selection. The app has its own `dateTime()` (composed
 * from `shortDate` + `timeOfDay`) for exactly this; these three bypassed it.
 */
import { describe, expect, it } from 'vitest';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';

const SRC = resolve(__dirname, '..');
const read = (rel: string) => readFileSync(resolve(SRC, rel), 'utf8');

const TOUCHED = [
  'routes/platform/AuditPage.tsx',
  'routes/tenant/_modals/InvitationLinkModal.tsx',
  'routes/platform/_modals/AdminInviteLinkModal.tsx',
];

describe('no retained timestamp bypasses the locale-aware formatter', () => {
  it.each(TOUCHED)('%s has no bare toLocaleString() call', (rel) => {
    expect(read(rel)).not.toMatch(/\.toLocaleString\(\)/);
  });

  it.each(TOUCHED)('%s formats via the app\'s dateTime() helper instead', (rel) => {
    expect(read(rel)).toMatch(/\bdateTime\(/);
  });
});
