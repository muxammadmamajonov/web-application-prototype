/**
 * Compile-time pin for the type half of `@/lib/mutations`' public contract.
 *
 * Types are erased before any test runs, so `Object.keys` cannot see them, and
 * `tsconfig.json` excludes `*.test.ts` from `tsc -b` — a pin inside the sibling
 * `mutationsContract.test.ts` would be a guard that can never fail. This module
 * is ordinary source, so `npm run typecheck` compiles it: if any type below
 * stops being exported from the barrel, the import fails here by name.
 *
 * Every entry is a type a real consumer imports today. Request/response shapes
 * with no consumer are deliberately absent — they are private to their own
 * module, and adding one here would re-create the over-exported surface this
 * pin exists to hold steady.
 *
 * Nothing imports this module and nothing should: it emits no runtime code.
 */
import type {
  CustomerInvite, CustomerWrite, GuarantorWrite, InstallmentItemWrite, ProductWrite,
} from './mutations';

/** Naming each type keeps `noUnusedLocals` satisfied and the imports load-bearing. */
export type PinnedMutationTypes = [
  CustomerInvite, CustomerWrite, GuarantorWrite, InstallmentItemWrite, ProductWrite,
];
