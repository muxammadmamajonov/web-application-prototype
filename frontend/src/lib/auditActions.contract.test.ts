/**
 * Every audit action the backend records must be readable in both locales.
 *
 * THE BUG THIS PINS (found on a real device, 2026-08-08). The audit screen
 * translates an action through `audit.actions.<action>`, with the raw code as
 * the i18next fallback. That fallback is the whole problem: a missing key does
 * not throw, does not warn, and does not look broken to the developer who added
 * the action — it just shows an operator `tenant.impersonated_mutation` where a
 * sentence should be. Two actions had shipped that way.
 *
 * The two sides are different files in different languages, and nothing links
 * them: a backend author adds `audit.record(action="...")` and has no reason to
 * open a JSON bundle in the frontend. This test is that link.
 *
 * `tenant.impersonated_mutation` matters most of all — it is the record of a
 * platform admin acting inside somebody else's shop, which is exactly the entry
 * an operator reviewing an incident needs to be able to read.
 */
import { describe, expect, it } from 'vitest';
import { readFileSync, readdirSync, statSync } from 'node:fs';
import { resolve } from 'node:path';
import uz from '../i18n/locales/uz.json';
import ru from '../i18n/locales/ru.json';

const BACKEND_APP = resolve(__dirname, '../../../backend/app');

function pythonFiles(dir: string): string[] {
  const out: string[] = [];
  for (const name of readdirSync(dir)) {
    if (name === '__pycache__') continue;
    const full = resolve(dir, name);
    if (statSync(full).isDirectory()) { out.push(...pythonFiles(full)); continue; }
    if (name.endsWith('.py')) out.push(full);
  }
  return out;
}

/**
 * Every action string the backend records, read straight out of the source.
 *
 * Deliberately keyed off the `action=` keyword of the audit recorder rather
 * than off any dotted string literal: this must track what is ACTUALLY written
 * to the log, not everything that happens to look like an action.
 */
const BACKEND_ACTIONS: string[] = (() => {
  const found = new Set<string>();
  for (const file of pythonFiles(BACKEND_APP)) {
    const src = readFileSync(file, 'utf8');
    for (const m of src.matchAll(/\baction\s*=\s*["']([a-z_]+\.[a-z_]+)["']/g)) {
      found.add(m[1]);
    }
  }
  return [...found].sort();
})();

/** The screen's lookup: dots become underscores so the key does not collide
 *  with i18next's nesting separator. Mirrors AuditPage's `actionLabel`. */
const keyFor = (action: string) => action.replace(/\./g, '_');

// eslint-disable-next-line @typescript-eslint/no-explicit-any
const actionsIn = (bundle: any): Record<string, string> => bundle?.audit?.actions ?? {};

describe('the contract reaches both sides', () => {
  it('finds the backend audit actions', () => {
    // Guards against a silent pass if the recorder is renamed or moved: with an
    // empty list every assertion below is vacuously true.
    expect(BACKEND_ACTIONS.length).toBeGreaterThan(15);
    expect(BACKEND_ACTIONS).toContain('tenant.impersonated_mutation');
  });

  it('finds the translation bundles', () => {
    expect(Object.keys(actionsIn(uz)).length).toBeGreaterThan(15);
    expect(Object.keys(actionsIn(ru)).length).toBeGreaterThan(15);
  });
});

describe('every recorded action is translated', () => {
  it.each([['uz', uz], ['ru', ru]] as const)('%s has a label for each action', (lng, bundle) => {
    const have = actionsIn(bundle);
    const missing = BACKEND_ACTIONS.filter((a) => !have[keyFor(a)]);
    expect(missing,
      `these actions are recorded by the backend but have no ${lng} label:\n` +
      `${missing.join('\n')}\n` +
      'The audit screen falls back to the raw code, so an operator sees ' +
      '"tenant.impersonated_mutation" instead of a sentence — silently, with ' +
      `no warning. Add each to audit.actions in ${lng}.json.`,
    ).toEqual([]);
  });

  it('uz and ru cover exactly the same actions', () => {
    // A label in one bundle only means the other locale silently degrades to
    // the raw code for those users alone — the hardest version to notice.
    expect(Object.keys(actionsIn(uz)).sort()).toEqual(Object.keys(actionsIn(ru)).sort());
  });
});
