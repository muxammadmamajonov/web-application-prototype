/**
 * The invite link the BACKEND mints must be one this module can decode (#3, #12).
 *
 * The business-owner invitation was once broken by a one-word disagreement
 * nothing could catch: the backend built `?startapp=inv_<token>` and this module
 * decoded only `join_`. On a modern Telegram client `startapp=` opens the Mini
 * App directly and the bot's /start handler never fires — so the owner-claim
 * link resolved to null and the owner landed on a generic screen with the token
 * still unused in the database. Older clients fell back to the bot, which DID
 * match, which is why it looked intermittent rather than broken.
 *
 * #12 replaced the prefixed token with a bare 6-character code, which moves the
 * agreement from a prefix to an ALPHABET and a LENGTH — more to disagree about,
 * not less. So both are read out of the backend source here rather than
 * restated: if someone widens `CODE_ALPHABET` or raises `CODE_LEN` on one side,
 * these fail instead of silently dropping every link built from the new symbols.
 *
 * Same idiom as `leadContact.test.ts` (email grammar) and
 * `importLimit.contract.test.ts` (upload ceiling).
 */
import { describe, expect, it } from 'vitest';
import { readFileSync, readdirSync } from 'node:fs';
import { resolve } from 'node:path';
import { startParam, startParamToPath } from './deepLinks';

const BACKEND = resolve(__dirname, '../../../../backend/app');
const LEGACY_TOKEN = 'Ab3dEf9hJk2mNp5qRs8tUv1wXy4z';

function backendSource(rel: string): string {
  return readFileSync(resolve(BACKEND, rel), 'utf8');
}

const codes = backendSource('core/invite_codes.py');

/** The alphabet and length the backend actually mints from. */
function backendCodeSpec(): { alphabet: string; len: number } {
  const a = codes.match(/^LEGACY_ALPHABET = "([^"]+)"/m);
  const l = codes.match(/^LEGACY_CODE_LEN = (\d+)/m);
  expect(a, 'LEGACY_ALPHABET not found in backend/app/core/invite_codes.py').toBeTruthy();
  expect(l, 'LEGACY_CODE_LEN not found in backend/app/core/invite_codes.py').toBeTruthy();
  return { alphabet: a![1], len: Number(l![1]) };
}

describe('the credential format both sides must agree on', () => {
  /** The byte count the backend mints from. */
  function backendCredentialBytes(): number {
    const m = codes.match(/^CREDENTIAL_BYTES = (\d+)/m);
    expect(m, 'CREDENTIAL_BYTES not found in backend/app/core/invite_codes.py').toBeTruthy();
    return Number(m![1]);
  }

  it('decodes a credential of the length the backend actually mints', () => {
    // 3 bytes → 4 url-safe characters, and `secrets.token_urlsafe` does not pad.
    const len = Math.ceil((backendCredentialBytes() * 8) / 6);
    const sample = 'aZ9_-'.repeat(20).slice(0, len);
    expect(startParamToPath(sample, 'uz'),
      `backend mints ${len}-char credentials but this module does not decode them`,
    ).toBe(`/i/${sample}`);
  });

  it('passes a credential through with its case intact', () => {
    // Case-significant: the verifier is computed over the exact string.
    const len = Math.ceil((backendCredentialBytes() * 8) / 6);
    const mixed = 'aB'.repeat(40).slice(0, len);
    expect(startParamToPath(mixed, 'uz')).toBe(`/i/${mixed}`);
  });

  it('still reads a kind-prefixed link rather than mistaking it for one', () => {
    const uuid = '11111111-2222-3333-4444-555555555555';
    expect(startParamToPath(`inst_${uuid}`, 'uz')).toBe(`/uz/me/installments/${uuid}`);
  });
});

describe('the retired code format both sides must still agree on', () => {
  const { alphabet, len } = backendCodeSpec();

  it('decodes a code built from every symbol the backend can mint', () => {
    // Slide a window across the whole alphabet so no symbol goes untested —
    // a regex that omitted, say, `Z` would still pass a single fixed sample.
    for (let i = 0; i < alphabet.length; i++) {
      const code = Array.from({ length: len }, (_, k) => alphabet[(i + k) % alphabet.length]).join('');
      expect(startParamToPath(code, 'uz'),
        `backend can mint "${code}" but this module does not decode it`,
      ).toBe(`/i/${code}`);
    }
  });

  it('decodes it whatever casing the recipient typed', () => {
    const code = alphabet.slice(0, len);
    expect(startParamToPath(code.toLowerCase(), 'uz')).toBe(`/i/${code}`);
  });

  it('refuses a symbol the backend deliberately excludes', () => {
    // I/O/0/1 are absent from the alphabet so a code cannot be mistyped into a
    // different valid one. Accepting them here would reopen that.
    for (const bad of ['I', 'O', '0', '1']) {
      expect(alphabet).not.toContain(bad);
      const code = alphabet.slice(0, len - 1) + bad;
      expect(startParamToPath(code, 'uz'), `"${code}" must not decode as a code`).toBeNull();
    }
  });

  it('refuses the wrong length', () => {
    expect(startParamToPath(alphabet.slice(0, len - 1), 'uz')).toBeNull();
    expect(startParamToPath(alphabet.slice(0, len + 1), 'uz')).toBeNull();
  });

  it('agrees with the builder this module exposes', () => {
    const code = alphabet.slice(0, len);
    expect(startParam.invite(code)).toBe(code);
  });
});

