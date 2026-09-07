/**
 * The UI's role rules must say what the backend's role rules say.
 *
 * THE DUPLICATE (source-of-truth audit, 2026-08-11). `owner || manager` was
 * written out inline wherever a screen needed it — twice in `InstallmentsPage`,
 * once in `persona.ts` — while the actual authority, `_require_owner_or_manager`
 * in `backend/app/api/tenant/_shared.py`, lived somewhere else entirely. Three
 * copies of one permission rule are three places to forget when it changes, and
 * a permission rule that drifts silently tends to drift in the direction that
 * GRANTS: the UI shows a clerk a button, the clerk presses it, the API answers
 * 403, and nobody learns anything except that the product is broken.
 *
 * The rule now lives once (`canManageTenant`). This test reads the BACKEND
 * SOURCE and asserts the two agree — the same technique the other
 * `*.contract.test.ts` files in this folder use, so a change on either side that
 * is not made on both fails here rather than in production.
 *
 * It deliberately parses the Python rather than restating it: a hand-copied
 * expectation is exactly the fourth copy this test exists to prevent.
 */
import { describe, expect, it } from 'vitest';
import { readFileSync } from 'node:fs';
import { canManageTenant } from '@/store/persona';
import type { TenantRole } from '@/types';

const SHARED = '../backend/app/api/tenant/_shared.py';
const src = readFileSync(SHARED, 'utf-8');

/** The roles the backend's `_require_owner_or_manager` lets through. */
function backendManageRoles(): Set<string> {
  // `if membership.role not in (TenantRole.owner, TenantRole.manager):`
  const m = src.match(
    /def _require_owner_or_manager[\s\S]*?membership\.role not in \(([^)]*)\)/,
  );
  if (!m) throw new Error('could not locate _require_owner_or_manager in ' + SHARED);
  return new Set(
    m[1].split(',').map((s) => s.trim().replace('TenantRole.', '')).filter(Boolean),
  );
}

const ALL_ROLES: TenantRole[] = ['owner', 'manager', 'sales'];

describe('the manage-records rule matches the backend', () => {
  it('finds the backend rule at all', () => {
    // If this ever fails, the guard below is silently vacuous — which is worse
    // than no guard, because it reads as coverage.
    expect(backendManageRoles().size).toBeGreaterThan(0);
  });

  it('permits exactly the roles the API permits', () => {
    const backend = backendManageRoles();
    for (const role of ALL_ROLES) {
      expect(
        canManageTenant(role),
        `frontend and backend disagree about "${role}" — the UI would offer an ` +
        'action the API refuses (or hide one it allows)',
      ).toBe(backend.has(role));
    }
  });

  it('treats "no active role" as no permission', () => {
    // A null role is "we do not know which shop this is yet". Defaulting that to
    // permitted is how a gate becomes a decoration.
    expect(canManageTenant(null)).toBe(false);
  });
});

describe('the rule is stated once', () => {
  const files = import.meta.glob('/src/**/*.{ts,tsx}', { query: '?raw', import: 'default', eager: true }) as Record<string, string>;

  it('no screen restates owner-or-manager inline', () => {
    const offenders: string[] = [];
    for (const [path, text] of Object.entries(files)) {
      if (path.includes('.test.') || path.endsWith('/store/persona.ts')) continue;
      text.split('\n').forEach((line, i) => {
        if (/===\s*'owner'\s*\|\|[^|]*===\s*'manager'/.test(line)) {
          offenders.push(`${path}:${i + 1}  ${line.trim().slice(0, 90)}`);
        }
      });
    }
    expect(
      offenders,
      'these restate the permission rule instead of calling canManageTenant():\n' +
      offenders.join('\n'),
    ).toEqual([]);
  });
});
