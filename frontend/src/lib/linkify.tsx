import type { ReactNode } from 'react';
import { SUPPORT_USERNAME } from './telegram';

/** The token the uz/ru locale strings embed where the support handle belongs.
 *  It is a placeholder, not necessarily the handle that gets rendered — see
 *  `linkifySupport`. */
const SUPPORT_TOKEN = `@${SUPPORT_USERNAME}`;

/** Render plain text with `@MuddatoSupport` mentions turned into links to the
 *  support Telegram (#63). Surrounding text is preserved verbatim.
 *
 *  (MUDD-71 H3) `username` defaults to the build-time constant so behaviour is
 *  byte-identical to before; pass `useSupportUsername()` to render the handle an
 *  operator configured. This module used to carry its own literal
 *  `https://t.me/muddatosupport` — a third source of truth for the handle, and
 *  one that already disagreed with `SUPPORT_USERNAME` on case. */
export function linkifySupport(text: string, username: string = SUPPORT_USERNAME): ReactNode {
  const token = SUPPORT_TOKEN.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
  return text.split(new RegExp(`(${token})`, 'g')).map((part, i) =>
    part === SUPPORT_TOKEN ? (
      <a
        key={i}
        href={`https://t.me/${username}`}
        target="_blank"
        rel="noreferrer"
        className="text-accent underline underline-offset-2"
      >
        @{username}
      </a>
    ) : (
      part
    ),
  );
}
