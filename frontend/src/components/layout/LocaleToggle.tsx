import { useEffect } from 'react';
import { useTranslation } from 'react-i18next';
import { useNavigate, useParams } from 'react-router';
import { cn } from '@/lib/cn';
import { SUPPORTED_LOCALES, type Locale } from '@/i18n';

/** "O'Z / RU" pill toggle. Also keeps i18n.language in sync with the URL
 *  locale segment so t() never returns the raw key when the user lands on
 *  /uz or /ru directly. */
export function LocaleToggle({ className }: { className?: string }) {
  const { i18n } = useTranslation();
  const nav = useNavigate();
  const { locale: pathLocale } = useParams<{ locale: Locale }>();
  const current = (pathLocale ?? i18n.language) as Locale;

  // Sync i18n with the URL — fixes raw-key flashes after a hard reload.
  useEffect(() => {
    if (pathLocale && pathLocale !== i18n.language) {
      i18n.changeLanguage(pathLocale);
    }
  }, [pathLocale, i18n]);

  const setLocale = (next: Locale) => {
    if (next === current) return;
    i18n.changeLanguage(next);
    const here = location.pathname;
    const rewritten = here.replace(/^\/(uz|ru)(?=\/|$)/, `/${next}`);
    nav(rewritten === here ? `/${next}` : rewritten);
  };

  return (
    <div className={cn('flex items-center rounded-control border border-line bg-surface p-1 text-caption font-medium', className)}>
      {SUPPORTED_LOCALES.map((l) => (
        <button
          key={l}
          onClick={() => setLocale(l)}
          className={cn(
            'h-7 rounded-icon px-2.5 transition',
            current === l ? 'bg-ink text-white' : 'text-ink-soft hover:bg-ink/5',
          )}
        >
          {l === 'uz' ? "O'Z" : 'RU'}
        </button>
      ))}
    </div>
  );
}
