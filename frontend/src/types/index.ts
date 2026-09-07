/** Shared TypeScript types — mirrored from the backend Pydantic schemas. */

type Locale = 'uz' | 'ru';

export type Persona = 'platform' | 'tenant' | 'staff' | 'client';

export type TenantRole = 'owner' | 'manager' | 'sales';
type PlatformRole = 'super_admin' | 'support';

type TenantStatus = 'active' | 'demo' | 'suspended' | 'blocked';
/**
 * A plan CODE — an owner-chosen slug, not a member of a closed set.
 *
 * This was `'trial' | 'lite' | 'pro' | 'custom'`, the fixed enum plan codes
 * carried before R36 made them free-form. The live catalogue is now
 * starter/business/pro/enterprise/flex, so the union named exactly one code
 * that still exists — it typed `planCode` as a value it demonstrably is not,
 * and `planCode === 'lite'` type-checked while being dead at runtime.
 *
 * Deliberately `string`: the `plans` table is the only authority on which codes
 * exist, and a union here can only ever be a stale copy of it. Decide
 * capabilities from the plan's own flags, never by comparing this.
 */
type PlanCode = string;

export interface MeResponse {
  id: string;
  telegramId: number;
  telegramUsername: string | null;
  firstName: string | null;
  lastName: string | null;
  /** The name to ADDRESS this person by, already resolved server-side.
   *
   *  Not the same thing as `firstName`. The profile screen legitimately shows a
   *  Telegram profile name; a greeting must not, because the signed-in account
   *  may be an operator's. `null` means no personal name is known — render the
   *  neutral greeting rather than substituting a handle, a phone or a shop's
   *  own label for the person. */
  displayName: string | null;
  photoUrl: string | null;
  phone: string | null;
  email: string | null;
  birthdate: string | null;
  gender: 'male' | 'female' | null;
  locale: Locale;
  platformRole: PlatformRole | null;
  tenants: Array<{
    id: string;
    slug: string;
    name: string;
    role: TenantRole;
    status: TenantStatus;
    planCode: PlanCode;
    trialEndsAt: string | null;
    /** The shop's own currency — the same value its generated contracts and
     *  receipts are stamped with. Optional so a client running against an older
     *  backend simply falls back to the default instead of rendering
     *  `undefined` next to every sum. See lib/currency.ts. */
    currency?: string;
  }>;
}
