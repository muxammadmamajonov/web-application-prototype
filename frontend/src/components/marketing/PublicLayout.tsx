import { useEffect, useMemo, useRef, useState, type ReactNode } from 'react';
import { createPortal } from 'react-dom';
import { useTranslation } from 'react-i18next';
import { Link, useParams } from 'react-router';
import { ChevronDown, Menu, X } from 'lucide-react';
import { Logo } from '@/components/layout/Logo';
import { LocaleToggle } from '@/components/layout/LocaleToggle';
import { Container } from '@/components/ui/Container';
import { SkipLink } from '@/components/a11y/SkipLink';
import { BOT_USERNAME, CHANNEL_USERNAME } from '@/lib/telegram';
import { useSupportUsername } from '@/hooks/useSupportUsername';
import { cn } from '@/lib/cn';
import { useFocusTrap } from '@/hooks/useFocusTrap';

/**
 * One locale-independent navigation model.
 *
 * `titleKey`/`labelKey` are translated through the route-bound `t`, and `path`
 * is locale-prefixed at render. A per-locale copy of this structure (which is
 * what this replaced) puts user-visible copy in JSX, outside `uz.json`/`ru.json`
 * where it cannot be reviewed or kept in parity, and lets the two locales drift
 * to different LINKS as well as different words.
 */
type NavItem =
  | { labelKey: string; path: string; external?: false }
  | { labelKey: string; href: string; external: true };
type FooterGroup = { titleKey: string; items: NavItem[] };

/** `to`/`href` for an item, locale-prefixed when it is internal. */
const hrefFor = (it: NavItem, lng: string) => (it.external ? it.href : `/${lng}${it.path}`);

/** Header nav dropdown mirroring a footer link group (#62): Mahsulot / Kompaniya
 *  / Yuridik. Click to toggle; closes on outside-click or item selection. */
function NavDropdown({ group, lng }: { group: FooterGroup; lng: 'uz' | 'ru' }) {
  const { t } = useTranslation('translation', { lng });
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!open) return;
    const onDoc = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener('mousedown', onDoc);
    return () => document.removeEventListener('mousedown', onDoc);
  }, [open]);
  const itemCls = 'block rounded-icon px-3 py-2 text-body text-ink-soft transition hover:bg-ink/5 hover:text-ink';
  return (
    <div ref={ref} className="relative">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
        className="inline-flex items-center gap-1 text-body text-ink-soft transition hover:text-ink"
      >
        {t(group.titleKey)}
        <ChevronDown size={14} className={cn('transition-transform', open && 'rotate-180')} />
      </button>
      {open && (
        <div className="absolute left-0 top-full z-50 mt-2 min-w-[210px] rounded-card border border-line bg-surface p-1.5 shadow-pop">
          {group.items.map((it) => (
            it.external ? (
              <a key={it.labelKey} href={it.href} target="_blank" rel="noreferrer" onClick={() => setOpen(false)} className={itemCls}>{t(it.labelKey)}</a>
            ) : (
              <Link key={it.labelKey} to={hrefFor(it, lng)} onClick={() => setOpen(false)} className={itemCls}>{t(it.labelKey)}</Link>
            )
          ))}
        </div>
      )}
    </div>
  );
}

export function PublicHeader({ lng }: { lng: 'uz' | 'ru' }) {
  // Bound to the route locale, not `i18n.language`: the marketing shell is
  // rendered straight from the URL segment, and the two only converge after
  // LocaleToggle's sync effect runs.
  const { t } = useTranslation('translation', { lng });
  const footerGroups = useFooterGroups();
  const [open, setOpen] = useState(false);
  // The landing is cream-on-cream, so a faint bar reads as "no background".
  // Give it a frosted surface + shadow that deepens once the page scrolls.
  const [scrolled, setScrolled] = useState(false);
  useEffect(() => {
    const onScroll = () => setScrolled(window.scrollY > 8);
    onScroll();
    window.addEventListener('scroll', onScroll, { passive: true });
    return () => window.removeEventListener('scroll', onScroll);
  }, []);
  return (
    <header
      className={cn(
        'sticky top-0 z-40 border-b bg-surface transition-shadow duration-200',
        scrolled
          ? 'border-line shadow-header'
          : 'border-line/70',
      )}
    >
      <Container className="flex h-16 items-center gap-3 sm:gap-6">
        <Link to={`/${lng}`} className="shrink-0" aria-label={t('a11y.home')}><Logo /></Link>
        <nav className="hidden lg:flex items-center gap-7">
          {footerGroups.map((group) => (
            <NavDropdown key={group.titleKey} group={group} lng={lng} />
          ))}
        </nav>
        <div className="ml-auto flex items-center gap-1.5 sm:gap-2">
          <LocaleToggle />
          <Link to="/auth/login" className="hidden sm:inline-flex h-10 items-center px-3 text-body font-medium text-ink-soft hover:text-ink">
            {t('public.login')}
          </Link>
          {/* Persistent primary CTA — keeps the convert action visible on scroll.
              Self-serve signup is deferred, so acquisition CTAs lead to the
              demo request that is actually fulfilled. */}
          <Link
            to={`/${lng}/demo`}
            className="hidden sm:inline-flex h-10 items-center rounded-pill bg-ink px-4 text-body font-semibold text-white hover:brightness-110"
          >
            {t('public.startDemo')}
          </Link>
          <button
            onClick={() => setOpen(true)}
            className="tap-target lg:hidden grid h-11 w-11 place-items-center rounded-control border border-line bg-surface text-ink"
            aria-label={t('a11y.openMenu')}
          >
            <Menu size={18} />
          </button>
        </div>
      </Container>
      {open && <MobileDrawer lng={lng} onClose={() => setOpen(false)} />}
    </header>
  );
}

