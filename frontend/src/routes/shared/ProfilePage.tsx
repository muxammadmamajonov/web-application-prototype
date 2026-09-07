import { Suspense, useCallback, useEffect, useState } from 'react';
import { VerifiedPhoneRow } from '@/components/ui/VerifiedPhoneRow';
import type { IdentitySnapshot } from '@/components/ui/PhoneVerificationCard';
import { useMyIdentity } from '@/lib/queries';
import { useTranslation } from 'react-i18next';
import type { TFunction } from 'i18next';
import {
  Building2, CheckCircle2, Check, Globe, HelpCircle, Loader2, LogOut, Mail,
  Send, ShieldCheck, User as UserIcon,
} from 'lucide-react';
import { lazyWithRetry } from '@/lib/lazyWithRetry';
import { SettingsShell, type SettingsTab } from '@/components/layout/SettingsShell';
import { Avatar } from '@/components/ui/Avatar';
import { Button } from '@/components/ui/Button';
import { Field, inputCls } from '@/components/ui/Modal';
import { EmailInput } from '@/components/ui/FormFields';
import { Select } from '@/components/ui/Select';
import { DatePicker } from '@/components/ui/DatePicker';
import { displayName, localToday } from '@/lib/format';
import { apiErrorMessage } from '@/lib/errors';
import { toast } from '@/lib/feedback';
import { useSession } from '@/store/session';
import type { MeResponse } from '@/types';
import { logout } from '@/lib/auth';
import { useUpdateProfile } from '@/lib/mutations';
import { CHANNEL_USERNAME, isInsideTelegramClient } from '@/lib/telegram';
import { useSupportUsername } from '@/hooks/useSupportUsername';
import { useSupportChannels } from '@/lib/queries';

type TFn = TFunction;

// Router.tsx already declares this as `./client/PrivacyPage` for the standalone
// `/me/privacy` route — lazy here too, or this chunk gets pulled into the
// eager ProfilePage bundle a second time regardless of that declaration.
const ClientPrivacyPage = lazyWithRetry(() => import('../client/PrivacyPage'));

const TabLoading = () => (
  <div className="grid min-h-[30vh] place-items-center text-body text-muted">
    <Loader2 size={18} className="animate-spin" />
  </div>
);

/**
 * Profile — same two-pane master/detail as Settings (SettingsShell): a rail of
 * sub-pages (Details / Language / Help / Businesses) beside the detail on
 * desktop, drill-in on mobile. Used by every persona (client, staff, tenant
 * owner, platform admin). The avatar summary sits above the rail; logout below.
 */
