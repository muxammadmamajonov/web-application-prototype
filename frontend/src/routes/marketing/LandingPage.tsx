import { useState } from 'react';
import { Link, useParams } from 'react-router';
import { useTranslation } from 'react-i18next';
import {
  ArrowRight, Bell, Calculator, Check, ChevronDown, CreditCard, MessageSquare,
  ShieldCheck, Wallet,
} from 'lucide-react';
import { Container } from '@/components/ui/Container';
import { Button } from '@/components/ui/Button';
import { buttonClasses } from '@/components/ui/buttonClasses';
import { PublicShell } from '@/components/marketing/PublicLayout';
import { cn } from '@/lib/cn';
import { type PublicPlan, usePublicPlans, useYearlyBilling } from '@/lib/queries';
import { linkifySupport } from '@/lib/linkify';
import { useSupportUsername } from '@/hooks/useSupportUsername';
import { money } from '@/lib/format';

// Nav lives in <PublicHeader />.

export default function LandingPage() {
  const { i18n } = useTranslation();
  const { locale } = useParams<{ locale?: string }>();
  const lng = (locale ?? i18n.language ?? 'uz') as 'uz' | 'ru';

  return (
    <PublicShell lng={lng}>
      <Hero lng={lng} />
      <HowItWorks lng={lng} />
      <Features lng={lng} variant="teaser" />
      <ProductTour lng={lng} />
      <Stats lng={lng} />
      <Pricing lng={lng} variant="teaser" />
      <Faq lng={lng} />
      <FinalCta lng={lng} />
    </PublicShell>
  );
}

// ── Header ──────────────────────────────────────────────────────────────────
// SiteHeader removed — landing now uses the shared <PublicHeader />.

// ── Hero ────────────────────────────────────────────────────────────────────
/** Exported so the i18n migration can be verified against the real hero. */
export function Hero({ lng }: { lng: 'uz' | 'ru' }) {
  const { t } = useTranslation('translation', { lng });
  return (
    <section className="border-b border-line">
      <Container className="grid grid-cols-1 gap-12 py-16 lg:grid-cols-2 lg:py-24">
        <div>
          <span className="inline-flex items-center gap-2 rounded-pill border border-line bg-surface px-3 py-1 text-caption text-ink-soft">
            <span className="h-1.5 w-1.5 rounded-pill bg-success" />
            {t('landing.hero.tag')}
          </span>
          <h1 className="mt-6 h-hero font-semibold leading-[1.05] sm:leading-[1.02] tracking-tight break-words">
            {t('landing.hero.title.l1')}{' '}
            <span className="text-accent">{t('landing.hero.title.accent')}</span>{' '}
            {t('landing.hero.title.l2')}
          </h1>
          <p className="mt-6 max-w-hero-col text-ink-soft text-subsection leading-relaxed">
            {t('landing.hero.sub')}
          </p>
          <div className="mt-8 flex flex-wrap items-center gap-3">
            {/* These navigate, so they ARE links — styled like buttons rather
                than wrapping one, which would nest a button inside a link. */}
            <Link to={`/${lng}/demo`} className={buttonClasses({ variant: 'inverse', size: 'lg' })}>
              {t('landing.cta.tryDemo')} <ArrowRight size={18} />
            </Link>
            <Link to={`/${lng}/contact`} className={buttonClasses({ variant: 'outline', size: 'lg' })}>
              {t('landing.cta.talkToSales')}
            </Link>
          </div>
          <div className="mt-10 flex flex-wrap items-center gap-x-5 gap-y-2 text-small text-muted">
            {(t('landing.hero.socialProof') as string).split('·').map((chip, i) => (
              <span key={i} className="inline-flex items-center gap-1.5">
                <Check size={16} className="text-success" /> {chip.trim()}
              </span>
            ))}
          </div>
        </div>

        <HeroIllustration lng={lng} />
      </Container>
    </section>
  );
}

/** Real product screenshot (web dashboard + mobile companion) rendered as a
 *  single responsive image so it scales cleanly on resize — no coded mockup. */
function HeroIllustration({ lng }: { lng: 'uz' | 'ru' }) {
  const { t } = useTranslation('translation', { lng });
  return (
    <div className="flex items-center justify-center lg:justify-end">
      <img
        src="/hero-dashboard.png"
        alt={t('landing.ui.heroAlt')}
        width={1446}
        height={1114}
        loading="eager"
        decoding="async"
        className="h-auto w-full max-w-hero-col"
      />
    </div>
  );
}

