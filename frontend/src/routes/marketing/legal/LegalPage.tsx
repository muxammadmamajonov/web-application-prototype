import type { ReactNode } from 'react';
import { useParams } from 'react-router';
import { Container } from '@/components/ui/Container';
import { PublicShell } from '@/components/marketing/PublicLayout';
import { linkifySupport } from '@/lib/linkify';
import { useSupportUsername } from '@/hooks/useSupportUsername';

interface Props {
  uz: { kicker: string; title: string; intro: string; sections: Array<{ heading: string; body: string }> };
  ru: { kicker: string; title: string; intro: string; sections: Array<{ heading: string; body: string }> };
  /** Effective / last-updated date (ISO yyyy-mm-dd). Shown at the foot of the page. */
  updated?: string;
}

// Default effective date for the legal documents. Bump this when the text of
// any policy materially changes.
const DEFAULT_UPDATED = '2026-05-30';

function formatUpdated(iso: string, lng: 'uz' | 'ru'): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  // Locale-safe formatting rather than a hand-maintained month table: the same
  // reason the date pickers moved to `Intl`. Uzbek Latin is the shipped script.
  try {
    return new Intl.DateTimeFormat(lng === 'ru' ? 'ru-RU' : 'uz-Latn-UZ',
      { day: 'numeric', month: 'long', year: 'numeric' }).format(d);
  } catch {
    return iso;
  }
}

export function LegalPage({ uz, ru, updated = DEFAULT_UPDATED }: Props): ReactNode {
  const support = useSupportUsername();
  const { locale } = useParams<{ locale?: string }>();
  const lng = (locale ?? 'uz') as 'uz' | 'ru';
  const data = lng === 'ru' ? ru : uz;
  return (
    <PublicShell>
      <section className="border-b border-line section-hero">
        <Container>
          <div className="kicker text-accent">{data.kicker}</div>
          <h1 className="mt-2 h-hero-2 font-semibold tracking-tight leading-[1.05]">{data.title}</h1>
          <p className="mt-4 max-w-lead text-ink-soft text-subsection">{data.intro}</p>
        </Container>
      </section>

      <section className="section-band">
        <Container className="max-w-3xl">
          <div className="space-y-8">
            {data.sections.map((s) => (
              <article key={s.heading}>
                <h2 className="text-title font-semibold tracking-tight">{s.heading}</h2>
                <p className="mt-3 text-body text-ink-soft leading-relaxed whitespace-pre-line">{linkifySupport(s.body, support)}</p>
              </article>
            ))}
          </div>
          <p className="mt-12 text-caption text-muted">
            {lng === 'ru'
              ? `Действует с ${formatUpdated(updated, 'ru')}.`
              : `${formatUpdated(updated, 'uz')}dan kuchga kiradi.`}
          </p>
        </Container>
      </section>
    </PublicShell>
  );
}