export default function ProfilePage() {
  const { t, i18n } = useTranslation();
  const me = useSession((s) => s.me);
  const update = useUpdateProfile();
  const [saved, setSaved] = useState(false);

  // The server's view of this person's proved phone. Read here, never derived:
  // the profile screen must not be a second opinion about identity.
  const identityQ = useMyIdentity();
  const [identity, setIdentity] = useState<IdentitySnapshot | null>(null);
  useEffect(() => { if (identityQ.data) setIdentity(identityQ.data); }, [identityQ.data]);
  const refetchIdentity = useCallback(
    async () => (await identityQ.refetch()).data ?? null,
    [identityQ],
  );

  // No `phone` here. The account's number is not part of this form — it is
  // proved, not typed, and `VerifiedPhoneRow` owns it.
  const [form, setForm] = useState({
    display_name: '', email: '',
    birthdate: '', gender: '' as '' | 'male' | 'female',
  });

  useEffect(() => {
    if (!me) return;
    setForm({
      // The person's own name, not Telegram's. Seeding from `firstName` would
      // put a value in the box that saving cannot keep.
      display_name: me.displayName ?? '',
      email: me.email ?? '',
      birthdate: me.birthdate ?? '',
      gender: (me.gender ?? '') as '' | 'male' | 'female',
    });
  }, [me]);

  const saveDetails = async () => {
    try {
      await update.mutateAsync({
        display_name: form.display_name.trim(),
        // `phone` is deliberately absent from the payload: the server refuses a
        // number that is not the one already proved, and this form has nothing
        // to say about it.
        email: form.email.trim(),
        birthdate: form.birthdate,
        gender: form.gender,
        locale: (me?.locale ?? 'uz') as 'uz' | 'ru',
      });
      setSaved(true);
      setTimeout(() => setSaved(false), 2000);
    } catch (ex) {
      toast.error(apiErrorMessage(ex));
    }
  };

  const pickLanguage = async (locale: 'uz' | 'ru') => {
    if (locale !== i18n.language) i18n.changeLanguage(locale);
    try {
      await update.mutateAsync({ locale });
      toast.success(t('common.saved', 'Saqlandi'));
    } catch { /* language already switched client-side */ }
  };

  const hasTenants = (me?.tenants?.length ?? 0) > 0;
  const tabs: SettingsTab[] = [
    { key: 'details',  label: t('profile.menuDetails', 'Shaxsiy maʼlumotlar'), sub: t('profile.menuDetailsSub', 'Ism, telefon, sana'), icon: UserIcon },
    { key: 'language', label: t('profile.menuLanguage', 'Til'), sub: me?.locale === 'ru' ? 'Русский' : "O'zbekcha", icon: Globe },
    { key: 'help',     label: t('profile.menuHelp', 'Yordam'), sub: t('profile.menuHelpSub', 'Qo‘llab-quvvatlash bilan bog‘lanish'), icon: HelpCircle },
    { key: 'privacy',  label: t('clientPrivacy.title'), sub: t('clientPrivacy.subtitle'), icon: ShieldCheck },
    ...(hasTenants
      ? [{ key: 'businesses', label: t('profile.businessesSection', 'Bizneslarim'), sub: String(me!.tenants.length), icon: Building2 } as SettingsTab]
      : []),
  ];

  // The shared name chain (full name → @handle → em-dash). This card used to
  // stop at the em-dash, so a nameless Telegram user read "—" here and
  // "@johndoe" on every list screen. The handle link below is suppressed when
  // the handle is ALREADY standing in as the name, so unifying the chain never
  // prints the same "@johndoe" twice in one two-line card.
  // The resolved name the server decided on; the Telegram profile is the
  // fallback it already applied, not a second source to merge in here.
  const name = me?.displayName
    ?? displayName({ first: me?.firstName, last: me?.lastName, username: me?.telegramUsername });
  const showHandleLink = !!me?.telegramUsername && name !== `@${me.telegramUsername}`;

  const railTop = (
    <section className="card p-5">
      <div className="flex items-center gap-3">
        <Avatar name={me?.firstName ?? me?.telegramUsername ?? '?'} photoUrl={me?.photoUrl} size="lg" />
        <div className="min-w-0">
          {/* Two lines, not an ellipsis. This rail card is narrow by design, so
              `truncate` clipped ordinary full names ("Example User" →
              "Muxammad Mamaj…") on the one screen whose subject IS the person.
              A name is worth a second line; `line-clamp-2` still bounds it. */}
          <div className="line-clamp-2 text-subsection font-semibold leading-snug">{name}</div>
          {showHandleLink && (
            <a href={`https://t.me/${me!.telegramUsername}`} target="_blank" rel="noreferrer" className="text-small text-accent hover:underline">
              @{me!.telegramUsername}
            </a>
          )}
        </div>
      </div>
    </section>
  );

  const footer = isInsideTelegramClient() ? undefined : (
    <Button variant="outline" size="md" onClick={() => logout()} className="w-full">
      <LogOut size={16} /> {t('common.logout')}
    </Button>
  );

  return (
    <SettingsShell title={t('nav.profile')} tabs={tabs} railTop={railTop} footer={footer}>
      {(key) => {
        switch (key) {
          case 'details':    return <DetailsView form={form} setForm={setForm} onSave={saveDetails} saving={update.isPending} saved={saved} t={t}
                              identity={identity} refetchIdentity={refetchIdentity} setIdentity={setIdentity} />;
          case 'language':   return <LanguageView current={(me?.locale ?? 'uz') as 'uz' | 'ru'} onPick={pickLanguage} />;
          case 'help':       return <HelpView t={t} />;
          case 'privacy':    return <Suspense fallback={<TabLoading />}><ClientPrivacyPage embedded /></Suspense>;
          case 'businesses': return <BusinessesView me={me} t={t} />;
          default:           return null;
        }
      }}
    </SettingsShell>
  );
}