// ── Trust bar ───────────────────────────────────────────────────────────────
// ── How it works ────────────────────────────────────────────────────────────
function HowItWorks({ lng }: { lng: 'uz' | 'ru' }) {
  const { t } = useTranslation('translation', { lng });
  const steps = [1, 2, 3] as const;
  return (
    <section id="how" className="border-b border-line section-band-lg">
      <Container>
        <div className="kicker text-accent">{t('landing.how.kicker')}</div>
        <h2 className="mt-2 max-w-lead h-hero-3 font-semibold tracking-tight">
          {t('landing.how.title')}
        </h2>
        <div className="mt-12 grid grid-cols-1 gap-6 md:grid-cols-3">
          {steps.map((n) => (
            <article key={n} className="card p-7">
              <div className="font-mono text-body text-muted">{String(n).padStart(2, '0')}</div>
              <h3 className="mt-3 text-section font-semibold tracking-tight">{t(`landing.how.step${n}.title`)}</h3>
              <p className="mt-3 text-body text-ink-soft leading-relaxed">{t(`landing.how.step${n}.body`)}</p>
            </article>
          ))}
        </div>
      </Container>
    </section>
  );
}

// ── Features grid ───────────────────────────────────────────────────────────
export function Features({ lng, variant = 'full' }: { lng: 'uz' | 'ru'; variant?: 'full' | 'teaser' }) {
  const { t } = useTranslation('translation', { lng });
  const allItems = [
    { key: 'calc',     icon: Calculator    },
    { key: 'payment',  icon: Wallet        },
    { key: 'reminder', icon: Bell          },
    { key: 'roles',    icon: ShieldCheck   },
    { key: 'reports',  icon: CreditCard    },
    { key: 'bot',      icon: MessageSquare },
  ] as const;
  const teaser = variant === 'teaser';
  const items = teaser ? allItems.slice(0, 3) : allItems;
  return (
    <section id="features" className="border-b border-line section-band-lg">
      <Container>
        <div className="kicker text-accent">{t('landing.features.kicker')}</div>
        <h2 className="mt-2 max-w-lead h-hero-3 font-semibold tracking-tight">
          {t('landing.features.title')}
        </h2>
        <p className="mt-4 max-w-lead text-ink-soft">{t('landing.features.sub')}</p>

        <div className="mt-12 grid grid-cols-1 gap-px overflow-hidden rounded-card border border-line bg-line sm:grid-cols-2 lg:grid-cols-3">
          {items.map(({ key, icon: Icon }) => (
            <div key={key} className="bg-surface p-6">
              <div className="grid h-10 w-10 place-items-center rounded-control bg-[color:var(--c-accent-soft)] text-accent">
                <Icon size={18} />
              </div>
              <h3 className="mt-4 font-semibold tracking-tight">{t(`landing.features.${key}.title`)}</h3>
              <p className="mt-2 text-body text-ink-soft">{t(`landing.features.${key}.body`)}</p>
            </div>
          ))}
        </div>

        {teaser && (
          <Link to={`/${lng}/pricing`}
                className="mt-8 inline-flex items-center gap-1.5 text-body font-medium text-accent hover:gap-2.5 transition-all">
            {t('landing.more.features')} <ArrowRight size={16} />
          </Link>
        )}
      </Container>
    </section>
  );
}

// ── Product tour — full tabbed dashboard tour (shown inline on the landing) ──
function ProductTour({ lng }: { lng: 'uz' | 'ru' }) {
  return <ProductTourFull lng={lng} />;
}

const TOUR_TABS = ['overview', 'customers', 'installments', 'payments', 'reports'] as const;

