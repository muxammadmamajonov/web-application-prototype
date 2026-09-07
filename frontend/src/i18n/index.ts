import i18n from 'i18next';
import LanguageDetector from 'i18next-browser-languagedetector';
import { initReactI18next } from 'react-i18next';

import uz from './locales/uz.json';
import ru from './locales/ru.json';

export const SUPPORTED_LOCALES = ['uz', 'ru'] as const;
export type Locale = (typeof SUPPORTED_LOCALES)[number];

i18n
  .use(LanguageDetector)
  .use(initReactI18next)
  .init({
    resources: {
      uz: { translation: uz },
      ru: { translation: ru },
    },
    fallbackLng: ['ru', 'uz'],
    supportedLngs: SUPPORTED_LOCALES as unknown as string[],
    interpolation: { escapeValue: false },
    detection: {
      order: ['path', 'localStorage', 'navigator'],
      lookupFromPathIndex: 0,
      caches: ['localStorage'],
    },
  });

// (#160) Keep <html lang> in sync with the active language. It was hardcoded
// "uz" in index.html and never updated on locale switch — wrong for screen
// readers, browser hyphenation, and "translate this page" prompts.
function applyHtmlLang(lng: string): void {
  if (typeof document === 'undefined') return;
  document.documentElement.lang = lng?.startsWith('ru') ? 'ru' : 'uz';
}
i18n.on('languageChanged', applyHtmlLang);
applyHtmlLang(i18n.language);

export default i18n;
