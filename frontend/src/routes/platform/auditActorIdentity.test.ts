/**
 * M-30 (MUDD-62), partial — the audit log's desktop table already resolves
 * `actor_name` (server-side, falling back to the id only when the actor no
 * longer exists — see the `AuditRow.actor_name` comment in
 * `lib/queries/platform.ts`). Two other surfaces on the same page never
 * picked that up:
 *
 *  - the mobile card's `<Avatar>` fed it the raw `actor_user_id`, so the
 *    avatar's initials were derived from a UUID instead of a name;
 *  - the detail modal printed `detail.actor_user_id` and `detail.tenant_id`
 *    verbatim, with no name and no fallback-labelling, unlike every other id
 *    shown elsewhere on this same page (e.g. `entity_id.slice(0, 8)`,
 *    `tenantName()` for the filter chips).
 *
 * This guard pins both surfaces to the resolved name, id only as fallback.
 */
import { describe, expect, it } from 'vitest';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';

const SRC = readFileSync(resolve(__dirname, 'AuditPage.tsx'), 'utf8');

describe('audit log shows actor/tenant names, not raw ids (M-30)', () => {
  it('mobile card avatar derives from the resolved actor name, id as fallback', () => {
    expect(SRC).toMatch(/<Avatar\s+name=\{a\.actor_name \?\? a\.actor_user_id \?\? '\?'\}/);
  });

  it('detail modal actor row prefers the resolved name over the raw id', () => {
    expect(SRC).toMatch(/detail\.actor_name/);
    // The bare, unlabelled raw id must no longer be the only thing rendered.
    expect(SRC).not.toMatch(/<span className="font-mono text-caption">\{detail\.actor_user_id\}<\/span>/);
  });

  it('detail modal tenant row resolves through the same tenantName() helper as the filter chips', () => {
    expect(SRC).toMatch(/tenantName\(detail\.tenant_id\)/);
    expect(SRC).not.toMatch(/<span className="font-mono text-caption">\{detail\.tenant_id\}<\/span>/);
  });
});