// ── Sub-views (rendered inside the shell's detail pane) ──────────────────────

/**
 * The body of one Profile sub-page. Header-free on purpose.
 *
 * Each of these used to repeat the icon, title and hint of the rail row sitting
 * highlighted right beside it — the same translation keys, the same glyph, about
 * 24px away — and on mobile the same label `MobileHeader` had just shown. That
 * is the settings breadcrumb again: a third statement of where you already are.
 */
function SectionCard({ children }: { children: React.ReactNode }) {
  return <section className="card p-6">{children}</section>;
}

function DetailsView({ form, setForm, onSave, saving, saved, t,
                      identity, refetchIdentity, setIdentity }: {
  identity: IdentitySnapshot | null;
  refetchIdentity: () => Promise<IdentitySnapshot | null>;
  setIdentity: (s: IdentitySnapshot) => void;
  form: { display_name: string; email: string; birthdate: string; gender: '' | 'male' | 'female' };
  setForm: React.Dispatch<React.SetStateAction<typeof form>>;
  onSave: () => void; saving: boolean; saved: boolean; t: TFn;
}) {
  return (
    <SectionCard>
      <div className="grid gap-3 md:grid-cols-2">
        {/* One field, because one name is what the product addresses people by.
            Two boxes implied Telegram's given/family pair, which this does not
            write and cannot keep. */}
        <Field label={t('profile.displayName', 'Ism va familiya')}
               hint={t('profile.displayNameHint', 'Ilovada shu ism ko‘rinadi')}>
          <input className={inputCls} maxLength={120} value={form.display_name}
                 data-testid="profile-display-name"
                 onChange={(e) => setForm((f) => ({ ...f, display_name: e.target.value }))}
                 placeholder={t('profile.displayName', 'Ism va familiya')} />
        </Field>
        {/* ONE phone number, and it is not a text box.

            A second field lived here — "Bogʻlanish uchun telefon", free text,
            saved straight to the account — directly beside the proved number
            above. Two boxes, one identity: whichever the person filled in last
            was the one some screen would show, and the typed one reached nobody
            and proved nothing. The account's number is the one Telegram
            verified; changing it means proving a new one, which is what the row
            above is for. */}
        <VerifiedPhoneRow snapshot={identity} onRefetch={refetchIdentity}
                          onVerified={setIdentity} />
        <Field label={t('profile.email', 'E-pochta')}>
          <EmailInput value={form.email} onChange={(v) => setForm((f) => ({ ...f, email: v }))} />
        </Field>
        <Field label={t('profile.birthdate', 'Tug‘ilgan sana')}>
          <DatePicker value={form.birthdate} max={localToday()}
                      onChange={(iso) => setForm((f) => ({ ...f, birthdate: iso }))} />
        </Field>
        <Field label={t('profile.gender', 'Jins')}>
          <Select
            value={form.gender}
            onChange={(v) => setForm((f) => ({ ...f, gender: v as '' | 'male' | 'female' }))}
            options={[
              { value: '', label: t('profile.genderUnset', 'Tanlanmagan') },
              { value: 'male', label: t('profile.male', 'Erkak') },
              { value: 'female', label: t('profile.female', 'Ayol') },
            ]}
          />
        </Field>
      </div>
      <p className="mt-4 text-caption text-muted">
        {t('profile.detailsNote', 'Ism, familiya va rasm Telegram’dan ham yangilanishi mumkin. Eslatmalar Telegram bot orqali keladi.')}
      </p>
      <Button variant="primary" size="md" onClick={onSave} loading={saving} className="mt-5 w-full sm:w-auto">
        {saving ? <Loader2 size={16} className="animate-spin" /> : <CheckCircle2 size={16} />}
        {saved ? t('common.saved', 'Saqlandi') : t('common.save')}
      </Button>
    </SectionCard>
  );
}

