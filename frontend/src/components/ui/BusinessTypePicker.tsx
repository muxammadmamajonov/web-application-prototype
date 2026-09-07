import { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Check, Search } from 'lucide-react';
import { cn } from '@/lib/cn';
import { useOnboardingOptions } from '@/lib/queries';

interface Props {
  value: string[];
  onChange: (v: string[]) => void;
  lng: 'uz' | 'ru';
}

/**
 * Multi-select business-type picker — a SEARCHABLE GRID of emoji+name cards,
 * replacing the cramped chip wall. Reads the canonical onboarding business types.
 * Empty selection means "applies to every business" (surfaced in the hint).
 */
export function BusinessTypePicker({ value, onChange, lng }: Props) {
  const { t } = useTranslation();
  const { data, isLoading } = useOnboardingOptions();
  const [q, setQ] = useState('');

  const types = data?.business_types ?? [];
  const name = (bt: { name_uz: string; name_ru: string }) => (lng === 'ru' ? bt.name_ru : bt.name_uz);
  const ql = q.trim().toLowerCase();
  const shown = ql ? types.filter((bt) => name(bt).toLowerCase().includes(ql)) : types;
  const toggle = (slug: string) =>
    onChange(value.includes(slug) ? value.filter((x) => x !== slug) : [...value, slug]);

  return (
    <div>
      <div className="mb-3 text-caption text-muted">
        {value.length === 0
          ? t('platformCatalog.bizTypesAllHint', 'Hech narsa tanlanmasa — barcha bizneslarga ko‘rinadi.')
          : t('platformCatalog.bizTypesSelected', { count: value.length, defaultValue: '{{count}} ta tanlandi' })}
      </div>

      {types.length > 8 && (
        <div className="relative mb-3">
          <Search size={15} className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-muted" />
          <input
            value={q}
            onChange={(e) => setQ(e.target.value)}
            placeholder={t('common.search', 'Qidirish…') ?? ''}
            aria-label={t('common.search', 'Qidirish') ?? 'search'}
            className="h-10 w-full rounded-xl border border-line bg-surface pl-9 pr-3 text-base outline-none placeholder:text-muted focus:border-ink/25 focus:bg-white"
          />
        </div>
      )}

      {isLoading ? (
        <div className="grid grid-cols-2 gap-2">
          {Array.from({ length: 6 }).map((_, i) => <div key={i} className="h-16 animate-pulse rounded-xl bg-ink/5" />)}
        </div>
      ) : (
        <div className="grid grid-cols-2 gap-2">
          {shown.map((bt) => {
            const sel = value.includes(bt.slug);
            return (
              <button
                key={bt.slug}
                type="button"
                role="checkbox"
                aria-checked={sel}
                onClick={() => toggle(bt.slug)}
                className={cn(
                  'relative flex items-center gap-2.5 rounded-xl border p-3 text-left transition',
                  sel ? 'border-[color:var(--c-accent-fill)] bg-[color:var(--c-accent-soft)]' : 'border-line bg-surface hover:border-ink/25',
                )}
              >
                <span className="grid h-9 w-9 shrink-0 place-items-center rounded-lg bg-cream text-lg leading-none">{bt.emoji ?? '📦'}</span>
                <span className="min-w-0 flex-1 truncate text-sm font-medium">{name(bt)}</span>
                {sel && (
                  <span className="grid h-5 w-5 shrink-0 place-items-center rounded-full bg-[color:var(--c-accent-fill)] text-white">
                    <Check size={12} />
                  </span>
                )}
              </button>
            );
          })}
          {shown.length === 0 && (
            <div className="col-span-2 py-6 text-center text-sm text-muted">{t('common.noResults', 'Topilmadi')}</div>
          )}
        </div>
      )}
    </div>
  );
}