function ProductTourFull({ lng }: { lng: 'uz' | 'ru' }) {
  const { t } = useTranslation('translation', { lng });
  const [tab, setTab] = useState<(typeof TOUR_TABS)[number]>('overview');
  return (
    <section id="product" className="border-b border-line bg-surface section-band-lg">
      <Container>
        <div className="kicker text-accent">{t('landing.tour.kicker')}</div>
        <h2 className="mt-2 max-w-lead h-hero-3 font-semibold tracking-tight">
          {t('landing.tour.title')}
        </h2>

        {/* Tab pills — switch the feature being explained. */}
        <div className="mt-8 -mx-1 flex gap-2 overflow-x-auto px-1 pb-1">
          {TOUR_TABS.map((k) => (
            <button
              key={k}
              onClick={() => setTab(k)}
              className={`shrink-0 rounded-pill px-4 py-2 text-body font-medium transition ${
                tab === k ? 'bg-ink text-white' : 'border border-line bg-surface text-ink-soft hover:text-ink'
              }`}
            >
              {t(`landing.tour.tabs.${k}`)}
            </button>
          ))}
        </div>

        <div className="mt-6 grid gap-6 lg:grid-cols-[0.9fr_1.1fr] lg:items-center">
          <div>
            <h3 className="text-title font-semibold tracking-tight">{t(`landing.tour.${tab}.title`)}</h3>
            <p className="mt-3 text-ink-soft">{t(`landing.tour.${tab}.body`)}</p>
          </div>
          <div className="overflow-hidden rounded-card border border-line bg-cream p-3 shadow-pop sm:p-5">
            <img
              src="/hero-dashboard.png"
              alt={t(`landing.tour.${tab}.title`)}
              width={1456}
              height={1114}
              loading="lazy"
              className="w-full rounded-control"
            />
          </div>
        </div>
      </Container>
    </section>
  );
}

// ── Stats ──────────────────────────────────────────────────────────────────
function Stats({ lng }: { lng: 'uz' | 'ru' }) {
  const { t } = useTranslation('translation', { lng });
  // Keep these figures tied to shipped MVP behaviour.  The two payment rails
  // are cash and manual card/bank transfer; provider-hosted Click/Payme are V2.
  const items = [
    { val: '2',    key: 'payMethods' },
    { val: '2',    key: 'languages'  },
    { val: '24/7', key: 'reminders'  },
    { val: '0%',   key: 'hiddenFee'  },
  ];
  return (
    <section id="audience" className="border-b border-line section-band-lg">
      <Container>
        <div className="grid grid-cols-2 gap-8 lg:grid-cols-4">
          {items.map(({ val, key }) => (
            <div key={key}>
              <div className="h-hero-2 font-semibold tracking-tight leading-none">{val}</div>
              <div className="mt-2 text-body text-ink-soft">{t(`landing.stats.${key}`)}</div>
            </div>
          ))}
        </div>
      </Container>
    </section>
  );
}

