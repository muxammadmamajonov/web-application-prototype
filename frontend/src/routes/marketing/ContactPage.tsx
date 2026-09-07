import { useState } from 'react';
import { useParams } from 'react-router';
import { useTranslation } from 'react-i18next';
import {
  AlertCircle, Building2, CheckCircle2, Headphones, Loader2, MapPin,
  Megaphone, Send,
} from 'lucide-react';
import { Container } from '@/components/ui/Container';
import { Button } from '@/components/ui/Button';
import { Field, inputCls } from '@/components/ui/Modal';
import { PublicShell } from '@/components/marketing/PublicLayout';
import { SmartContact, isValidEmail, isValidPhone } from '@/components/ui/SmartContact';
import { Textarea } from '@/components/ui/FormFields';
import { focusFirstInvalid, hasErrors } from '@/lib/forms';
import { linkifySupport } from '@/lib/linkify';
import { api } from '@/lib/api';
import { apiErrorMessage } from '@/lib/errors';
import { CHANNEL_USERNAME } from '@/lib/telegram';
import { useSupportUsername } from '@/hooks/useSupportUsername';

// Office coordinates (Sharof Rashidov 93A, Yunusobod, Tashkent)
const OFFICE_LAT = 41.329990;
const OFFICE_LNG = 69.276130;

export default function ContactPage() {
  const { i18n } = useTranslation();
  const { locale } = useParams<{ locale?: string }>();
  const lng = (locale ?? i18n.language ?? 'uz') as 'uz' | 'ru';
  const { t } = useTranslation('translation', { lng, keyPrefix: 'contact' });

  return (
    <PublicShell>
      <section className="border-b border-line section-hero">
        <Container>
          <div className="kicker text-accent">{t('kicker')}</div>
          <h1 className="mt-2 h-hero-2 font-semibold tracking-tight leading-[1.05]">{t('title')}</h1>
          <p className="mt-4 max-w-lead text-ink-soft text-subsection">{t('sub')}</p>
        </Container>
      </section>

      <section className="section-band">
        <Container className="grid grid-cols-1 gap-10 lg:grid-cols-[1.4fr_1fr]">
          <ContactForm lng={lng} />
          <ContactDetails lng={lng} />
        </Container>
      </section>
    </PublicShell>
  );
}

function ContactForm({ lng }: { lng: 'uz' | 'ru' }) {
  const { t } = useTranslation('translation', { lng, keyPrefix: 'contact' });
  const support = useSupportUsername();
  const [title, setTitle] = useState('');
  const [contact, setContact] = useState('');
  const [message, setMessage] = useState('');
  const [busy, setBusy] = useState(false);
  const [status, setStatus] = useState<'idle' | 'ok' | 'error'>('idle');
  const [err, setErr] = useState<string | null>(null);            // server error (banner)
  const [errors, setErrors] = useState<Record<string, string>>({}); // per-field client errors

  const clearErr = (k: string) => setErrors((e) => (e[k] ? { ...e, [k]: '' } : e));

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (busy) return;
    // Field-level client validation — errors render under each field + focus the
    // first invalid one; the contact may be a phone OR an email.
    const fe: Record<string, string> = {};
    if (title.trim().length < 2) fe.title = t('errTitle');
    if (!(isValidPhone(contact) || isValidEmail(contact))) fe.contact = t('errContact');
    setErrors(fe);
    if (hasErrors(fe)) { focusFirstInvalid(); return; }
    setBusy(true); setErr(null); setStatus('idle');
    try {
      await api.post('/contact/submit', {
        title, contact,
        contact_kind: isValidPhone(contact) ? 'phone' : 'email',
        message, locale: lng,
      });
      setStatus('ok');
      setTitle(''); setContact(''); setMessage('');
    } catch (e2) {
      setErr(apiErrorMessage(e2));   // localized; never the raw backend token
      setStatus('error');
    } finally { setBusy(false); }
  };

  if (status === 'ok') {
    return (
      <div className="card flex flex-col items-center gap-3 p-10 text-center">
        <div className="grid h-14 w-14 place-items-center rounded-pill bg-[color:var(--c-success-soft)] text-success">
          <CheckCircle2 size={28} />
        </div>
        <h2 className="text-title font-semibold tracking-tight">{t('thanksTitle')}</h2>
        <p className="max-w-panel text-body text-ink-soft">{linkifySupport(t('thanksBody'), support)}</p>
        <button onClick={() => setStatus('idle')} className="mt-3 text-body text-accent hover:underline">
          {t('sendAgain')}
        </button>
      </div>
    );
  }

  return (
    <form onSubmit={submit} className="card flex flex-col gap-5 p-6 sm:p-8" noValidate>
      <Field label={t('fields.title')} required error={errors.title}>
        <input
          maxLength={120}
          value={title}
          onChange={(e) => { setTitle(e.target.value); clearErr('title'); }}
          className={inputCls}
          placeholder={t('fields.titlePlaceholder')}
        />
      </Field>

      <div>
        <SmartContact
          required
          label={t('fields.contact')}
          value={contact}
          onChange={(v) => { setContact(v); clearErr('contact'); }}
          error={!!errors.contact}
        />
        {errors.contact && (
          <span role="alert" className="mt-1 block text-micro font-medium text-danger">{errors.contact}</span>
        )}
      </div>

      <Field label={t('fields.message')}>
        <Textarea
          rows={5}
          maxLength={2000}
          autosize
          value={message}
          onChange={setMessage}
          placeholder={t('fields.messagePlaceholder')}
        />
      </Field>

      {status === 'error' && err && (
        <div role="alert" className="flex items-start gap-2 rounded-control border border-danger/30 bg-[color:var(--c-danger-soft)] px-4 py-3 text-body text-danger">
          <AlertCircle size={16} className="mt-0.5 shrink-0" /><span>{err}</span>
        </div>
      )}

      <Button type="submit" variant="primary" size="lg" loading={busy}>
        {busy
          ? (<><Loader2 size={18} className="animate-spin" /> {t('sending')}</>)
          : (<><Send size={18} /> {t('submit')}</>)}
      </Button>
    </form>
  );
}

