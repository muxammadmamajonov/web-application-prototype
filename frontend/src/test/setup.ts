/**
 * Global vitest setup.
 *
 * The suite runs `environment: 'node'` by default and 96 files opt into jsdom
 * with a per-file `// @vitest-environment jsdom` pragma. Auto-cleanup from
 * `@testing-library/react` only self-registers when `globals: true` is set,
 * which it is not here — so every `render()` used to stay mounted for the whole
 * file. React 19 finishes its render work on a `setImmediate` scheduler task,
 * so a tree still mounted at teardown lands *after* jsdom is gone and throws
 * `ReferenceError: window is not defined` as an unhandled error. Vitest then
 * exits 1 even when every assertion passed — the failure is load-dependent,
 * which is why it only started biting once five runners shared the machine.
 *
 * Unmounting after each test drains the scheduler queue while the DOM is still
 * alive. The `document` guard keeps this a no-op for node-environment files,
 * where importing react-dom would be pointless work.
 */
import { afterEach, beforeAll } from 'vitest';

/**
 * `waitFor` / `findBy*` carry their OWN clock — testing-library's
 * `asyncUtilTimeout`, 1000ms by default — which is completely independent of
 * vitest's `testTimeout`. vite.config.ts already widened the vitest budget to
 * 15s for the five runners sharing one Mac mini (MUDD-90), but that knob never
 * reached testing-library, so an assertion still gave up after one second no
 * matter how much slack the runner had.
 *
 * That gap is what fails a route mounted through `lazyWithRetry`: the FIRST
 * test to reach a lazily-imported page pays the one-off dynamic-import
 * transform, and under contention that alone can outlast the second. The
 * signature is unmistakable — the first billing test times out while every
 * later test hitting the same page passes off the warm module cache, and the
 * whole file passes standalone. Same contention budget as MUDD-90, applied to
 * the clock that was actually expiring; kept under `testTimeout` so a genuinely
 * stuck assertion still reports testing-library's own diagnostic instead of a
 * bare vitest timeout. Local runs keep the tight default so a laptop fails fast.
 *
 * (MUDD-122 L3) Deliberately global to every jsdom file in CI, not scoped to
 * only the files that mount a lazy route: the contention is host-level (five
 * runners on one Mac mini), not test-specific, so any jsdom file's `waitFor` /
 * `findBy*` can land behind the same load, not only the ones that happen to
 * touch `lazyWithRetry` today. Narrowing this to a hand-picked file list would
 * just move the flake to the next file that gets unlucky, which is the same
 * shape of bug this fix exists to close. The real cost this trades away is
 * bounded, not unbounded: a GENUINELY broken assertion in CI now takes up to
 * 10s to report instead of 1s — still well under the 15s `testTimeout`
 * ceiling, and CI-only, so a local `pnpm test` still fails in 1s.
 */
beforeAll(async () => {
  if (typeof document === 'undefined') return;
  const { configure } = await import('@testing-library/react');
  configure({ asyncUtilTimeout: process.env.CI ? 10000 : 1000 });
});

afterEach(async () => {
  if (typeof document === 'undefined') return;
  const { cleanup } = await import('@testing-library/react');
  cleanup();
});
