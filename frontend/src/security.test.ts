/**
 * #745 — user-controlled strings (e.g. user.full_name) cannot inject HTML/JS.
 *
 * React escapes every value interpolated into JSX (`{user.full_name}`), so
 * user-controlled names are safe by default — there is no DOMPurify wrapper to
 * audit because there is no raw-HTML sink. The ONLY way to bypass auto-escaping
 * is `dangerouslySetInnerHTML`, which must never wrap unsanitized user input.
 *
 * This guard fails the build if anyone introduces `dangerouslySetInnerHTML`,
 * forcing an explicit "sanitize with DOMPurify first" review at that point.
 */
import { readdirSync, readFileSync, statSync } from 'node:fs';
import { dirname, join, relative } from 'node:path';
import { fileURLToPath } from 'node:url';
import { describe, expect, it } from 'vitest';

const SRC = dirname(fileURLToPath(import.meta.url)); // this file lives at src/

function sourceFiles(dir: string): string[] {
  const out: string[] = [];
  for (const entry of readdirSync(dir)) {
    if (entry === 'node_modules' || entry.startsWith('.')) continue;
    const full = join(dir, entry);
    if (statSync(full).isDirectory()) {
      out.push(...sourceFiles(full));
    } else if (/\.(tsx?|jsx?)$/.test(entry) && !/\.(test|spec)\./.test(entry)) {
      out.push(full);
    }
  }
  return out;
}

describe('#745 — no unescaped HTML sinks', () => {
  it('no source file opts out of React auto-escaping via dangerouslySetInnerHTML', () => {
    const offenders = sourceFiles(SRC)
      .filter((f) => readFileSync(f, 'utf8').includes('dangerouslySetInnerHTML'))
      .map((f) => relative(SRC, f));
    // If this ever fails: wrap the HTML in DOMPurify.sanitize() before injecting,
    // then add the file to an explicit allowlist here with a justification.
    expect(offenders).toEqual([]);
  });
});

describe('React Router security floor', () => {
  it('keeps the patched v8 package and removed DOM re-export package', () => {
    const pkg = JSON.parse(
      readFileSync(join(SRC, '..', 'package.json'), 'utf8'),
    ) as { dependencies?: Record<string, string> };
    const version = pkg.dependencies?.['react-router'] ?? '';
    const match = /^(\d+)\.(\d+)\.(\d+)$/.exec(version);

    expect(match, 'react-router must remain an exact, auditable version').not.toBeNull();
    const [, major, minor, patch] = match!.map(Number);
    expect(
      major > 8 || (major === 8 && (minor > 3 || (minor === 3 && patch >= 0))),
      'GHSA-qwww-vcr4-c8h2 requires react-router >= 8.3.0',
    ).toBe(true);
    expect(pkg.dependencies?.['react-router-dom']).toBeUndefined();
  });

  it('does not reintroduce imports from the removed react-router-dom package', () => {
    const offenders = sourceFiles(SRC)
      .filter((f) => readFileSync(f, 'utf8').includes('react-router-dom'))
      .map((f) => relative(SRC, f));
    expect(offenders).toEqual([]);
  });
});
