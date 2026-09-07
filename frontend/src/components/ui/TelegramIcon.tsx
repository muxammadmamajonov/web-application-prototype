/** Telegram's paper-plane mark — not in lucide-react (brand icons aren't),
 *  so it's a standalone SVG. Use wherever a "via Telegram" action needs the
 *  brand glyph, instead of re-pasting the path data. */
export function TelegramIcon({ size = 18 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" aria-hidden>
      <path
        d="M21.7 3.3c.4-.2.9.2.8.7L19 19.4c-.2.7-.8.9-1.4.6l-5-3.7-2.4 2.3c-.3.3-.6.4-1 .4l.4-5 9.1-8.2c.4-.4-.1-.6-.6-.3L7.9 11.7l-4.9-1.5c-.7-.2-.7-.7.2-1.1L21.7 3.3z"
        fill="currentColor"
      />
    </svg>
  );
}