// ── Pricing ────────────────────────────────────────────────────────────────
export function Pricing({ lng, variant = 'full' }: { lng: 'uz' | 'ru'; variant?: 'full' | 'teaser' }) {
  const { t } = useTranslation('translation', { lng });
  // Fully data-driven from the DB (GET /plans/public → only is_public plans).
  const { data: live, isLoading, isError, refetch } = usePublicPlans();
  const { data: yearly } = useYearlyBilling();
  // Monthly is the default view on purpose: it is the smaller, less alarming
  // number, and the yearly saving reads as a gain from there rather than the
  // monthly price reading as a penalty.
  const [annual, setAnnual] = useState(false);
  const showToggle = !!yearly?.enabled && (yearly?.discount_pct ?? 0) > 0;
  const plans = live ?? [];
  const planName = (p: PublicPlan) => (lng === 'ru' ? p.name_ru : p.name_uz);
  const fmt = (n: number | null | undefined) => (n == null ? '∞' : new Intl.NumberFormat('uz').format(n));
  const isFlex = (p: PublicPlan) => p.max_active_installments === 0 && !!p.overage?.contract?.enabled;
  const notes = (['note1', 'note2', 'note3', 'note4', 'note5'] as const)
    .map((k) => t(`landing.ui.${k}`));
  // On /pricing this block IS the page, so its title is the page title; on the
  // landing it is one section under the hero's h1.
  const Title = variant === 'full' ? 'h1' : 'h2';

  return (
    <section id="pricing" className="border-b border-line bg-surface section-band-lg">
      <Container>
        <div className="kicker text-accent">{t('landing.pricing.kicker')}</div>
        <Title className="mt-2 max-w-lead h-hero-3 font-semibold tracking-tight">
          {t('landing.pricing.title')}
        </Title>
        <p className="mt-4 max-w-lead text-ink-soft">{t('landing.pricing.sub')}</p>

        {/* Billing-period toggle. Rendered only when a discount is actually
            configured — a toggle offering "yearly (0% off)" advertises that we
            have nothing to offer. */}
        {showToggle && (
          <div className="mt-8 inline-flex items-center gap-1 rounded-pill border border-line bg-cream p-1"
               role="group" aria-label={t('landing.ui.billingPeriod', "To'lov davri")}>
            {([false, true] as const).map((yr) => (
              <button
                key={String(yr)}
                type="button"
                onClick={() => setAnnual(yr)}
                aria-pressed={annual === yr}
                className={cn(
                  'tap-target min-h-11 rounded-pill px-4 text-body font-medium transition',
                  annual === yr ? 'bg-ink text-white' : 'text-ink-soft hover:bg-ink/5',
                )}
              >
                {yr
                  ? t('landing.ui.billedYearly', 'Yillik')
                  : t('landing.ui.billedMonthly', 'Oylik')}
                {yr && (
                  <span className={cn('ml-1.5 text-caption', annual ? 'text-white/80' : 'text-accent')}>
                    −{yearly!.discount_pct}%
                  </span>
                )}
              </button>
            ))}
          </div>
        )}

        {isLoading && !live ? (
          <div className="mt-12 grid grid-cols-1 gap-5 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-5">
            {[0, 1, 2].map((i) => (
              <div
                key={i}
                className="h-[420px] animate-pulse rounded-card border border-line bg-surface"
              />
            ))}
          </div>
        ) : isError && (!live || live.length === 0) ? (
          <div className="mt-12 grid grid-cols-1">
            <div className="mx-auto flex max-w-panel flex-col items-center gap-4 rounded-card border border-line bg-surface p-8 text-center">
              <p className="text-ink-soft">
                {t('landing.ui.plansLoadFailed')}
              </p>
              <Button variant="outline" size="md" onClick={() => refetch()}>
                {t('landing.ui.retry')}
              </Button>
            </div>
          </div>
        ) : (
        <div className="mt-12 grid grid-cols-1 gap-5 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-5">
          {plans.map((p) => {
            const popular = p.is_popular;
            const cov = p.overage?.contract;
            return (
              <article key={p.code} className={cn(
                'relative flex flex-col rounded-card border p-6 shadow-card',
                popular ? 'border-ink bg-ink text-white' : 'border-line bg-surface text-ink',
              )}>
                {popular && (
                  // Overhang ribbon (out of normal flow) so the plan name still
                  // starts on the same line as the other cards' names. (#-landing)
                  <div className="absolute -top-3 left-1/2 inline-flex -translate-x-1/2 whitespace-nowrap rounded-pill bg-accent px-3 py-0.5 text-micro font-semibold text-white shadow-card">
                    {t('landing.ui.mostPopular')}
                  </div>
                )}
                <div className={cn('text-micro font-medium uppercase tracking-kicker', popular ? 'text-white/70' : 'text-accent')}>
                  {planName(p)}
                </div>
                <div className="mt-3 flex items-baseline gap-1.5">
                  {p.is_contact_sales ? (
                    <span className="h-page font-semibold tracking-tight">{t('landing.ui.negotiable')}</span>
                  ) : (
                    <>
                      <span className="h-display font-semibold tracking-tight">
                        {money(annual ? p.price_yearly_monthly_equivalent : p.price_monthly)}
                      </span>
                      <span className={cn('text-body', popular ? 'text-white/70' : 'text-muted')}>{t('landing.ui.perMonth')}</span>
                    </>
                  )}
                </div>
                {/* Under the toggle we quote the per-MONTH equivalent, because
                    "1,430,400 a year" means far less to a shop owner than
                    "279,200 a month". The full yearly charge and the absolute
                    saving both appear here so nothing is hidden. */}
                {annual && !p.is_contact_sales && (
                  <div className={cn('mt-1 text-caption', popular ? 'text-white/85' : 'text-ink-soft')}>
                    {t('landing.ui.yearlyBilled', {
                      total: money(p.price_yearly),
                      saving: money(p.yearly_saving),
                      defaultValue: "Yiliga {{total}} — {{saving}} tejaysiz",
                    })}
                  </div>
                )}
                {isFlex(p) && cov ? (
                  <div className={cn('mt-1 text-caption', popular ? 'text-white/85' : 'text-ink-soft')}>
                    {t('landing.ui.flexPerContract', { price: money(cov.price) })}
                  </div>
                ) : (!p.is_contact_sales && cov?.enabled ? (
                  <div className={cn('mt-1 text-caption', popular ? 'text-white/70' : 'text-muted')}>
                    {t('landing.ui.overLimit', { price: money(cov.price) })}
                  </div>
                ) : null)}

                <ul className={cn('mt-5 space-y-2 text-body', popular ? 'text-white' : 'text-ink-soft')}>
                  {([
                    [t('landing.ui.rowActive'), fmt(p.max_active_installments)],
                    [t('landing.ui.rowStaff'), fmt(p.max_staff)],
                  ] as const).map(([label, val]) => (
                    <li key={label} className="flex items-center justify-between gap-2">
                      <span className={cn(popular ? 'text-white/80' : 'text-muted')}>{label}</span>
                      <span className="font-medium tabular-nums">{val}</span>
                    </li>
                  ))}
                </ul>

                {isFlex(p) && (
                  <div className={cn('mt-3 rounded-control px-3 py-2 text-caption', popular ? 'bg-white/10 text-white/90' : 'bg-[color:var(--c-accent-soft)] text-ink-soft')}>
                    {t('landing.ui.flexNote')}
                  </div>
                )}

                <div className="mt-auto pt-6">
                  <Link to={p.is_contact_sales ? `/${lng}/contact` : `/${lng}/demo?plan=${p.code}`}
                        className={buttonClasses({ variant: popular ? 'primary' : 'inverse', size: 'md', block: true })}>
                      {p.is_contact_sales ? t('landing.ui.ctaContact') : t('landing.ui.ctaTry')}
                  </Link>
                </div>
              </article>
            );
          })}
        </div>
        )}

        {/* Subscription + overage explanation */}
        <div className="mt-8 grid gap-2 text-body text-ink-soft sm:grid-cols-2">
          {notes.map((n, i) => (
            <div key={i} className="flex items-start gap-2">
              <Check size={16} className="mt-0.5 shrink-0 text-success" />
              <span>{n}</span>
            </div>
          ))}
        </div>

        {variant === 'teaser' ? (
          <Link to={`/${lng}/pricing`}
                className="mt-10 inline-flex items-center gap-1.5 text-body font-medium text-accent hover:gap-2.5 transition-all">
            {t('landing.more.pricing')} <ArrowRight size={16} />
          </Link>
        ) : (
          <PricingMatrix lng={lng} plans={plans} />
        )}
      </Container>
    </section>
  );
}


