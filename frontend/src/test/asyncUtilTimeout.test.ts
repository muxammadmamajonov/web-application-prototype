// @vitest-environment jsdom
/**
 * Guards the contention budget in `src/test/setup.ts`.
 *
 * The bug this pins was not a wrong number, it was a number that never reached
 * the clock: `vite.config.ts` widened vitest's `testTimeout` for CI, but every
 * `waitFor` kept expiring on testing-library's separate 1000ms
 * `asyncUtilTimeout`. Asserting the value vitest merely *configures* would
 * reproduce that mistake, so this reads back what testing-library itself
 * reports.
 */
import { describe, expect, it } from 'vitest';
import { getConfig } from '@testing-library/react';

describe('the CI async-util budget actually reaches testing-library', () => {
  it('applies the widened timeout under CI and the tight one locally', () => {
    expect(getConfig().asyncUtilTimeout).toBe(process.env.CI ? 10000 : 1000);
  });
});
