/**
 * C3-08 — production source explains invariants, not audit history.
 *
 * A previous pass reported this as already true. It was not: `R-01`, `R-02`,
 * `R-06`, `R-08` and `Phase 3` were all still present in shipped source. This
 * test exists so the claim is checked rather than asserted — a report can be
 * wrong, a sweep cannot.
 *
 * What is banned is the BOOKKEEPING, not the reasoning. A comment saying *why*
 * an invariant exists — a security, money, timezone or accessibility reason —
 * is the point of commenting. A comment saying which review pass found it is
 * noise to everyone who reads the file afterwards.
 *
 * Test files are exempt: a regression test's header describing the defect it
 * pins is exactly what that comment is for.
 */
import { describe, expect, it } from 'vitest';
import { readdirSync, readFileSync, existsSync } from 'node:fs';
import { resolve } from 'node:path';

/** The exact patterns the pass-3 brief requires to return zero. */
const PATTERNS: Array<[string, RegExp]> = [
  ['Phase [0-9]', /Phase [0-9]/],
  ['UX-[0-9]+', /UX-[0-9]+/],
  ['R-[0-9]+', /\bR-[0-9]+/],
  ['P2-[0-9]+', /\bP2-[0-9]+/],
  ['C3-[0-9]+', /\bC3-[0-9]+/],
];

const ROOTS = [
  resolve(__dirname, '..'),                              // frontend/src
  resolve(__dirname, '../../../backend/app'),            // backend/app
];
const CODE = /\.(ts|tsx|py|js)$/;

function productionSources(root: string): Array<{ path: string; text: string }> {
  const out: Array<{ path: string; text: string }> = [];
  if (!existsSync(root)) return out;
  const walk = (dir: string) => {
    for (const e of readdirSync(dir, { withFileTypes: true })) {
      const p = resolve(dir, e.name);
      if (e.isDirectory()) {
        if (e.name === 'tests' || e.name === '__pycache__' || e.name === 'node_modules') continue;
        walk(p);
        continue;
      }
      if (!CODE.test(e.name)) continue;
      if (e.name.includes('.test.') || e.name.startsWith('test_')) continue;
      out.push({ path: p, text: readFileSync(p, 'utf8') });
    }
  };
  walk(root);
  return out;
}

const FILES = ROOTS.flatMap(productionSources);

describe('the sweep actually reaches the code', () => {
  it('finds both production trees', () => {
    // A sweep that silently matched nothing would "pass" forever.
    for (const root of ROOTS) expect(existsSync(root), `${root} is missing`).toBe(true);
    expect(FILES.length, 'no production sources were scanned').toBeGreaterThan(200);
    expect(FILES.some((f) => f.path.endsWith('.py')), 'backend was not scanned').toBe(true);
    expect(FILES.some((f) => f.path.endsWith('.tsx')), 'frontend was not scanned').toBe(true);
  });

  it('excludes tests, which may name the defect they pin', () => {
    expect(FILES.some((f) => f.path.includes('.test.'))).toBe(false);
    expect(FILES.some((f) => /\/tests\//.test(f.path))).toBe(false);
  });
});

describe.each(PATTERNS)('no production source contains %s', (label, re) => {
  it('returns zero matches', () => {
    const hits: string[] = [];
    for (const { path, text } of FILES) {
      text.split('\n').forEach((line, i) => {
        if (re.test(line)) hits.push(`${path.split('/src/').pop() ?? path}:${i + 1}: ${line.trim().slice(0, 120)}`);
      });
    }
    expect(hits, `${label} still appears in production source:\n${hits.join('\n')}`).toEqual([]);
  });
});

describe('the safety rationale those comments carried is still there', () => {
  const read = (rel: string) => {
    for (const root of ROOTS) {
      const p = resolve(root, rel);
      if (existsSync(p)) return readFileSync(p, 'utf8');
    }
    throw new Error(`not found: ${rel}`);
  };

  const readPkg = (rel: string) => {
    for (const root of ROOTS) {
      const dir = resolve(root, rel);
      if (existsSync(dir)) {
        return readdirSync(dir)
          .filter((f) => f.endsWith('.py'))
          .map((f) => readFileSync(resolve(dir, f), 'utf8'))
          .join('\n');
      }
    }
    throw new Error(`not found: ${rel}`);
  };

  it('keeps the money-safety note on the query client', () => {
    const src = read('lib/queryClient.ts');
    expect(src).toMatch(/never be replayed/i);
    expect(src).toMatch(/READS|WRITES/);
  });

  it('keeps the timezone reasoning in the tenant API', () => {
    // The tenant API is a package; read every module so this covers the same
    // code it did when those handlers lived in one file.
    const src = readPkg('api/tenant');
    expect(src).toMatch(/UTC\+5|tenant's today|tenant's date/i);
    expect(src).toMatch(/nightly/i);          // the cron-lag explanation
  });

  it('keeps the PII rule on the draft hook', () => {
    const src = read('hooks/useWizardDraft.ts');
    expect(src).toMatch(/customer id/i);
    expect(src).toMatch(/shared shop-floor devices|shared/i);
  });

  it('keeps the accessibility reasoning on the shared primitives', () => {
    expect(read('components/ui/DataTable.tsx')).toMatch(/rowgroup|columnheader/);
    expect(read('components/ui/DatePicker.tsx')).toMatch(/roving/i);
    // The phrase wraps across comment lines, so match its stable prefix.
    expect(read('components/ui/buttonClasses.ts')).toMatch(/nests a button inside a/i);
  });
});