// ── Feature comparison matrix (data-driven from the live plans) ─────────────
function PricingMatrix({ lng, plans }: { lng: 'uz' | 'ru'; plans: PublicPlan[] }) {
  type Cell = boolean | string | number;
  const { t } = useTranslation('translation', { lng });
  if (!plans.length) return null;
  const num = (n: number | null | undefined): Cell => (n == null ? '∞' : new Intl.NumberFormat('uz').format(n));
  const feat = (p: PublicPlan, k: string): Cell => !!p.features?.[k];

  const rows: { label: string; val: (p: PublicPlan) => Cell }[] = [
    { label: t('landing.ui.rowStaff'), val: (p) => num(p.max_staff) },
    { label: t('landing.ui.rowActive'), val: (p) => num(p.max_active_installments) },
    { label: t('landing.matrix.mxTelegramBotWebapp'), val: (p) => feat(p, 'telegram_bot') },
    { label: t('landing.matrix.mxManualPayments'), val: (p) => feat(p, 'cash') },
    { label: t('landing.matrix.mxAvtomatikEslatmalar'), val: (p) => feat(p, 'reminders') },
    { label: t('landing.matrix.mxMijozlarBoshqaruvi'), val: (p) => feat(p, 'customer_management') },
    { label: t('landing.matrix.mxToLovlarTarixi'), val: (p) => feat(p, 'payment_history') },
    { label: t('landing.matrix.mxCsvExport'), val: (p) => feat(p, 'basic_reports') },
    { label: t('landing.matrix.mxAuditJurnali'), val: (p) => feat(p, 'audit_logs') },
    { label: t('landing.matrix.mxAsosiyHisobotlar'), val: (p) => feat(p, 'basic_reports') },
    { label: t('landing.matrix.mxOverageRuxsat'), val: (p) => !!p.overage?.contract?.enabled },
    { label: t('landing.matrix.mxQoShimchaShartnoma'),
      val: (p) => (p.overage?.contract?.enabled ? `${money(p.overage.contract.price)}` : '—') },
  ];

  const renderCell = (v: Cell) => {
    if (v === true) return <Check size={16} className="mx-auto text-success" />;
    if (v === false) return <span className="mx-auto inline-block h-px w-4 bg-line align-middle" />;
    return <span className="text-body tabular-nums">{v}</span>;
  };

  return (
    <div className="mt-12 card overflow-x-auto p-0">
      <table className="w-full min-w-[640px]">
        <thead>
          <tr className="border-b border-line">
            <th className="px-5 py-4 text-left text-micro font-medium uppercase tracking-kicker text-muted">
              {t('landing.matrix.mxImkoniyatlarTaqqoslash')}
            </th>
            {plans.map((p) => (
              <th key={p.code} className={cn(
                'border-l px-4 py-4 text-center text-micro font-medium uppercase tracking-kicker',
                p.is_popular ? 'border-ink bg-ink/[0.03] text-ink' : 'border-line text-ink-soft',
              )}>
                {lng === 'ru' ? p.name_ru : p.name_uz}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => (
            <tr key={r.label} className="border-b border-line last:border-0">
              <td className="px-5 py-3 text-body">{r.label}</td>
              {plans.map((p) => (
                <td key={p.code} className={cn('border-l border-line px-4 py-3 text-center', p.is_popular && 'bg-ink/[0.015]')}>
                  {renderCell(r.val(p))}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

// ── FAQ ────────────────────────────────────────────────────────────────────
function Faq({ lng }: { lng: 'uz' | 'ru' }) {
  const support = useSupportUsername();
  const { t } = useTranslation('translation', { lng });
  const qs = [1, 2, 3, 4, 5, 6] as const;
  // Seeded FROM the id list, not from a literal: an initial value written by
  // hand drifts the moment the ids change, and a value that matches no id
  // renders "all closed" while the code reads as "first open".
  const [open, setOpen] = useState<number | null>(qs[0]);
  return (
    <section id="faq" className="border-b border-line bg-surface section-band-lg">
      <Container className="max-w-3xl">
        <div className="kicker text-accent">{t('landing.faq.kicker')}</div>
        <h2 className="mt-2 h-hero-3 font-semibold tracking-tight">
          {t('landing.faq.title')}
        </h2>
        <p className="mt-3 text-ink-soft">{linkifySupport(t('landing.faq.sub'), support)}</p>

        <ul className="mt-10 divide-y divide-line border-y border-line">
          {qs.map((i) => {
            const isOpen = open === i;
            // The answer stays mounted and is hidden, so `aria-controls` always
            // resolves to a real element — a conditional answer leaves the
            // reference dangling exactly when a collapsed item is announced.
            const answerId = `faq-answer-${i}`;
            return (
              <li key={i}>
                <button
                  onClick={() => setOpen(isOpen ? null : i)}
                  aria-expanded={isOpen}
                  aria-controls={answerId}
                  className="flex w-full items-center justify-between gap-6 py-5 text-left text-subsection font-medium"
                >
                  <span>{t(`landing.faq.q${i}.q`)}</span>
                  <ChevronDown size={18} className={cn('shrink-0 transition', isOpen && 'rotate-180')} />
                </button>
                <p id={answerId} hidden={!isOpen} className="pb-6 pr-8 text-body text-ink-soft leading-relaxed">
                  {t(`landing.faq.q${i}.a`)}
                </p>
              </li>
            );
          })}
        </ul>
      </Container>
    </section>
  );
}

// ── Final CTA ──────────────────────────────────────────────────────────────
function FinalCta({ lng }: { lng: 'uz' | 'ru' }) {
  const { t } = useTranslation('translation', { lng });
  return (
    <section id="contact" className="bg-ink section-band-lg text-white">
      <Container className="text-center">
        <h2 className="mx-auto max-w-3xl h-hero-2 font-semibold tracking-tight">
          {t('landing.finalCta.title')}
        </h2>
        <p className="mx-auto mt-4 max-w-xl text-white/70">{t('landing.finalCta.sub')}</p>
        <div className="mt-8 flex flex-wrap items-center justify-center gap-3">
          <Link to={`/${lng}/demo`} className={buttonClasses({ variant: 'primary', size: 'lg' })}>
            {t('landing.cta.tryDemo')} <ArrowRight size={18} />
          </Link>
          <Link to={`/${lng}/contact`}
                className={buttonClasses({ variant: 'outline', size: 'lg', className: 'border-white/20 bg-transparent text-white hover:bg-white/5' })}>
            {t('landing.cta.talkToSales')}
          </Link>
        </div>
      </Container>
    </section>
  );
}

// SiteFooter removed — landing now uses the shared <PublicFooter />.

// ── Local copy table ───────────────────────────────────────────────────────
// Kept inline so the landing ships independently of the rest of i18n. Real
// translations stay in src/i18n/locales/* for the app shell.
