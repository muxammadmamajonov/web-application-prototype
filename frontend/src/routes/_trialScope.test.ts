/**
 * Phase 3 remediation R-08 — no retained MVP control may start a trial.
 *
 * Phase 2 keeps trial lifecycle and trial configuration at LATER. The first
 * Phase 3 pass removed the *symptoms* — the trial banner and the trial-days
 * setting — but left the two controls that actually put a shop onto a trial:
 * the plan selector when provisioning a tenant, and the platform-wide default
 * plan. Either would start a trial lifecycle the MVP does not run.
 *
 * These assertions are structural on purpose. The controls are `Select`
 * options built from a scope flag, so what matters is that the flag gates them
 * — and that the gate is the shared `V2_SURFACES_ENABLED`, not a local literal
 * someone can flip by accident.
 */
import { describe, expect, it } from 'vitest';
import { readFileSync } from 'node:fs';
import { V2_SURFACES_ENABLED } from './_scope';

const read = (p: string) => readFileSync(p, 'utf-8');
/** Source with comments stripped — assertions are about shipped behaviour. */
const code = (p: string) =>
  read(p).replace(/\/\*[\s\S]*?\*\//g, '').replace(/\/\/.*$/gm, '');

const CREATE_TENANT = 'src/routes/platform/_modals/CreateTenantModal.tsx';
const PLATFORM_SETTINGS = 'src/routes/platform/SettingsPage.tsx';
const TENANT_DRAWER = 'src/routes/platform/_modals/TenantDetailDrawer.tsx';

describe('the MVP scope flag is off', () => {
  it('so every gate below is actually closed in the shipped build', () => {
    expect(V2_SURFACES_ENABLED).toBe(false);
  });
});

describe('provisioning a tenant cannot start a trial', () => {
  it('offers the trial plan only behind the scope flag', () => {
    const src = code(CREATE_TENANT);
    const trialOption = src.match(/.*value: 'trial'.*/)?.[0] ?? '';
    expect(trialOption, 'a trial option exists in source (retained for V2)').toBeTruthy();
    expect(trialOption, 'and it is gated').toContain('V2_SURFACES_ENABLED');
  });

  it('seeds the plan from the catalogue, never from a literal', () => {
    // These two assertions used to pin `useState<string>('lite')` and
    // `setPlan('lite')` — and that is how the defect they were meant to prevent
    // survived. `lite` stopped existing in R36 when plan codes became
    // owner-chosen slugs, so the "real plan, not trial" they asserted had itself
    // become an unreal plan, and every shop created without touching the Select
    // was stamped with a code no `plans` row matches: no limits, no price, no
    // subscription row. A literal cannot track a catalogue the operator edits on
    // the next page over, so what is pinned now is that there is no literal.
    const src = code(CREATE_TENANT);
    expect(src, 'the seed must be empty until the catalogue answers')
      .toContain("useState<string>('')");
    expect(src).not.toContain("useState<string>('trial')");
    expect(src, 'and the default comes from the fetched options')
      .toMatch(/setPlan\(planOptions\[0\]\.value\)/);
  });

  it('resets to no plan between creations, not to a literal', () => {
    // A reset to `trial` would re-arm the removed option for the next tenant; a
    // reset to any hardcoded code re-arms the rot above. Empty re-runs the
    // catalogue seed.
    const src = code(CREATE_TENANT);
    expect(src).toContain("setPlan('')");
    expect(src).not.toContain("setPlan('trial')");
  });
});

describe('the platform-wide default plan cannot be trial', () => {
  it('excludes trial from the default-plan options while V2 is off', () => {
    // These two assertions used to pin a hardcoded `value: 'trial'` option and a
    // `stored === 'trial' ? 'lite'` coercion. That literal option list was the
    // H2 defect: plan codes became owner-chosen slugs in R36, the catalogue moved
    // to starter/business/pro/enterprise/flex, and the form still offered
    // Lite/Pro/Custom — of which only Pro existed. The options now come from the
    // live catalogue, so what has to be pinned is the INVARIANT the old literals
    // happened to provide, not the literals themselves.
    const src = code(PLATFORM_SETTINGS);
    expect(src, 'options must be derived from the catalogue, not a literal list')
      .toContain('plansQuery.data');
    const line = src.split('\n').find((l) => l.includes("p.code !== 'trial'")) ?? '';
    expect(line, 'a trial plan in the catalogue must still be filtered out').toBeTruthy();
    expect(line, 'and that filter is what the scope flag opens').toContain('V2_SURFACES_ENABLED');
  });

  it('never silently rewrites a stored default plan', () => {
    // The old coercion rewrote a legacy `trial` row to `lite` for display. With
    // `lite` itself now absent from the catalogue that behaviour would invent a
    // second wrong answer, so the stored code is shown verbatim and an unknown
    // one is labelled as missing — the operator must see what new shops are
    // actually being stamped with.
    const src = code(PLATFORM_SETTINGS);
    expect(src).not.toContain("? 'lite' : stored");
    expect(src).toContain('platformSettings.unknownPlan');
  });

  it('does not present the trial-days field', () => {
    const src = code(PLATFORM_SETTINGS);
    const idx = src.indexOf('platformSettings.trialDays');
    expect(idx, 'the field exists in source, retained for V2').toBeGreaterThan(-1);
    // …inside a scope-gated block.
    expect(src.slice(Math.max(0, idx - 400), idx)).toContain('V2_SURFACES_ENABLED');
  });
});

describe('an existing trial tenant stays readable', () => {
  it('shows trial as a plan option only for a tenant already on it', () => {
    const src = code(TENANT_DRAWER);
    const line = src.split('\n').find((l) => l.includes("value: 'trial'")) ?? '';
    expect(line).toContain('V2_SURFACES_ENABLED');
    // Legacy compatibility: the row's own current plan keeps its label.
    expect(line).toContain("currentPlan === 'trial'");
  });
});

describe('trial source is retained, not deleted', () => {
  it('keeps the deferred trial UI in the tree for V2', () => {
    // Phase 5 owns deletion; this phase only makes it unreachable.
    expect(read('src/components/layout/TrialBanner.tsx')).toContain('TrialBanner');
    expect(read(PLATFORM_SETTINGS)).toContain('trial_days');
  });
});