function LanguageView({ current, onPick }: {
  current: 'uz' | 'ru'; onPick: (l: 'uz' | 'ru') => void;
}) {
  const opts: Array<{ code: 'uz' | 'ru'; label: string; native: string }> = [
    { code: 'uz', label: "O'zbekcha", native: 'Uzbek' },
    { code: 'ru', label: 'Русский', native: 'Russian' },
  ];
  return (
    <SectionCard>
      <ul className="space-y-2.5">
        {opts.map((o) => (
          <li key={o.code}>
            <button onClick={() => onPick(o.code)}
                    aria-current={current === o.code ? 'true' : undefined}
                    className="flex w-full items-center justify-between rounded-control border border-line px-4 py-4 text-left hover:bg-ink/5">
              <span>
                <span className="block text-body font-medium">{o.label}</span>
                <span className="block text-caption text-muted">{o.native}</span>
              </span>
              {current === o.code && <Check size={18} className="text-accent" />}
            </button>
          </li>
        ))}
      </ul>
    </SectionCard>
  );
}

function HelpView({ t }: { t: TFn }) {
  // The support handle and corporate address come from platform settings, which
  // an operator edits on Sozlamalar → Aloqa kanali. They used to be frontend
  // constants, so that settings form was a set of fields whose values went
  // nowhere: changing the support handle reached no user, and this page showed
  // `support@muddato.uz` while the stored corporate email was empty. The
  // constants remain as the fallback for a cold cache or an older backend.
  const channels = useSupportChannels();
  const supportUser = useSupportUsername();
  const email = channels.data?.company_email;
  const items = [
    { icon: Send, title: `@${supportUser}`, sub: t('profile.supportTg', 'Qo‘llab-quvvatlash xizmati'), href: `https://t.me/${supportUser}` },
    { icon: Send, title: `@${CHANNEL_USERNAME}`, sub: t('profile.channel', 'Rasmiy kanal · yangiliklar'), href: `https://t.me/${CHANNEL_USERNAME}` },
    // Only when one is actually configured — an address nobody has been told to
    // watch is worse than no address at all.
    ...(email
      ? [{ icon: Mail, title: email, sub: t('profile.supportEmail', 'E-pochta orqali yozing'), href: `mailto:${email}` }]
      : []),
  ];
  return (
    <SectionCard>
      <ul className="space-y-2.5">
        {items.map((it) => (
          <li key={it.title}>
            <a href={it.href} target="_blank" rel="noreferrer"
               className="flex items-center gap-3 rounded-control border border-line px-4 py-3 hover:border-ink/20">
              <span className="grid h-9 w-9 place-items-center rounded-control bg-[color:var(--c-accent-soft)] text-accent"><it.icon size={16} /></span>
              <span className="min-w-0">
                <span className="block truncate text-body font-medium">{it.title}</span>
                <span className="block truncate text-caption text-muted">{it.sub}</span>
              </span>
            </a>
          </li>
        ))}
      </ul>
    </SectionCard>
  );
}

/** Tenant role label — reuses the same translated strings the Menu hub's
 *  workspace strip already uses for this exact value (menu.role*), so "owner"
 *  reads identically everywhere instead of leaking the raw English code. */
function tenantRoleLabel(role: 'owner' | 'manager' | 'sales', t: TFn): string {
  switch (role) {
    case 'owner':   return t('menu.roleOwner', 'Egasi');
    case 'manager': return t('menu.roleManager', 'Menejer');
    case 'sales':   return t('menu.roleSales', 'Sotuvchi');
    default:        return role;
  }
}

function BusinessesView({ me, t }: { me: MeResponse | null; t: TFn }) {
  return (
    <SectionCard>
      <ul className="space-y-2">
        {(me?.tenants ?? []).map((tn) => (
          <li key={tn.id} className="flex items-center justify-between rounded-control border border-line px-4 py-3">
            <div className="min-w-0">
              <div className="truncate font-medium">{tn.name}</div>
              <div className="text-caption text-muted">{tenantRoleLabel(tn.role, t)} · {t(`status.${tn.status}`, tn.status)}</div>
            </div>
            <span className="kicker font-mono">{tn.planCode}</span>
          </li>
        ))}
      </ul>
    </SectionCard>
  );
}