function MobileDrawer({ lng, onClose }: { lng: 'uz' | 'ru'; onClose: () => void }) {
  const { t } = useTranslation('translation', { lng });
  const footerGroups = useFooterGroups();
  const panelRef = useRef<HTMLDivElement>(null);
  // The shared dialog bundle, not a second overlay system: body-scroll lock,
  // Esc-to-close, Tab trapped inside the panel, initial focus moved in, and
  // focus RETURNED to whatever opened it — the menu trigger. The drawer
  // previously locked scroll by hand and did none of the rest, so a keyboard
  // user could Tab straight out into the page behind it and, on close, was left
  // with focus on <body>.
  useFocusTrap(panelRef, true, onClose);

  // Portaled to <body> so the panel/backdrop are positioned against the
  // viewport — the header's backdrop-blur establishes a containing block for
  // fixed descendants, which would otherwise trap this drawer inside the bar.
  return createPortal(
    <div className="fixed inset-0 z-[60] lg:hidden">
      {/* Backdrop: solid 75% ink with backdrop-blur so the landing content
          behind it stops competing for attention. */}
      <button
        type="button"
        aria-label={t('a11y.closeMenu')}
        onClick={onClose}
        className="absolute inset-0 bg-ink/75 backdrop-blur-sm"
      />
      <div
        ref={panelRef}
        role="dialog"
        aria-modal="true"
        aria-label={t('public.menuTitle', 'Menyu')}
        className="absolute right-0 top-0 flex h-full w-[84%] max-w-[360px] flex-col bg-surface shadow-pop animate-in slide-in-from-right"
      >
        <div className="flex h-16 shrink-0 items-center justify-between border-b border-line px-5">
          <Logo />
          <button onClick={onClose}
                  aria-label={t('a11y.closeMenu')}
                  className="tap-target grid h-11 w-11 place-items-center rounded-control text-ink-soft hover:bg-ink/5"><X size={18} aria-hidden="true" /></button>
        </div>
        <nav className="flex-1 overflow-y-auto p-4">
          {footerGroups.map((group) => (
            <div key={group.titleKey} className="mb-4">
              {/* `.kicker` uppercases visually, so the translated value stays
                  in its natural case — never `.toUpperCase()` a translation. */}
              <div className="kicker mb-1.5 px-3">{t(group.titleKey)}</div>
              {group.items.map((it) => (
                it.external ? (
                  <a key={it.labelKey} href={it.href} target="_blank" rel="noreferrer" onClick={onClose}
                     className="block rounded-control px-3 py-2.5 text-body text-ink hover:bg-ink/5">{t(it.labelKey)}</a>
                ) : (
                  <Link key={it.labelKey} to={hrefFor(it, lng)} onClick={onClose}
                        className="block rounded-control px-3 py-2.5 text-body text-ink hover:bg-ink/5">{t(it.labelKey)}</Link>
                )
              ))}
            </div>
          ))}
          <div className="border-t border-line pt-3">
            <Link to="/auth/login" onClick={onClose}
                  className="block rounded-control px-3 py-3 text-subsection font-medium text-ink-soft hover:bg-ink/5">
              {t('public.login')}
            </Link>
            <Link to={`/${lng}/demo`} onClick={onClose}
                  className="mt-2 flex items-center justify-center rounded-pill bg-ink px-4 py-3 text-subsection font-semibold text-white">
              {t('public.startDemo')}
            </Link>
          </div>
        </nav>
      </div>
    </div>,
    document.body,
  );
}