function ContactDetails({ lng }: { lng: 'uz' | 'ru' }) {
  const { t } = useTranslation('translation', { lng, keyPrefix: 'contact' });
  const support = useSupportUsername();
  return (
    <aside className="flex flex-col gap-5">
      <div className="card p-6">
        <h3 className="text-subsection font-semibold">{t('directLinks')}</h3>
        <ul className="mt-4 space-y-3 text-body">
          <li>
            <a
              href={`https://t.me/${CHANNEL_USERNAME}`}
              target="_blank" rel="noreferrer"
              className="flex items-center gap-3 rounded-control border border-line bg-cream px-3 py-3 hover:border-ink/20"
            >
              <span className="grid h-9 w-9 place-items-center rounded-icon bg-[color:var(--c-accent-soft)] text-accent">
                <Megaphone size={16} />
              </span>
              <span className="flex-1">
                <div className="font-medium">@{CHANNEL_USERNAME}</div>
                <div className="text-caption text-muted">{t('channelDesc')}</div>
              </span>
            </a>
          </li>
          <li>
            <a
              href={`https://t.me/${support}`}
              target="_blank" rel="noreferrer"
              className="flex items-center gap-3 rounded-control border border-line bg-cream px-3 py-3 hover:border-ink/20"
            >
              <span className="grid h-9 w-9 place-items-center rounded-icon bg-[color:var(--c-success-soft)] text-success">
                <Headphones size={16} />
              </span>
              <span className="flex-1">
                <div className="font-medium">@{support}</div>
                <div className="text-caption text-muted">{t('supportDesc')}</div>
              </span>
            </a>
          </li>
        </ul>
      </div>

      <div className="card overflow-hidden p-0">
        <div className="px-6 py-5 border-b border-line">
          <h3 className="flex items-center gap-2 text-subsection font-semibold">
            <Building2 size={16} className="text-accent" />
            {t('officeTitle')}
          </h3>
          <p className="mt-1 text-body text-ink-soft">
            <MapPin size={14} className="inline -mt-0.5 mr-1" />
            Sharof Rashidov 93A · Yunusobod · Toshkent
          </p>
        </div>
        <a
          href={`https://yandex.uz/maps/?ll=${OFFICE_LNG}%2C${OFFICE_LAT}&z=17&pt=${OFFICE_LNG}%2C${OFFICE_LAT}`}
          target="_blank" rel="noreferrer"
          className="block aspect-[16/10] relative bg-cream"
          aria-label={t('openInYandex')}
        >
          <iframe
            title={t('officeMapTitle')}
            className="absolute inset-0 h-full w-full border-0 pointer-events-none"
            loading="lazy"
            referrerPolicy="no-referrer-when-downgrade"
            src={`https://yandex.uz/map-widget/v1/?ll=${OFFICE_LNG}%2C${OFFICE_LAT}&z=17&mode=search&text=${OFFICE_LAT}%2C${OFFICE_LNG}&pt=${OFFICE_LNG}%2C${OFFICE_LAT},pm2rdm`}
          />
        </a>
      </div>
    </aside>
  );
}
