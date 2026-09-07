import { useParams } from 'react-router';
import { Briefcase, Send } from 'lucide-react';
import { Container } from '@/components/ui/Container';
import { Button } from '@/components/ui/Button';
import { EmptyState } from '@/components/ui/EmptyState';
import { PublicShell } from '@/components/marketing/PublicLayout';
import { useSupportUsername } from '@/hooks/useSupportUsername';

export default function CareersPage() {
  const { locale } = useParams<{ locale?: string }>();
  const lng = (locale ?? 'uz') as 'uz' | 'ru';
  const t = COPY[lng];
  const support = useSupportUsername();
  return (
    <PublicShell>
      <section className="border-b border-line py-16">
        <Container>
          <div className="kicker text-accent">{t.kicker}</div>
          <h1 className="mt-2 h-hero-2 font-semibold tracking-tight leading-[1.05]">{t.title}</h1>
          <p className="mt-4 max-w-2xl text-ink-soft text-subsection">{t.sub}</p>
        </Container>
      </section>

      <section className="py-16">
        <Container>
          <EmptyState
            className="mx-auto max-w-2xl"
            tone="accent"
            icon={<Briefcase size={26} />}
            title={t.emptyTitle}
            body={t.emptyBody}
            action={(
              <>
                <a href={`https://t.me/${support}`} target="_blank" rel="noreferrer">
                  <Button variant="inverse" size="md">
                    <Send size={16} /> {t.writeTo.replace('{{username}}', support)}
                  </Button>
                </a>
                <p className="text-caption text-muted">{t.cta}</p>
              </>
            )}
          />
        </Container>
      </section>
    </PublicShell>
  );
}

const COPY = {
  uz: {
    kicker: 'VAKANSIYALAR',
    title: 'Muddato jamoasiga qo\'shiling.',
    sub: "O'sayotgan jamoa, qiziqarli muammolar va to'g'ridan-to'g'ri ta'sir. Hozircha ochiq vakansiya yo'q — lekin kelajakka qarayapmiz.",
    emptyTitle: "Hozircha ochiq vakansiya yo'q",
    emptyBody: "Yaqinda joylarni e'lon qilamiz. Agar Muddato uchun foydali bo'lishingiz mumkin deb o'ylasangiz — yozib qoldiring.",
    writeTo: '@{{username}}\'ga yozing',
    cta: "Resume yoki LinkedIn havolasini yuboring",
  },
  ru: {
    kicker: 'ВАКАНСИИ',
    title: 'Присоединяйтесь к команде Muddato.',
    sub: 'Растущая команда, интересные задачи и прямое влияние. Сейчас открытых вакансий нет — но мы смотрим в будущее.',
    emptyTitle: 'Открытых вакансий пока нет',
    emptyBody: 'Скоро объявим. Если думаете, что будете полезны Muddato — напишите.',
    writeTo: 'Написать @{{username}}',
    cta: 'Пришлите резюме или ссылку на LinkedIn',
  },
} as const;
