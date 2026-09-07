import { useMemo } from 'react';
import { NavLink, useParams } from 'react-router';
import { useTranslation } from 'react-i18next';
import { ChevronRight, Lock } from 'lucide-react';
import { cn } from '@/lib/cn';
import { displayName } from '@/lib/format';
import { Avatar } from '@/components/ui/Avatar';
import { PageHeader } from '@/components/ui/PageHeader';
import { MobileHeader } from '@/components/mobile/primitives';
import { menuItemsFor, MENU_DESC_KEY } from '@/routes/_navConfig';
import { useSession } from '@/store/session';
import type { Locale } from '@/i18n';
import type { Persona } from '@/types';

/**
 * The mobile "Menyu" hub — a real route (so the bottom-nav Menu tab is highlighted
 * from the URL, survives refresh, and deep-links). Reads the SAME nav config as the
 * sidebar, so the grouped sections (Operatsiya / O'sish / Boshqaruv) match desktop
 * exactly. Renders a workspace/role strip + grouped cards with icon tiles,
 * descriptions, chevrons, and active/locked states — a premium navigation hub, not
 * a plain row list.
 */
export default function MenuPage({ persona }: { persona: Persona }) {
  const { t, i18n } = useTranslation();
  const locale = (useParams<{ locale: Locale }>().locale ?? (i18n.language?.startsWith('ru') ? 'ru' : 'uz')) as Locale;
  const me = useSession((s) => s.me);

  // Workspace / role for the account strip — brand name for the platform, tenant
  // name for a business; role caption from the user's real platform/tenant role.
  const tenant = me?.tenants?.[0];
  // The shared name chain (full name → @handle → fallback). This is the one
  // caller that overrides the fallback: the name is a FRAGMENT of the
  // "Ism · Rol" caption below, where an em-dash would read as a missing item
  // rather than as "no name", so an absent name simply drops out of the join.
  const userName = displayName(
    { first: me?.firstName, last: me?.lastName, username: me?.telegramUsername },
    { fallback: '' },
  );
  const roleLabel = persona === 'platform'
    ? (me?.platformRole === 'super_admin' ? t('menu.roleSuperAdmin', 'Bosh administrator')
      : me?.platformRole === 'support' ? t('menu.roleSupport', 'Qo‘llab-quvvatlash')
      : t('menu.rolePlatform', 'Platforma admini'))
    : tenant?.role === 'owner' ? t('menu.roleOwner', 'Egasi')
      : tenant?.role === 'manager' ? t('menu.roleManager', 'Menejer')
      : tenant?.role === 'sales' ? t('menu.roleSales', 'Sotuvchi')
      : persona === 'staff' ? t('menu.roleStaff', 'Xodim') : '';
  const workspace = persona === 'platform' ? 'Muddato' : (tenant?.name ?? 'Muddato');
  const caption = [userName, roleLabel].filter(Boolean).join(' · ');

  const items = useMemo(() => menuItemsFor(persona, locale, t), [persona, locale, t]);

  // Group consecutive items by their (already-localized) section label.
  const groups = useMemo(() => {
    const out: { section: string; rows: typeof items }[] = [];
    for (const it of items) {
      const section = it.section ?? '';
      const last = out[out.length - 1];
      if (last && last.section === section) last.rows.push(it);
      else out.push({ section, rows: [it] });
    }
    return out;
  }, [items]);

  return (
    <>
      <div className="hidden md:block">
        <PageHeader title={t('common.menu', 'Menyu')} subtitle={t('menu.subtitle', 'Qo‘shimcha bo‘limlar va sozlamalar')} />
      </div>
      <div className="md:hidden">
        <MobileHeader title={t('common.menu', 'Menyu')} />
        <p className="mb-4 text-small leading-snug text-muted">{t('menu.subtitle', 'Qo‘shimcha bo‘limlar va sozlamalar')}</p>
      </div>

      <div className="mx-auto w-full max-w-lead space-y-6">
        {/* Workspace / role strip */}
        <div className="flex items-center gap-3 rounded-card border border-line bg-surface p-4">
          <Avatar name={workspace} size="md" />
          <div className="min-w-0 flex-1">
            <div className="truncate text-subsection font-semibold tracking-tight">{workspace}</div>
            {caption && <div className="truncate text-caption text-muted">{caption}</div>}
          </div>
        </div>

        {groups.map(({ section, rows }) => (
          <section key={section || 'sec'}>
            {/* A group heading earns its place only when there is more than one
                group to tell apart. With a single section it sits above every
                row on the screen and divides nothing — the same "label that
                labels everything" the settings breadcrumb was. */}
            {section && groups.length > 1 && <div className="kicker px-1 pb-2">{section}</div>}
            <ul className="overflow-hidden rounded-card border border-line bg-surface divide-y divide-line">
              {rows.map((it) => {
                const Icon = it.icon;
                const descKey = MENU_DESC_KEY[it.key];
                const desc = descKey ? t(descKey, '') : '';
                if (it.locked) {
                  return (
                    <li key={it.key} className="flex items-center gap-3 px-4 py-3.5 text-muted">
                      <span className="grid h-10 w-10 shrink-0 place-items-center rounded-control bg-cream"><Icon size={18} /></span>
                      <span className="min-w-0 flex-1">
                        <span className="block truncate text-body font-medium">{it.label}</span>
                        {desc && <span className="block truncate text-caption text-muted">{desc}</span>}
                      </span>
                      <Lock size={16} className="shrink-0" />
                    </li>
                  );
                }
                return (
                  <li key={it.key}>
                    <NavLink
                      to={it.to}
                      end
                      className={({ isActive }) => cn(
                        'flex items-center gap-3 px-4 py-3.5 transition tap-target',
                        isActive ? 'bg-ink/[0.04]' : 'hover:bg-ink/[0.03] active:bg-ink/[0.05]',
                      )}
                    >
                      {({ isActive }) => (
                        <>
                          <span className={cn(
                            'grid h-10 w-10 shrink-0 place-items-center rounded-control transition',
                            isActive ? 'bg-ink text-white' : 'bg-[color:var(--c-accent-soft)] text-[color:var(--c-accent-strong)]',
                          )}>
                            <Icon size={18} />
                          </span>
                          <span className="min-w-0 flex-1">
                            <span className="block truncate text-body font-medium text-ink">{it.label}</span>
                            {desc && <span className="block truncate text-caption text-muted">{desc}</span>}
                          </span>
                          <ChevronRight size={16} className="shrink-0 text-muted" />
                        </>
                      )}
                    </NavLink>
                  </li>
                );
              })}
            </ul>
          </section>
        ))}
      </div>
    </>
  );
}
