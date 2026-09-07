import type { TFunction } from 'i18next';
import type { MeResponse } from '@/types';

/**
 * How to address the person using the client app.
 *
 * The server has already decided WHICH name that is (`displayName`); this only
 * decides how to render "no name is known". It exists so both client surfaces
 * ask the same question and neither reaches for `firstName` again.
 *
 * `firstName` is the Telegram profile name of whatever account is signed in.
 * On a shop's own device that can be an operator's account, and greeting a
 * customer by a support identity is the defect this indirection prevents. A
 * missing name is a neutral greeting — never a handle, a phone, a shop's label
 * for the person, or the bot's own name.
 */
export function greetingFor(
  t: TFunction,
  me: Pick<MeResponse, 'displayName'> | null | undefined,
  keys: { named: string; anonymous: string },
): string {
  const name = (me?.displayName ?? '').trim();
  return name ? t(keys.named, { name }) : t(keys.anonymous);
}

export const CLIENT_GREETING = { named: 'client.greeting', anonymous: 'client.greetingAnon' };
export const WELCOME_GREETING = { named: 'welcome.greeting', anonymous: 'welcome.greetingAnon' };
