import { useSupportChannels } from '@/lib/queries';
import { SUPPORT_USERNAME } from '@/lib/telegram';

/**
 * The one place the app answers "what is the support handle?".
 *
 * (MUDD-71 H3) There used to be three answers and they disagreed:
 *   1. `lib/telegram.ts` — the build-time constant `SUPPORT_USERNAME`, which
 *      eight surfaces imported directly;
 *   2. `lib/linkify.tsx` — its own literal `https://t.me/muddatosupport`, in a
 *      different CASE from (1), so the same handle rendered two ways;
 *   3. `platform_settings.support` via `GET /contact/channels` — the only one an
 *      operator can actually change (Sozlamalar → Aloqa kanali), and the only one
 *      exactly one surface read.
 *
 * So an operator could change the handle and seven of eight places kept sending
 * users to the old one. The stored value now wins everywhere; the constant is
 * the fallback for a cold cache, an offline start or an older backend, which is
 * why it stays. Cached 30 min by `useSupportChannels`, so this costs nothing to
 * call from as many components as need it.
 *
 * Returns the bare handle without `@` — call sites add the sigil or build
 * `https://t.me/${handle}` as they already did.
 */
export function useSupportUsername(): string {
  const channels = useSupportChannels();
  return channels.data?.support_username || SUPPORT_USERNAME;
}

/** The support chat URL, from the same single source. */
export function useSupportUrl(): string {
  return `https://t.me/${useSupportUsername()}`;
}
