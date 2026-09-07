/**
 * A stored phone must never be rendered raw.
 *
 * THE BUG THIS PINS (found on a real device, 2026-08-08). Since r96 the backend
 * stores ONE canonical form, `+998888888580`, because that is what makes "is
 * this number already taken?" answerable. That form is for machines: people
 * read `+998 88 888 85 80`.
 *
 * When canonical storage shipped, the display sites were swept onto
 * `displayPhone()` — but only the tenant, staff and client pages. The PLATFORM
 * tenant list and detail drawer were missed, so opening a business in the
 * Mini App showed an unspaced wall of digits. It was invisible in every test
 * and every mock, because the mocks still carry pre-canonicalisation strings
 * with spaces already in them; only real production data exposes it.
 *
 * A sweep is the right shape here: the failure is "somebody added a new place
 * that shows a phone", which no single component test can anticipate.
 */
import { describe, expect, it } from 'vitest';
import { readdirSync, readFileSync } from 'node:fs';
import { resolve } from 'node:path';

const SRC = resolve(__dirname, '..');

function sourceFiles(dir = SRC, prefix = ''): Array<{ rel: string; text: string }> {
  const out: Array<{ rel: string; text: string }> = [];
  for (const e of readdirSync(dir, { withFileTypes: true })) {
    const rel = prefix ? `${prefix}/${e.name}` : e.name;
    if (e.isDirectory()) { out.push(...sourceFiles(resolve(dir, e.name), rel)); continue; }
    if (!e.name.endsWith('.tsx') || e.name.includes('.test.')) continue;
    out.push({ rel, text: readFileSync(resolve(dir, e.name), 'utf8') });
  }
  return out;
}

const FILES = sourceFiles();

/** `{something.phone}` or `{something.contact_phone}` rendered as JSX text. */
const RAW_RENDER = /\{[a-zA-Z_$][\w$.?]*\.(?:contact_phone|secondary_phone|phone)\s*(?:\?\?[^}]*)?\}/;

/**
 * Places a raw phone is CORRECT and must stay that way.
 * `tel:` hrefs want the unspaced number — a dialer should not be handed
 * spaces — and search/filter predicates compare against raw stored values.
 */
function isAllowedContext(line: string): boolean {
  return line.includes('tel:')            // dialer links
    // `errors.phone` / `errors.secondary_phone` are validation MESSAGES keyed
    // by field name — text, not a number to format.
    || /\berrors?\.[\w$]*phone/.test(line)
    || line.includes('displayPhone')      // already formatted
    || line.includes('formatUzPhone')
    || line.includes('phoneOrEmpty')
    || line.includes('.includes(')        // search predicates
    || line.includes('toLowerCase()')
    || line.includes('value=')            // controlled inputs
    || line.includes('onChange');
}

describe('the phone sweep reaches the source', () => {
  it('scans a meaningful tree', () => {
    expect(FILES.length).toBeGreaterThan(100);
  });
});

describe('every rendered phone goes through displayPhone', () => {
  it('no component prints a stored phone raw', () => {
    const offenders: string[] = [];
    for (const { rel, text } of FILES) {
      text.split('\n').forEach((line, i) => {
        if (!RAW_RENDER.test(line)) return;
        if (isAllowedContext(line)) return;
        offenders.push(`${rel}:${i + 1}  ${line.trim().slice(0, 96)}`);
      });
    }
    expect(offenders,
      `these render the CANONICAL stored phone straight to the screen:\n${offenders.join('\n')}\n` +
      'Since r96 the backend stores +998888888580, not "+998 88 888 85 80" — so this ' +
      'shows the user an unspaced wall of digits. Wrap it in displayPhone(). ' +
      '`tel:` hrefs are exempt and should stay raw.',
    ).toEqual([]);
  });

  it('form state seeded from the server goes through the mask', () => {
    // A SECOND variant of the same bug, found on the real client after the
    // first fix: `phone: me.phone ?? ''` drops the canonical value straight
    // into a controlled input. PhoneInput formats on CHANGE, not on mount, so
    // the user saw their own number as an unspaced run of digits until they
    // typed in the field. The sweep above deliberately exempts `value=` lines,
    // which is correct for the render but blind to the seed — hence this.
    const SEED = /^\s*(?:phone|secondary_phone|contact_phone)\s*:\s*(?!.*(?:phoneOrEmpty|formatUzPhone|displayPhone))[a-zA-Z_$][\w$.?]*\.[\w$]*phone/;
    const offenders: string[] = [];
    for (const { rel, text } of FILES) {
      text.split('\n').forEach((line, i) => {
        if (!SEED.test(line)) return;
        offenders.push(`${rel}:${i + 1}  ${line.trim().slice(0, 90)}`);
      });
    }
    expect(offenders,
      `these seed a form field with the raw stored phone:\n${offenders.join('\n')}\n` +
      'Wrap it in phoneOrEmpty() so the field opens already masked.',
    ).toEqual([]);
  });

  it('a whole-object form seed still masks its phone', () => {
    // A THIRD variant, found on the real client after the first two fixes. The
    // sweep above and the `phone:`-key guard both look for a phone being
    // assigned by name. Tenant settings seeded its entire form from the server
    // object at once — `useState(data.profile)` — so there was no `phone:` key
    // anywhere for either check to see, and "Asosiy telefon" rendered the raw
    // canonical `+998888888580`. The same page's Profil tab masked correctly,
    // which is what made it look like the fix had simply not shipped.
    const text = FILES.find((f) => f.rel === 'routes/tenant/SettingsPage.tsx')?.text ?? '';
    expect(text, 'routes/tenant/SettingsPage.tsx not found — re-point this test').toBeTruthy();
    expect(
      /contact_phone:\s*phoneOrEmpty\(/.test(text),
      'tenant SettingsPage seeds its form from the server object wholesale; ' +
      'contact_phone must be passed through phoneOrEmpty() there or the owner ' +
      'sees an unspaced wall of digits until they type in the field.',
    ).toBe(true);
  });

  it('the two platform surfaces that were missed stay fixed', () => {
    // Found on a real device: the Mini App platform view showed +998888888580.
    for (const rel of ['routes/platform/TenantsPage.tsx',
                       'routes/platform/_modals/TenantDetailDrawer.tsx']) {
      const text = FILES.find((f) => f.rel === rel)?.text ?? '';
      expect(text, `${rel} not found — re-point this test`).toBeTruthy();
      expect(text, `${rel} stopped formatting its phone`).toContain('displayPhone');
    }
  });
});
