import { useTranslation } from 'react-i18next';
import { useParams } from 'react-router';
import { PublicShell } from '@/components/marketing/PublicLayout';
import { Pricing } from './LandingPage';

export default function PricingPage() {
  const { i18n } = useTranslation();
  const { locale } = useParams<{ locale?: string }>();
  const lng = (locale ?? i18n.language ?? 'uz') as 'uz' | 'ru';
  return (
    <PublicShell lng={lng}>
      <Pricing lng={lng} variant="full" />
    </PublicShell>
  );
}
