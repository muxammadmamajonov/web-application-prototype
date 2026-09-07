/**
 * MUDD-69 LOW-3 — "MRR" is SaaS-operator jargon with no meaning to a
 * Muddato shop owner reading the platform admin's revenue card. Both
 * locales must spell out the plain-language equivalent instead.
 */
import { describe, expect, it } from 'vitest';
import uz from './locales/uz.json';
import ru from './locales/ru.json';

describe('platformOverview.mrrEstimate (LOW-3)', () => {
  it('uz.json carries no raw "MRR" acronym', () => {
    expect(uz.platformOverview.mrrEstimate).not.toMatch(/MRR/);
  });

  it('ru.json carries no raw "MRR" acronym', () => {
    expect(ru.platformOverview.mrrEstimate).not.toMatch(/MRR/);
  });
});