// Features / Roadmap / Careers are deferred, so the public navigation must not
// advertise them. Pricing, contact, the Telegram channel and the legal pages
// are retained.
//
// (MUDD-71 H3) A hook rather than a module constant: the Partners link points at
// the support handle, which an operator edits in platform settings. As a const it
// froze at build time, so changing the handle silently left the public footer —
// the most visible surface of all — pointing at the old chat. Memoised on the
// handle, so the three consumers keep a stable array identity between renders.
function useFooterGroups(): FooterGroup[] {
  const support = useSupportUsername();
  return useMemo<FooterGroup[]>(() => [
    {
      titleKey: 'public.groupProduct',
      items: [
        { labelKey: 'public.navPricing', path: '/pricing' },
        { labelKey: 'public.navNews', href: `https://t.me/${CHANNEL_USERNAME}`, external: true },
      ],
    },
    {
      titleKey: 'public.groupCompany',
      items: [
        { labelKey: 'public.navAbout', path: '' },
        { labelKey: 'public.navContact', path: '/contact' },
        { labelKey: 'public.navPartners', href: `https://t.me/${support}`, external: true },
      ],
    },
    {
      titleKey: 'public.groupLegal',
      items: [
        { labelKey: 'public.navTerms', path: '/terms' },
        { labelKey: 'public.navPrivacy', path: '/privacy' },
        { labelKey: 'public.navRefund', path: '/refund' },
        { labelKey: 'public.navCookies', path: '/cookies' },
      ],
    },
  ], [support]);
}

export function PublicFooter({ lng }: { lng: 'uz' | 'ru' }) {
  const { t } = useTranslation('translation', { lng });
  const footerGroups = useFooterGroups();
  return (
    <footer className="bg-cream pt-16 pb-10">
      <Container>
        {/* logo (2 cols on tablet, 2 cols on desktop) + 3 link groups (1 each) */}
        <div className="grid grid-cols-2 gap-10 md:grid-cols-5">
          <div className="col-span-2">
            <Logo />
            <p className="mt-4 max-w-xs text-body text-ink-soft">{t('public.footerTagline')}</p>
            <a
              className="mt-3 inline-block text-body text-accent hover:underline"
              href={`https://t.me/${BOT_USERNAME}`}
              target="_blank" rel="noreferrer"
            >@{BOT_USERNAME}</a>
          </div>
          {footerGroups.map((group) => (
            <div key={group.titleKey}>
              <div className="kicker mb-3">{t(group.titleKey)}</div>
              <ul className="space-y-2 text-body text-ink-soft">
                {group.items.map((it) => (
                  <li key={it.labelKey}>
                    {it.external ? (
                      <a className="hover:text-ink" href={it.href} target="_blank" rel="noreferrer">{t(it.labelKey)}</a>
                    ) : (
                      <Link className="hover:text-ink" to={hrefFor(it, lng)}>{t(it.labelKey)}</Link>
                    )}
                  </li>
                ))}
              </ul>
            </div>
          ))}
        </div>
        <div className="mt-12 flex flex-col justify-between gap-3 border-t border-line pt-6 text-caption text-muted sm:flex-row">
          <span>© Muddato {new Date().getFullYear()} · {t('public.rightsReserved')}</span>
          <span>{t('public.location')}</span>
        </div>
      </Container>
    </footer>
  );
}

/**
 * Wraps a marketing/standalone page with the shared header + footer, and with
 * the `<main id="main">` landmark that `SkipLink` targets and that
 * `RouteFocusManager` focuses on every route change.
 *
 * `lng` is an override for the routes whose pattern carries no `:locale`
 * segment — the landing is routed as the literal `/uz` and `/ru`, so reading
 * the locale from `useParams` alone would render Uzbek chrome on `/ru`.
 */
export function PublicShell({ children, lng: lngOverride }: { children: ReactNode; lng?: 'uz' | 'ru' }) {
  const { locale } = useParams<{ locale?: string }>();
  const lng = lngOverride ?? ((locale ?? 'uz') as 'uz' | 'ru');
  return (
    <div className={cn('min-h-screen bg-cream text-ink')}>
      <SkipLink />
      <PublicHeader lng={lng} />
      <main id="main" tabIndex={-1} className="focus:outline-none">{children}</main>
      <PublicFooter lng={lng} />
    </div>
  );
}
