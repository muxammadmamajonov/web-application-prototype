import { useTranslation } from 'react-i18next';

/** (#557) Skip-to-content link — visually hidden until focused, rendered as the
 *  first focusable element of a shell so keyboard/AT users can jump past the
 *  nav/sidebar straight into <main id="main" tabIndex={-1}>. Uses Tailwind's
 *  built-in sr-only + focus:not-sr-only to reveal on focus only. */
export function SkipLink() {
  const { t } = useTranslation();
  return (
    <a
      href="#main"
      className="sr-only focus:not-sr-only focus:fixed focus:left-4 focus:top-4 focus:z-[200] focus:rounded-icon focus:bg-ink focus:px-4 focus:py-2 focus:text-body focus:font-medium focus:text-white focus:shadow-pop focus:outline-none focus-visible:ring-2 focus-visible:ring-white/60"
    >
      {t('a11y.skip', "Asosiy kontentga o'tish")}
    </a>
  );
}