describe('the links the backend emits', () => {
  it('are startapp deep links, not /start ones', () => {
    // `start=` would need the bot handler to fire; `startapp=` opens the Mini
    // App directly, which is the path that was broken.
    expect(codes).toMatch(/\?startapp=/);
  });

  it('carry the bare code — no prefix', () => {
    const m = codes.match(/startapp=\{([a-z_]+)\}/);
    expect(m, 'could not find the deep-link builder').toBeTruthy();
    expect(m![1], 'the deep link must interpolate the code alone').toBe('code');
  });

  it('are all built by the shared module', () => {
    // #12 asked for centralised invite logic. Four hand-rolled copies of this
    // f-string are what let the encoder and the decoder drift apart, so the
    // check is that no OTHER backend module builds one.
    //
    // Scoped to INVITE links on purpose. `api/platform/referrals.py` builds
    // `?startapp=ref_<code>` and `services/notifications.py` takes an
    // already-encoded start param — different destinations with their own
    // grammars, and folding them in here would only invite a false alarm.
    const INVITE_LINK = /\?startapp=(inv_|join_|\{(inv\.)?(token|code)\})/;
    const offenders: string[] = [];
    const walk = (dir: string) => {
      for (const e of readdirSync(dir, { withFileTypes: true })) {
        const p = resolve(dir, e.name);
        if (e.isDirectory()) { walk(p); continue; }
        if (!e.name.endsWith('.py')) continue;
        const rel = p.slice(BACKEND.length + 1);
        if (rel === 'core/invite_codes.py') continue;   // the one legitimate builder
        if (INVITE_LINK.test(readFileSync(p, 'utf8'))) offenders.push(rel);
      }
    };
    walk(BACKEND);
    expect(offenders,
      `these build an invite deep link directly instead of calling invite_codes.deep_link:\n${offenders.join('\n')}`,
    ).toEqual([]);
  });
});

describe("the bot's /start fallback agrees too", () => {
  const bot = backendSource('bot/main.py');

  it('accepts the bare codes we mint now', () => {
    expect(bot).toMatch(/invite_codes\.looks_like_code\(/);
  });

  it('still accepts the prefixes already sitting in chats', () => {
    expect(bot).toMatch(/invite_codes\.LEGACY_PREFIXES/);
  });
});

describe('legacy links keep working', () => {
  // Invitations live up to 30 days, so links minted before #12 must resolve
  // until the last one has expired.
  it.each(['inv', 'join'])('%s_<token> still resolves to the public join page', (prefix) => {
    expect(startParamToPath(`${prefix}_${LEGACY_TOKEN}`, 'uz')).toBe(`/i/${LEGACY_TOKEN}`);
  });

  it('passes a mixed-case legacy token through untouched', () => {
    // Upper-casing it would turn a miss into a wrong hit: the old alphabet was
    // mixed-case, so two tokens could differ only by case.
    const path = startParamToPath(`inv_${LEGACY_TOKEN}`, 'uz');
    expect(path).toBe(`/i/${LEGACY_TOKEN}`);
    expect(path).not.toBe(`/i/${LEGACY_TOKEN.toUpperCase()}`);
  });

  it('is locale-independent — the join page is public and unprefixed', () => {
    // An invitee has no locale preference stored yet; the page must not 404
    // behind a language segment they never chose.
    expect(startParamToPath(`inv_${LEGACY_TOKEN}`, 'ru')).toBe(`/i/${LEGACY_TOKEN}`);
  });

  it('still refuses a malformed token', () => {
    expect(startParamToPath('inv_../../etc/passwd', 'uz')).toBeNull();
    expect(startParamToPath('inv_', 'uz')).toBeNull();
  });
});

describe('the other destinations are untouched', () => {
  it('still decode', () => {
    expect(startParamToPath('pay_abc', 'uz')).toBe('/uz/me/installments/abc/pay');
    expect(startParamToPath('ref_XY12', 'uz')).toBe('/uz/me/home?ref=XY12');
    expect(startParamToPath('nonsense_1', 'uz')).toBeNull();
  });

  it('named keywords win over the code shape', () => {
    // `profile` is 7 chars so it cannot collide today, but the ordering is what
    // guarantees a future 6-letter keyword is not swallowed as an invite code.
    expect(startParamToPath('home', 'uz')).toBe('/uz/me/home');
    expect(startParamToPath('profile', 'uz')).toBe('/uz/me/profile');
  });
});
