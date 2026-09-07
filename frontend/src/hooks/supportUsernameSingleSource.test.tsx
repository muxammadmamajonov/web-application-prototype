// @vitest-environment jsdom
/**
 * MUDD-71 H3 — one answer to "what is the support handle?".
 *
 * The handle had three sources that disagreed:
 *   1. `lib/telegram.ts` — the build-time constant `SUPPORT_USERNAME`, imported
 *      directly by eight user-facing surfaces;
 *   2. `lib/linkify.tsx` — its own literal `https://t.me/muddatosupport`, in a
 *      DIFFERENT CASE from (1);
 *   3. `platform_settings.support` via `GET /contact/channels` — the only one an
 *      operator can change (Sozlamalar → Aloqa kanali), and the only one that
 *      exactly one surface (ProfilePage) read.
 *
 * So the settings form worked and changed nothing a user could see on seven of
 * eight screens. `useSupportUsername()` is now the only reader, with the
 * constant kept as the cold-cache/offline fallback.
 *
 * The source-level assertions below are the ones that actually stay true: a
 * render test only covers the screens someone remembered to render, while these
 * fail the moment a NEW surface reaches for the constant again.
 */
import { describe, expect, it, beforeEach, vi, afterEach } from 'vitest';
import { readFileSync, readdirSync, statSync } from 'node:fs';
import { join } from 'node:path';
import { renderHook, cleanup } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import type { ReactNode } from 'react';

// ── source-level: who is allowed to name the handle ─────────────────────────

const SRC = 'src';

function walk(dir: string, out: string[] = []): string[] {
  for (const name of readdirSync(dir)) {
    const p = join(dir, name);
    if (statSync(p).isDirectory()) walk(p, out);
    else if (/\.tsx?$/.test(p) && !/\.test\.tsx?$/.test(p)) out.push(p);
  }
  return out;
}

/** Strip comments — a doc block is allowed to NAME the old sources in order to
 *  explain why they are gone. The rule is about executable code.
 *
 *  The line-comment pattern requires the `//` NOT to follow a `:` — otherwise it
 *  eats the tail of every `https://…` literal, which is exactly what these
 *  assertions are looking for, and the test passes by seeing nothing. */
const code = (p: string) =>
  readFileSync(p, 'utf-8')
    .replace(/\/\*[\s\S]*?\*\//g, '')
    .replace(/(^|[^:'"`\\])\/\/.*$/gm, '$1');

/** The two modules that may mention the constant: the module that DEFINES it,
 *  and the hook that wraps it as a fallback. `linkify` may reference it as the
 *  placeholder token the locale strings embed. */
const CONSTANT_OWNERS = [
  join(SRC, 'lib', 'telegram.ts'),
  join(SRC, 'hooks', 'useSupportUsername.ts'),
  join(SRC, 'lib', 'linkify.tsx'),
];

const FILES = walk(SRC);

describe('the support handle has one source', () => {
  it('no component imports SUPPORT_USERNAME directly', () => {
    const offenders = FILES.filter(
      (p) => !CONSTANT_OWNERS.includes(p) && /\bSUPPORT_USERNAME\b/.test(code(p)),
    );
    expect(offenders).toEqual([]);
  });

  it('no module builds a support t.me URL from a literal handle', () => {
    // A bare `@MuddatoSupport` inside uz/ru copy is NOT an offence: that is the
    // placeholder token `linkifySupport` swaps for the configured handle, so the
    // prose already routes through the single source. What must not exist is a
    // second *link target* — a t.me URL that no setting can change.
    const offenders = FILES.filter(
      (p) => !CONSTANT_OWNERS.includes(p) && /t\.me\/[Mm]uddato[Ss]upport/.test(code(p)),
    );
    expect(offenders).toEqual([]);
  });

  it('linkify no longer carries its own lowercase support URL', () => {
    expect(code(join(SRC, 'lib', 'linkify.tsx'))).not.toMatch(/t\.me\/muddatosupport/);
  });
});

// ── behaviour: the stored value wins, the constant is the fallback ──────────

const channels = vi.hoisted(() => ({ current: undefined as unknown }));
vi.mock('@/lib/queries', () => ({ useSupportChannels: () => ({ data: channels.current }) }));

import { useSupportUsername, useSupportUrl } from './useSupportUsername';
import { SUPPORT_USERNAME } from '@/lib/telegram';

const wrap = ({ children }: { children: ReactNode }) => (
  <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
    {children}
  </QueryClientProvider>
);

beforeEach(() => { channels.current = undefined; });
afterEach(() => cleanup());

describe('useSupportUsername', () => {
  it('returns the operator-configured handle when platform settings have one', () => {
    channels.current = { support_username: 'MuddatoHelp', company_email: null };
    expect(renderHook(() => useSupportUsername(), { wrapper: wrap }).result.current).toBe('MuddatoHelp');
  });

  it('falls back to the build-time constant on a cold cache', () => {
    expect(renderHook(() => useSupportUsername(), { wrapper: wrap }).result.current).toBe(SUPPORT_USERNAME);
  });

  it('falls back rather than rendering an empty handle when the setting is blank', () => {
    channels.current = { support_username: '', company_email: null };
    expect(renderHook(() => useSupportUsername(), { wrapper: wrap }).result.current).toBe(SUPPORT_USERNAME);
  });

  it('builds the chat URL from the same source', () => {
    channels.current = { support_username: 'MuddatoHelp', company_email: null };
    expect(renderHook(() => useSupportUrl(), { wrapper: wrap }).result.current).toBe('https://t.me/MuddatoHelp');
  });
});
