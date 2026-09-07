import { useTranslation } from 'react-i18next';
import { useParams } from 'react-router';
import { PublicHeader, PublicFooter } from '@/components/marketing/PublicLayout';
import { Features } from './LandingPage';

export default function FeaturesPage() {
  const { i18n } = useTranslation();
  const { locale } = useParams<{ locale?: string }>();
  const lng = (locale ?? i18n.language ?? 'uz') as 'uz' | 'ru';
  return (
    <div className="min-h-screen bg-cream text-ink">
      <PublicHeader lng={lng} />
      <Features lng={lng} variant="full" />
      <PublicFooter lng={lng} />
    </div>
  );
}
