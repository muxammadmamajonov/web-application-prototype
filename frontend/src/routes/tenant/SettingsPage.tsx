import { useState } from 'react';
import { Link, useParams } from 'react-router';
import { useTranslation } from 'react-i18next';
import { useQuery } from '@tanstack/react-query';
import {
  Bell, Bot, Building2, Calculator, CalendarDays, CheckCircle2, CreditCard,
  ExternalLink, FileText, Loader2, Lock, MessageSquare, Plus, ShieldCheck, Trash2, UserPlus,
} from 'lucide-react';
import { queryKeys } from '@/lib/queryKeys';
import { SettingsShell, type SettingsTab } from '@/components/layout/SettingsShell';
import { V2_SURFACES_ENABLED } from '@/routes/_scope';
import { Switch, SwitchTrack } from '@/components/ui/Switch';
import { EmptyState } from '@/components/ui/EmptyState';
import { Button } from '@/components/ui/Button';
import { Field, inputCls } from '@/components/ui/Modal';
import { PhoneInput, EmailInput, TelegramInput, Textarea, PercentInput } from '@/components/ui/FormFields';
import { phoneOrEmpty } from '@/lib/phone';
import { SUPPORTED_CURRENCIES } from '@/lib/currency';
import { Select } from '@/components/ui/Select';
import { TimePicker } from '@/components/ui/TimePicker';
import { CardManager } from '@/components/ui/CardManager';
import { OFFERED_PAYMENT_METHODS, isOffered } from '@/lib/paymentMethods';
import { validateLogoUrl, LOGO_URL_MAX, type LogoUrlError } from '@/lib/logoUrl';
import { PaymentProviderCard } from '@/components/ui/PaymentProviderCard';
import { cn } from '@/lib/cn';
import { confirmDialog } from '@/lib/feedback';
import { apiErrorMessage } from '@/lib/errors';
import { api } from '@/lib/api';
import {
  useTenantSettings,
  type TenantSettingsBundle, type CustomFieldDef,
} from '@/lib/queries';
import {
  usePatchTenantSettings, useActivateTenantBot, useDeactivateTenantBot,
} from '@/lib/mutations';

const LOGO_URL_FALLBACK: Record<LogoUrlError, string> = {
  too_short:  'Havola juda qisqa',
  too_long:   'Havola juda uzun',
  bad_scheme: "Havola https:// bilan boshlanishi kerak",
  malformed:  "Havola noto'g'ri",
};

type TabKey = 'profile' | 'calc' | 'payments' | 'reminders' | 'bot' | 'customerForm' | 'contract' | 'risk' | 'sms';

/**
 * Tenant Settings — sub-pages addressed via the `?tab=` query param (see
 * SettingsShell): deep-linkable, refresh-safe, and Back-button aware, while
 * still swapping in-place without a full page-load.
 *
 *   Profile   — biznes profili (nomi, logo, manzil, ish vaqti, aloqa)
 *   Calc      — bo'lib to'lash standart formulasi + chegaralar
 *   Payments  — Click/Payme/Cash/Card sozlash
 *   Reminders — bot eslatma matnlari + offsetlari + qaerga yuborish
 */
export default function TenantSettingsPage() {
  const { t } = useTranslation();
  const q = useTenantSettings();

  // (#448) Nine tabs read as one undifferentiated wall — cluster them and badge
  // the not-yet-functional stubs (SMS, Contract custom-template) "Tez orada".
  const gBusiness = t('tenantSettings.groupBusiness', 'Biznes');
  const gMoney    = t('tenantSettings.groupMoney', 'Pul qoidalari');
  const gComms    = t('tenantSettings.groupComms', 'Aloqa');
  const gAdvanced = t('tenantSettings.groupAdvanced', "Qo'shimcha");
  // Only the four groups that govern how the shop actually runs: who the
  // business is (incl. the transfer details customers pay into), the shared bot,
  // the default contract terms, and the payment methods offered. Tabs for
  // deferred capabilities are retained behind the scope flag.
  const tabs: SettingsTab[] = [
    { key: 'profile',      group: gBusiness, label: t('tenantSettings.tabProfile'),      sub: t('tenantSettings.subProfile', 'Nom, logo, aloqa'), icon: Building2 },
    { key: 'bot',          group: gBusiness, label: t('tenantSettings.tabBot'),          sub: t('tenantSettings.subBot', 'Telegram bot'), icon: Bot },
    { key: 'calc',         group: gMoney,    label: t('tenantSettings.tabCalc'),         sub: t('tenantSettings.subCalc', "Bo'lib to'lash sozlamalari"), icon: Calculator },
    { key: 'payments',     group: gMoney,    label: t('tenantSettings.tabPayments'),     sub: t('tenantSettings.subPayments', "To'lov usullari"), icon: CreditCard },
    ...(V2_SURFACES_ENABLED ? [
      { key: 'customerForm', group: gBusiness, label: t('tenantSettings.tabCustomerForm'), sub: t('tenantSettings.subCustomerForm', 'Mijoz formasi maydonlari'), icon: UserPlus },
      { key: 'reminders',    group: gComms,    label: t('tenantSettings.tabReminders'),    sub: t('tenantSettings.subReminders', 'Eslatma jadvali'), icon: Bell },
      { key: 'sms',          group: gComms,    label: t('tenantSettings.tabSms', 'SMS'),    sub: t('tenantSettings.subSms', 'SMS byudjeti'), icon: MessageSquare, soon: true },
      { key: 'contract',     group: gAdvanced, label: t('tenantSettings.tabContract', 'Shartnoma'), sub: t('tenantSettings.subContract', 'Shartnoma shabloni'), icon: FileText, soon: true },
      { key: 'risk',         group: gAdvanced, label: t('tenantSettings.tabRisk', 'Risk tarmog\'i'), sub: t('tenantSettings.subRisk', 'Risk tarmog\'i'), icon: ShieldCheck },
    ] : []),
  ];

  return (
    <SettingsShell
      title={t('nav.businessSettings')}
      tabs={tabs}
      loading={q.isLoading}
      error={q.error}
      onRetry={() => { void q.refetch(); }}
    >
      {(key) => {
        const data = q.data;
        if (!data) return null;
        switch (key as TabKey) {
          case 'profile':      return <ProfileTab data={data} />;
          case 'bot':          return <BotTab data={data} />;
          case 'customerForm': return <CustomerFormTab data={data} />;
          case 'calc':         return <CalcTab data={data} />;
          case 'payments':     return <PaymentsTab data={data} />;
          case 'reminders':    return <RemindersTab data={data} />;
          case 'sms':          return <SmsTab data={data} />;
          case 'contract':     return <ContractTab data={data} />;
          case 'risk':         return <RiskTab data={data} />;
          default:             return null;
        }
      }}
    </SettingsShell>
  );
}

// ── RiskTab — cross-tenant risk network opt-in (Round 23) ─────────────

function RiskTab({ data }: { data: TenantSettingsBundle }) {
  const { t } = useTranslation();
  const patch = usePatchTenantSettings();
  const [optIn, setOptIn] = useState<boolean>(data.risk?.risk_network_opt_in ?? false);
  const saveState = useSettingsSave();

  const save = () => saveState.run(() => patch.mutateAsync({ risk: { risk_network_opt_in: optIn } }));

  return (
    <SectionLayout
      title={t('tenantSettings.riskTitle', 'Risk tarmog\'i')}
      hint={t('tenantSettings.riskHint', 'Boshqa do\'konlar bilan birgalikda firibgar va kechikuvchi mijozlardan himoyalaning.')}
    >
      <label className="flex items-start gap-3 rounded-control border border-line bg-surface p-4">
        <Switch className="mt-0.5" checked={optIn} onChange={setOptIn} />
        <div className="min-w-0 flex-1">
          <div className="text-body font-medium">
            {t('tenantSettings.riskOptInLabel', 'Risk tarmog\'iga qo\'shilish')}
          </div>
          <div className="text-caption text-muted">
            {t('tenantSettings.riskOptInHint', 'Yoqilganda: 60+ kun kechikkan mijozlaringiz (14 kunlik apellatsiyadan keyin) tarmoqqa qo\'shiladi va siz boshqa do\'konlardagi belgilarni ko\'rasiz. O\'chirilganda hech narsa baham ko\'rilmaydi.')}
          </div>
        </div>
      </label>

      <div className="mt-4 grid gap-3 md:grid-cols-3">
        <div className="rounded-control bg-cream p-4">
          <div className="text-micro uppercase tracking-wider text-muted">
            {t('risk.privacy1Title', 'Maxfiylik')}
          </div>
          <div className="mt-1 text-caption text-ink-soft">
            {t('risk.privacy1', 'JSHSHIR hech qachon ochiq ko\'rinishda chiqmaydi — faqat shifrlangan hash.')}
          </div>
        </div>
        <div className="rounded-control bg-cream p-4">
          <div className="text-micro uppercase tracking-wider text-muted">
            {t('risk.privacy2Title', 'Anonimlik')}
          </div>
          <div className="mt-1 text-caption text-ink-soft">
            {t('risk.privacy2', 'Siz faqat belgilar soni va darajasini ko\'rasiz — qaysi do\'kon ekanini emas.')}
          </div>
        </div>
        <div className="rounded-control bg-cream p-4">
          <div className="text-micro uppercase tracking-wider text-muted">
            {t('risk.privacy3Title', 'Adolat')}
          </div>
          <div className="mt-1 text-caption text-ink-soft">
            {t('risk.privacy3', 'Qarzni to\'lagan mijozning belgisi avtomatik olib tashlanadi.')}
          </div>
        </div>
      </div>

      <SaveBar onSave={save} pending={patch.isPending} ok={saveState.ok} error={saveState.err} />
    </SectionLayout>
  );
}

// ── SmsTab — Eskiz SMS fallback budget + usage + history (Round 25) ───

function SmsTab({ data }: { data: TenantSettingsBundle }) {
  const { t } = useTranslation();
  void data; // #53: no SMS provider integrated yet — nothing to read or save.
  return (
    <SectionLayout
      title={t('tenantSettings.smsTitle', 'SMS eslatma (zaxira kanal)')}
      hint={t('tenantSettings.smsSoonHint', "Telegram'i yo'q mijozlarga SMS orqali eslatma — tez orada.")}
    >
      <div className="rounded-control border border-dashed border-line bg-cream p-10 text-center">
        <div className="mx-auto grid h-11 w-11 place-items-center rounded-control bg-[color:var(--c-accent-soft)] text-accent">
          <MessageSquare size={20} />
        </div>
        <div className="mt-3 text-body font-semibold">{t('tenantSettings.smsSoonTitle', 'Tez orada')}</div>
        <p className="mx-auto mt-1 max-w-panel text-small text-muted">
          {t('tenantSettings.smsSoonBody', "SMS integratsiyasi hozircha ulanmagan. Tayyor bo'lgach, byudjet va sozlamalar shu yerda paydo bo'ladi.")}
        </p>
      </div>
    </SectionLayout>
  );
}

// ── ContractTab — Pro+ contract HTML editor + preview ─────────────────

function ContractTab({ data }: { data: TenantSettingsBundle }) {
  void data;
  const { t } = useTranslation();
  const [previewUrl, setPreviewUrl] = useState<string | null>(null);
  const [previewing, setPreviewing] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  const previewPdf = async () => {
    setPreviewing(true); setErr(null);
    try {
      const r = await api.get('/tenant/settings/contract-template/preview.pdf', {
        responseType: 'blob',
      });
      const url = URL.createObjectURL(r.data as Blob);
      if (previewUrl) URL.revokeObjectURL(previewUrl);
      setPreviewUrl(url);
    } catch (ex) {
      setErr(apiErrorMessage(ex));
    } finally {
      setPreviewing(false);
    }
  };

  return (
    <SectionLayout
      title={t('tenantSettings.contract.title', 'Shartnoma shabloni')}
      hint={t('tenantSettings.contract.hint', "Tayyor, professional shartnoma shabloni avtomatik ishlatiladi — HTML yozish shart emas.")}
    >
      <div className="grid gap-4">
        <div className="rounded-control border border-line bg-cream p-4">
          <div className="text-body font-semibold">{t('tenantSettings.contract.readyTitle', 'Tayyor shablon')}</div>
          <p className="mt-0.5 text-caption text-muted">{t('tenantSettings.contract.readyBody', "Har bir shartnoma uchun professional PDF avtomatik tayyorlanadi — do'kon nomi, mijoz va to'lov jadvali bilan.")}</p>
          <div className="mt-3">
            <Button variant="primary" size="md" onClick={previewPdf} loading={previewing}>
              {previewing ? <Loader2 size={14} className="animate-spin" /> : <FileText size={14} />}
              {t('tenantSettings.contract.previewBtn', "PDF ko'rish")}
            </Button>
          </div>
          {err && <p className="mt-2 text-caption text-danger">{err}</p>}
        </div>
        {previewUrl && (
          <iframe src={previewUrl} className="h-[560px] w-full rounded-control border border-line" title={t('tenantSettings.contract.previewTitle', 'Shartnoma preview')} />
        )}
        {/* #54: custom HTML template not completed — hidden until it's ready. */}
        <div className="rounded-control border border-dashed border-line bg-cream/60 px-4 py-3 text-caption text-muted">
          {t('tenantSettings.contract.customSoon', "O'z HTML shablonni yuklash — tez orada. Hozircha barcha shartnomalar tayyor shablon asosida tayyorlanadi.")}
        </div>
      </div>
    </SectionLayout>
  );
}

// ── BotTab ────────────────────────────────────────────────────────────

function BotTab({ data }: { data: TenantSettingsBundle }) {
  const { t } = useTranslation();
  const locale = useParams<{ locale: string }>().locale ?? 'uz';
  const activate   = useActivateTenantBot();
  const deactivate = useDeactivateTenantBot();
  const [token, setToken] = useState('');
  const [err, setErr] = useState<string | null>(null);
  const [ok, setOk] = useState<string | null>(null);

  const isActivated = !!data.bot?.bot_activated_at;
  const mode = data.bot?.bot_mode ?? 'shared';
  const planAllowsBot = data.bot?.plan_allows_bot ?? false;

  const activateClick = async () => {
    setErr(null); setOk(null);
    if (!token || token.split(':').length !== 2) {
      setErr(t('tenantSettings.bot_invalidToken'));
      return;
    }
    try {
      const r = await activate.mutateAsync({ bot_token: token });
      setOk(`@${r.bot_username} ${t('tenantSettings.bot_activated')}`);
      setToken('');
    } catch (ex) {
      const detail = apiErrorMessage(ex);
      // (#39) Friendly fallback if the plan was downgraded mid-flow.
      setErr(detail === 'plan_does_not_allow_tenant_bot'
        ? t('tenantSettings.bot_planLockedBody', "Bu funksiya joriy tarifingizda mavjud emas. O'z Telegram botingizni ulash uchun tarifni yangilang.")
        : detail);
    }
  };

  const deactivateClick = async () => {
    if (!(await confirmDialog({ message: t('tenantSettings.bot_deactivateConfirm'), danger: true }))) return;
    try { await deactivate.mutateAsync(); setOk(t('tenantSettings.bot_deactivated')); }
    catch (ex) {
      setErr(apiErrorMessage(ex));
    }
  };

  return (
    <SectionLayout title={t('tenantSettings.botTitle')} hint={t('tenantSettings.botHint')}>
      {/* Current state banner */}
      <div className={cn(
        'mb-6 flex items-center gap-3 rounded-control border p-4 text-body',
        mode === 'tenant' && isActivated
          ? 'border-success/30 bg-success-soft'
          : 'border-line bg-cream',
      )}>
        <Bot size={20} className={mode === 'tenant' && isActivated ? 'text-success' : 'text-muted'} />
        <div className="min-w-0 flex-1">
          {mode === 'tenant' && isActivated ? (
            <>
              <div className="font-medium">@{data.bot?.bot_username}</div>
              <div className="text-caption text-muted">{t('tenantSettings.bot_modeTenantActive')}</div>
            </>
          ) : (
            <>
              <div className="font-medium">@MuddatoBot</div>
              <div className="text-caption text-muted">{t('tenantSettings.bot_modeShared')}</div>
            </>
          )}
        </div>
        {mode === 'tenant' && isActivated && (
          <Button variant="outline" size="sm" onClick={deactivateClick} loading={deactivate.isPending}>
            {t('tenantSettings.bot_deactivate')}
          </Button>
        )}
      </div>

      {/* Own-bot wizard, gated on the plan (#39): plan-locked tenants see an
          upsell instead of the wizard — marked upfront, no 403 after pasting a token.
          One shared bot ships today, so connecting a branded per-tenant bot is
          deferred. The state banner above is retained — an owner still needs to
          know which bot their customers talk to — but not the wizard. */}
      {V2_SURFACES_ENABLED && (planAllowsBot ? (
      <div className="space-y-5">
        <WizardStep n={1} title={t('tenantSettings.bot_step1Title')} body={t('tenantSettings.bot_step1Body')}>
          <a href="https://t.me/BotFather" target="_blank" rel="noreferrer"
             className="mt-2 inline-flex h-9 items-center gap-2 rounded-pill bg-[color:var(--c-brand-telegram)] px-4 text-small font-medium text-white hover:brightness-110">
            <ExternalLink size={14} /> @BotFather
          </a>
        </WizardStep>

        <WizardStep n={2} title={t('tenantSettings.bot_step2Title')} body={t('tenantSettings.bot_step2Body')}>
          <div className="mt-2 rounded-control bg-ink p-3 font-mono text-caption text-white">/newbot</div>
        </WizardStep>

        <WizardStep n={3} title={t('tenantSettings.bot_step3Title')} body={t('tenantSettings.bot_step3Body')}>
          <div className="mt-3 flex gap-2">
            <input
              type="password"
              className={inputCls + ' font-mono'}
              placeholder="123456789:AAEhBOweik6ad6PsXxa0F39mLn..."
              value={token}
              onChange={(e) => setToken(e.target.value.trim())}
            />
            <Button variant="primary" size="md" onClick={activateClick}
                    disabled={!token} loading={activate.isPending}>
              {t('tenantSettings.bot_activate')}
            </Button>
          </div>
        </WizardStep>
      </div>
      ) : (
        <div className="rounded-control border border-warn/40 bg-[color:var(--c-warn-soft)] p-5">
          <div className="flex items-center gap-2 font-medium text-ink">
            <Lock size={16} /> {t('tenantSettings.bot_planLockedTitle', "O'z botingiz — yuqori tariflarda")}
          </div>
          <p className="mt-1.5 text-small text-ink-soft">
            {t('tenantSettings.bot_planLockedBody', "Bu funksiya joriy tarifingizda mavjud emas. O'z Telegram botingizni ulash uchun tarifni yangilang.")}
          </p>
          <Link to={`/${locale}/tenant/billing`}
                className="mt-3 inline-flex h-9 items-center rounded-pill bg-ink px-4 text-small font-semibold text-white hover:brightness-110">
            {t('tenantSettings.bot_planLockedCta', "Tariflarni ko'rish")}
          </Link>
        </div>
      ))}

      {ok && (
        <div className="mt-5 inline-flex items-center gap-2 rounded-control border border-success/30 bg-success-soft px-4 py-3 text-body text-success">
          <CheckCircle2 size={16} /> {ok}
        </div>
      )}
      {err && (
        <div className="mt-5 rounded-control border border-danger/30 bg-[color:var(--c-danger-soft)] px-4 py-3 text-body text-danger">
          {err}
        </div>
      )}
    </SectionLayout>
  );
}

function WizardStep({ n, title, body, children }: {
  n: number; title: string; body: string; children?: React.ReactNode;
}) {
  return (
    <div className="flex gap-4 rounded-control border border-line bg-cream p-4">
      <span className="grid h-8 w-8 shrink-0 place-items-center rounded-pill bg-ink text-white text-small font-semibold">
        {n}
      </span>
      <div className="min-w-0 flex-1">
        <div className="text-body font-semibold">{title}</div>
        <p className="mt-1 text-caption text-ink-soft">{body}</p>
        {children}
      </div>
    </div>
  );
}

// ── CustomerFormTab ───────────────────────────────────────────────────

function CustomerFormTab({ data }: { data: TenantSettingsBundle }) {
  const { t } = useTranslation();
  const patch = usePatchTenantSettings();
  const [fields, setFields]       = useState<CustomFieldDef[]>(data.customer_form?.fields ?? []);
  const [autoApprove, setAutoApprove] = useState<boolean>(data.customer_form?.auto_approve ?? false);
  const saveState = useSettingsSave();

  const save = () => saveState.run(() => patch.mutateAsync({ customer_form: { fields, auto_approve: autoApprove } }));

  const addField = () => {
    const idx = fields.length + 1;
    setFields([...fields, {
      key: `custom_${idx}`,
      label: t('tenantSettings.cf_defaultLabel', { n: idx }),
      type: 'text',
      required: false,
      order: idx,
    }]);
  };

  const update = (i: number, patch: Partial<CustomFieldDef>) =>
    setFields(fields.map((f, idx) => idx === i ? { ...f, ...patch } : f));

  const remove = (i: number) => setFields(fields.filter((_, idx) => idx !== i));

  return (
    <SectionLayout title={t('tenantSettings.cfTitle')} hint={t('tenantSettings.cfHint')}>
      {/* Built-in fields (always-on, can't be removed) */}
      <div className="mb-5 rounded-control bg-cream p-4">
        <div className="mb-2 text-micro uppercase tracking-wider text-muted">
          {t('tenantSettings.cf_builtinTitle')}
        </div>
        <div className="flex flex-wrap gap-1.5 text-caption">
          {['fullName', 'phone', 'passport', 'jshshir', 'birthdate', 'address'].map((k) => (
            <span key={k} className="rounded-pill border border-line bg-white px-3 py-1">
              {t(`join.${k === 'passport' ? 'passSeries' : k}`)}
            </span>
          ))}
        </div>
      </div>

      {/* Auto-approve toggle */}
      <label className="mb-5 flex items-start gap-3 rounded-control border border-line bg-surface p-4">
        <Switch className="mt-0.5" checked={autoApprove} onChange={setAutoApprove} />
        <div className="min-w-0 flex-1">
          <div className="text-body font-medium">{t('tenantSettings.cf_autoApproveLabel')}</div>
          <div className="text-caption text-muted">{t('tenantSettings.cf_autoApproveHint')}</div>
        </div>
      </label>

      {/* Custom fields list */}
      <div className="flex items-center justify-between">
        <div className="text-micro uppercase tracking-wider text-muted">{t('tenantSettings.cf_customTitle')}</div>
        <Button variant="outline" size="sm" onClick={addField}>
          <Plus size={14} /> {t('tenantSettings.cf_addField')}
        </Button>
      </div>

      {fields.length === 0 ? (
        <div className="mt-3"><EmptyState size="compact" icon={<Plus size={20} />}
                                                title={t('tenantSettings.cf_noCustom')} /></div>
      ) : (
        <ul className="mt-3 space-y-2">
          {fields.map((f, i) => (
            <li key={i} className="rounded-control border border-line bg-cream p-3">
              <div className="grid gap-2 md:grid-cols-[1.5fr_120px_1fr_auto] md:items-end">
                <Field label={t('tenantSettings.cf_label')}>
                  <input className={inputCls} value={f.label}
                         onChange={(e) => update(i, { label: e.target.value })} />
                </Field>
                <Field label={t('tenantSettings.cf_type')}>
                  <Select
                    value={f.type}
                    onChange={(v) => update(i, { type: v as CustomFieldDef['type'] })}
                    options={[
                      { value: 'text', label: 'Matn' },
                      { value: 'number', label: 'Raqam' },
                      { value: 'phone', label: 'Telefon' },
                      { value: 'date', label: 'Sana' },
                      { value: 'file', label: 'Fayl' },
                      { value: 'select', label: 'Tanlov' },
                    ]}
                  />
                </Field>
                {f.type === 'select' ? (
                  <Field label={t('tenantSettings.cf_options')}>
                    <input className={inputCls} placeholder={t('tenantSettings.cf_optionsPh', 'vergul bilan')}
                           value={(f.options ?? []).join(', ')}
                           onChange={(e) => update(i, {
                             options: e.target.value.split(/,\s*/).filter(Boolean),
                           })} />
                  </Field>
                ) : (
                  <Field label={t('tenantSettings.cf_key')}>
                    <input className={inputCls + ' font-mono'} value={f.key} placeholder="snake_case"
                           onChange={(e) => update(i, { key: e.target.value.replace(/\s+/g, '_') })} />
                  </Field>
                )}
                <button type="button" onClick={() => remove(i)}
                        aria-label={t('tenantSettings.removeField', {
                          name: f.label || `#${i + 1}`,
                          defaultValue: 'Maydonni o‘chirish: {{name}}',
                        })}
                        className="tap-target grid h-11 w-11 place-items-center rounded-control border border-line text-danger hover:bg-[color:var(--c-danger-soft)]">
                  <Trash2 size={14} aria-hidden="true" />
                </button>
              </div>
              <label className="mt-2 inline-flex items-center gap-2 text-caption">
                <Switch size="sm" checked={f.required} onChange={(v) => update(i, { required: v })} />
                {t('tenantSettings.cf_required')}
              </label>
            </li>
          ))}
        </ul>
      )}

      <SaveBar onSave={save} pending={patch.isPending} ok={saveState.ok} error={saveState.err} />
    </SectionLayout>
  );
}

// ── ProfileTab ────────────────────────────────────────────────────────

function ProfileTab({ data }: { data: TenantSettingsBundle }) {
  const { t } = useTranslation();
  const patch = usePatchTenantSettings();
  // Seed the phone THROUGH the mask. Since r96 the backend stores one canonical
  // form (`+998888888580`) because that is what makes "is this number already
  // taken?" answerable — but that form is for machines. `PhoneInput` formats on
  // CHANGE, not on mount, so spreading the server object in wholesale showed the
  // owner their own number as an unspaced run of digits until they typed in the
  // field. The existing seed guard in phoneDisplay.test.ts looks for a `phone:`
  // key being assigned; a whole-object seed like this one has no such key and
  // slipped straight past it.
  const [f, setF] = useState({
    ...data.profile,
    contact_phone: phoneOrEmpty(data.profile.contact_phone ?? ''),
  });
  const saveState = useSettingsSave();

  const logoErr = validateLogoUrl(f.logo_url);
  // Blocked at the source: a bad URL must never reach the generic error screen.
  const save = () => { if (!logoErr) saveState.run(() => patch.mutateAsync({ profile: f })); };

  return (
    <SectionLayout>
      <div className="grid gap-4 md:grid-cols-2">
        <Field label={t('tenantSettings.fldBusinessName')} required>
          <input className={inputCls} value={f.business_name ?? ''} onChange={(e) => setF({ ...f, business_name: e.target.value })} placeholder="iPhone Plaza" />
        </Field>
        <Field label={t('tenantSettings.fldLogoUrl')} hint={t('tenantSettings.fldLogoUrlHint')}
               error={logoErr ? t(`tenantSettings.logoUrl_${logoErr}`, LOGO_URL_FALLBACK[logoErr]) : undefined}>
          <input className={inputCls} value={f.logo_url ?? ''} maxLength={LOGO_URL_MAX}
                 onChange={(e) => setF({ ...f, logo_url: e.target.value })}
                 aria-invalid={!!logoErr} placeholder="https://…" />
        </Field>
        <div className="md:col-span-2">
          <Field label={t('tenantSettings.fldAddress')}>
            <input className={inputCls} value={f.address ?? ''} onChange={(e) => setF({ ...f, address: e.target.value })} placeholder="Toshkent sh., Yunusobod tum., …" />
          </Field>
        </div>
        <Field label={t('tenantSettings.fldContactPhone')}>
          <PhoneInput value={f.contact_phone ?? ''} onChange={(v) => setF({ ...f, contact_phone: v })} />
        </Field>
        <Field label={t('tenantSettings.fldContactEmail')}>
          <EmailInput value={f.contact_email ?? ''} onChange={(v) => setF({ ...f, contact_email: v })} placeholder="info@gmail.com" />
        </Field>
        <Field label={t('tenantSettings.fldContactTelegram')} hint="@username">
          <TelegramInput value={f.contact_telegram ?? ''} onChange={(v) => setF({ ...f, contact_telegram: v })} placeholder="manager" />
        </Field>
        <Field
          label={t('tenantSettings.fldCurrency')}
          hint={t('tenantSettings.fldCurrencyHint', "Ekranlarda va shartnoma/chek PDF'larida shu birlik ko'rsatiladi")}
        >
          {/* Options come from SUPPORTED_CURRENCIES so this Select and everything
              that renders the chosen value cannot drift apart. Until now the
              choice made here reached the PDF renderer and nothing else: a shop
              on USD saw UZS on every screen while its own contracts and receipts
              said USD. */}
          <Select
            value={f.currency}
            onChange={(v) => setF({ ...f, currency: v })}
            options={SUPPORTED_CURRENCIES.map((c) => ({
              value: c,
              label: t(`tenantSettings.currency_${c}`, c),
            }))}
          />
        </Field>
        {/* (#14) Business Type is temporarily withdrawn from the UI. The
            stored `business_type` / `business_type_other` are carried through
            `f` untouched and saved back as-is, so a tenant who chose one keeps
            it and restoring the field restores their answer. */}
        <Field label={t('tenantSettings.fldBizHoursStart')}>
          <TimePicker value={f.business_hours.start ?? '09:00'} onChange={(v) => setF({ ...f, business_hours: { ...f.business_hours, start: v } })} />
        </Field>
        <Field label={t('tenantSettings.fldBizHoursEnd')}>
          <TimePicker value={f.business_hours.end ?? '21:00'} onChange={(v) => setF({ ...f, business_hours: { ...f.business_hours, end: v } })} />
        </Field>
        <div className="md:col-span-2">
          <Field label={t('tenantSettings.fldReceiptFooter')} hint={t('tenantSettings.fldReceiptFooterHint')}>
            <Textarea rows={3} maxLength={500} value={f.receipt_footer ?? ''} onChange={(v) => setF({ ...f, receipt_footer: v })} placeholder="Rahmat! Bizni tanlaganingiz uchun. +998901234567" />
          </Field>
        </div>
      </div>
      <SaveBar onSave={save} pending={patch.isPending} ok={saveState.ok} error={saveState.err} />
    </SectionLayout>
  );
}

// ── CalcTab ───────────────────────────────────────────────────────────

const FORMULA_DESCS: Record<TenantSettingsBundle['calc']['default_calc_formula'], string> = {
  none:           'tenantSettings.fxNoneDesc',
  annuity:        'tenantSettings.fxAnnuityDesc',
  differentiated: 'tenantSettings.fxDiffDesc',
  markup:         'tenantSettings.fxMarkupDesc',
};

function CalcTab({ data }: { data: TenantSettingsBundle }) {
  const { t } = useTranslation();
  const patch = usePatchTenantSettings();
  const [f, setF] = useState(data.calc);
  const saveState = useSettingsSave();

  const save = () => saveState.run(() => patch.mutateAsync({ calc: f }));

  return (
    <SectionLayout title={t('tenantSettings.calcTitle')} hint={t('tenantSettings.calcHint')}>
      <div className="grid gap-4 md:grid-cols-2">
        <div className="md:col-span-2">
          <Field label={t('tenantSettings.fldDefaultFormula')} hint={t('tenantSettings.fldDefaultFormulaHint')}>
            <div className="grid gap-2 sm:grid-cols-2 xl:grid-cols-4">
              {/* `none` and `markup` are retained; annuity and differentiated
                  are deferred, so a tenant default can never be set to one. */}
              {(V2_SURFACES_ENABLED
                ? (['none', 'annuity', 'differentiated', 'markup'] as const)
                : (['none', 'markup'] as const)
              ).map((k) => (
                <button
                  key={k}
                  type="button"
                  onClick={() => setF({ ...f, default_calc_formula: k })}
                  className={cn(
                    'flex flex-col items-start gap-1 rounded-control border p-3 text-left transition',
                    f.default_calc_formula === k
                      ? 'border-ink bg-ink text-white'
                      : 'border-line bg-cream hover:border-ink/30',
                  )}
                >
                  <span className="text-small font-semibold">{t(`tenantSettings.fx_${k}`)}</span>
                  <span className={cn('text-micro', f.default_calc_formula === k ? 'text-white/70' : 'text-muted')}>
                    {t(FORMULA_DESCS[k])}
                  </span>
                </button>
              ))}
            </div>
          </Field>
        </div>

        {(f.default_calc_formula === 'markup' || f.default_calc_formula === 'annuity') && (
          <Field
            label={f.default_calc_formula === 'markup' ? t('tenantSettings.fldMarkupPct') : t('tenantSettings.fldInterestPct')}
          >
            <PercentInput
              max={999}
              value={f.default_markup_pct}
              onChange={(n) => setF({ ...f, default_markup_pct: n })}
            />
          </Field>
        )}

        <Field label={t('tenantSettings.fldMaxMonths')}>
          <input type="number" min={1} max={120} className={inputCls}
                 value={f.default_max_months}
                 onChange={(e) => setF({ ...f, default_max_months: Number(e.target.value || 12) })} />
        </Field>

        {/* The hint states what an empty field means. Rendered empty with a `0`
            placeholder, this was indistinguishable from a stored 0 — the only
            cue was text contrast against its siblings on the same row — so an
            owner could not tell whether "no minimum down payment" was a rule
            they had set or one they had simply never filled in. */}
        <Field
          label={t('tenantSettings.fldMinDownPct')}
          hint={t('tenantSettings.fldMinDownPctHint', "Bo'sh qoldirilsa — boshlang'ich to'lov talab qilinmaydi")}
        >
          <PercentInput
            max={100}
            value={f.default_min_down_pct}
            onChange={(n) => setF({ ...f, default_min_down_pct: n })}
          />
        </Field>

        {/* Late fee and early-payoff discount are money parameters with no
            engine behind them — presenting them would promise behaviour the
            product does not perform. */}
        {V2_SURFACES_ENABLED && (
        <Field label={t('tenantSettings.fldLateFeePct')} hint={t('tenantSettings.fldLateFeePctHint')}>
          <PercentInput
            max={100}
            value={f.default_late_fee_pct_per_day}
            onChange={(n) => setF({ ...f, default_late_fee_pct_per_day: n })}
          />
        </Field>
        )}

        {V2_SURFACES_ENABLED && (
        <Field
          label={t('tenantSettings.fldEarlyPayoffPct', 'Erta yopish chegirmasi (%)')}
          hint={t('tenantSettings.fldEarlyPayoffPctHint',
                  'Mijoz qoldiqni hozir to\'lashga rozi bo\'lsa, qarz qoldig\'idan necha %, chegirma berasiz. 0 = chegirma yo\'q.')}
        >
          <PercentInput
            max={50}
            value={f.early_payoff_discount_pct ?? 0}
            onChange={(n) => setF({ ...f, early_payoff_discount_pct: n })}
          />
        </Field>
        )}
      </div>

      <div className="mt-5 rounded-control bg-cream p-4 text-caption text-muted">
        <span className="font-semibold text-ink">{t('tenantSettings.calcNoteTitle')}</span>{' '}
        {t('tenantSettings.calcNoteBody')}
      </div>

      <SaveBar onSave={save} pending={patch.isPending} ok={saveState.ok} error={saveState.err} />
    </SectionLayout>
  );
}

// ── PaymentsTab ───────────────────────────────────────────────────────

function PaymentsTab({ data }: { data: TenantSettingsBundle }) {
  const { t } = useTranslation();
  const patch = usePatchTenantSettings();
  const [f, setF] = useState(data.payments);
  const saveState = useSettingsSave();

  const save = () => saveState.run(() => patch.mutateAsync({ payments: f }));

  const toggle = (k: keyof TenantSettingsBundle['payments']['payment_methods']) =>
    setF({ ...f, payment_methods: { ...f.payment_methods, [k]: !f.payment_methods[k] } });

  const cards = f.payment_methods.cards ?? [];
  const hasCards = cards.length > 0;

  return (
    <SectionLayout>
      {/* (#14) Only the methods the product currently offers. Payme, Click and
          Bank Transfer are withdrawn from the shared list in
          lib/paymentMethods.ts, not deleted here — a tenant who had them
          configured keeps that configuration, and restoring one is a one-word
          change that this screen picks up automatically. */}
      <div className="mb-4 grid gap-2 md:grid-cols-3 lg:grid-cols-5">
        {OFFERED_PAYMENT_METHODS.map((k) => {
          const on = !!f.payment_methods[k];
          // Card needs somewhere to receive the money. Disabled rather than
          // hidden: hiding it would leave an owner with no idea why the method
          // they expect is missing, and no hint that adding a card unlocks it.
          const blocked = k === 'card' && !hasCards;
          // A blocked method is NOT accepted, so it must not read as on. The
          // switch used to render the stored `card: true` in the on position
          // while disabled — a muted control with the knob to the right, which
          // an owner reads as "card payments work". They do not: with no
          // receiving card the customer is offered Karta and the backend has
          // nowhere to send them (it answers `card_not_configured`). The stored
          // flag is deliberately NOT rewritten here — this screen reports state,
          // it does not silently change the shop's settings — but what it
          // reports is now what actually happens.
          const effectivelyOn = on && !blocked;
          return (
            <button
              key={k} type="button" role="switch" aria-checked={effectivelyOn}
              disabled={blocked}
              aria-describedby={blocked ? 'pm-card-blocked' : undefined}
              onClick={() => toggle(k)}
              className={cn(
                'flex items-center justify-between gap-2 rounded-control border px-4 py-3 text-body transition',
                blocked ? 'cursor-not-allowed border-line bg-cream text-muted opacity-60'
                  : on ? 'border-accent bg-[color:var(--c-accent-soft)] text-ink'
                       : 'border-line bg-cream text-ink-soft hover:border-ink/20',
              )}
            >
              <span className="font-medium">{t(`tenantSettings.pm_${k}`)}</span>
              <SwitchTrack checked={effectivelyOn} />
            </button>
          );
        })}
      </div>
      {!hasCards && (
        <p id="pm-card-blocked" className="mb-4 text-caption text-muted">
          {t('tenantSettings.pmCardNeedsCard', 'Karta orqali qabul qilish uchun avval qabul qiluvchi karta qo‘shing')}
        </p>
      )}

      {isOffered('click') && f.payment_methods.click && (
        <div className="mt-4">
          <PaymentProviderCard
            label="Click.uz" icon={<CreditCard size={16} />} withService
            creds={{ merchant_id: f.click_merchant_id ?? '', service_id: f.click_service_id ?? '', secret_key: f.click_secret_key ?? '' }}
            onChange={(c) => setF({ ...f, click_merchant_id: c.merchant_id ?? null, click_service_id: c.service_id ?? null, click_secret_key: c.secret_key ?? null })}
            secretHint={t('tenantSettings.secretMasked')}
          />
        </div>
      )}

      {isOffered('payme') && f.payment_methods.payme && (
        <div className="mt-4">
          <PaymentProviderCard
            label="Payme.uz" icon={<CreditCard size={16} />}
            creds={{ merchant_id: f.payme_merchant_id ?? '', secret_key: f.payme_secret_key ?? '' }}
            onChange={(c) => setF({ ...f, payme_merchant_id: c.merchant_id ?? null, payme_secret_key: c.secret_key ?? null })}
            secretHint={t('tenantSettings.secretMasked')}
          />
        </div>
      )}

      {/* Always visible, deliberately. This used to render only when Card or
          Transfer was already on — which, now that Card requires a saved card,
          would be unopenable: no card means no Card toggle, and no Card toggle
          meant no card list to add one to. The list is the prerequisite, so it
          comes first. */}
      <div className="mt-4">
        <div className="mb-2 text-micro uppercase tracking-wider text-muted">
          {t('tenantSettings.payoutCards', 'Qabul qiluvchi kartalar')}
        </div>
        <CardManager
          cards={cards}
          onChange={(next) => setF({ ...f, payment_methods: { ...f.payment_methods, cards: next } })}
        />
      </div>
      {/* #49: no free-text instructions — payers get a standard bilingual
          note + the receiving cards; tenants don't author it per-language. */}

      <SaveBar onSave={save} pending={patch.isPending} ok={saveState.ok} error={saveState.err} />
    </SectionLayout>
  );
}

// ── RemindersTab ──────────────────────────────────────────────────────

function RemindersTab({ data }: { data: TenantSettingsBundle }) {
  const { t } = useTranslation();
  const patch = usePatchTenantSettings();
  const [f, setF] = useState(data.reminders);
  const saveState = useSettingsSave();

  // #51: clear any per-tenant template overrides — reminders use the built-in
  // bilingual (uz/ru) texts, so a tenant never hardcodes a single language.
  const save = () => saveState.run(() => patch.mutateAsync({ reminders: { ...f, reminder_templates: {} } }));

  return (
    <SectionLayout title={t('tenantSettings.remindersTitle')} hint={t('tenantSettings.remindersHint')}>
      <div className="grid gap-4 md:grid-cols-2">
        <Field label={t('tenantSettings.fldReminderTarget')} hint={t('tenantSettings.fldReminderTargetHint')}>
          <Select
            value={f.reminder_target}
            onChange={(v) => setF({ ...f, reminder_target: v as 'owner' | 'channel' | 'both' })}
            options={[
              { value: 'owner', label: t('tenantSettings.tgt_owner') },
              { value: 'channel', label: t('tenantSettings.tgt_channel') },
              { value: 'both', label: t('tenantSettings.tgt_both') },
            ]}
          />
        </Field>
        {/* #50: only ask for a channel id when reminders actually go to a channel. */}
        {f.reminder_target !== 'owner' && (
          <Field label={t('tenantSettings.fldChannelId')} hint="-100…">
            <input className={inputCls} value={f.reminder_channel_id ?? ''}
                   onChange={(e) => setF({ ...f, reminder_channel_id: e.target.value })}
                   placeholder="-1001234567890" />
          </Field>
        )}

        <Field label={t('tenantSettings.fldBeforeDueOffsets')} hint={t('tenantSettings.fldBeforeDueOffsetsHint')}>
          <input className={inputCls} value={(f.reminder_offsets_days ?? []).join(', ')}
                 onChange={(e) => setF({
                   ...f,
                   reminder_offsets_days: e.target.value.split(/[,\s]+/).map(Number).filter((x) => !isNaN(x)),
                 })} placeholder="3, 1, 0" />
        </Field>
        <Field label={t('tenantSettings.fldOverdueEvery')} hint={t('tenantSettings.fldOverdueEveryHint')}>
          <input type="number" min={1} max={30} className={inputCls}
                 value={f.reminder_overdue_every_days}
                 onChange={(e) => setF({ ...f, reminder_overdue_every_days: Number(e.target.value || 3) })} />
        </Field>
      </div>

      <div className="mt-6 rounded-control border border-line bg-cream/60 px-4 py-3 text-caption text-ink-soft">
        {t('tenantSettings.templatesBuiltin', "Eslatma matnlari tayyor (o'zbek + rus) — avtomatik tarjima qilinadi. Alohida yozish shart emas.")}
      </div>

      <HolidaysList />

      <SaveBar onSave={save} pending={patch.isPending} ok={saveState.ok} error={saveState.err} />
    </SectionLayout>
  );
}

function HolidaysList() {
  const { t } = useTranslation();
  const [open, setOpen] = useState(false);
  const [year, setYear] = useState(new Date().getFullYear());
  const q = useQuery({
    queryKey: queryKeys.holidays.year(year),
    queryFn: async () =>
      (await api.get<Array<{ date: string; name_uz: string; name_ru: string; kind: string }>>(
        `/tenant/settings/holidays?year=${year}`,
      )).data,
    enabled: open,
  });

  return (
    <div className="mt-6 rounded-control border border-line p-4">
      <button
        type="button"
        onClick={() => setOpen(!open)}
        className="flex w-full items-center justify-between text-left"
      >
        <span className="inline-flex items-center gap-2 text-body font-medium">
          <CalendarDays size={14} className="text-accent" />
          {t('tenantSettings.holidaysTitle', 'Bayram kunlari (eslatma yuborilmaydi)')}
        </span>
        <span className="text-micro text-muted">{open ? '▾' : '▸'}</span>
      </button>
      {open && (
        <div className="mt-3">
          <div className="mb-2 flex items-center gap-2 text-caption">
            <button onClick={() => setYear(year - 1)} className="rounded-pill border border-line px-2 py-0.5 hover:bg-cream">←</button>
            <span className="font-mono">{year}</span>
            <button onClick={() => setYear(year + 1)} className="rounded-pill border border-line px-2 py-0.5 hover:bg-cream">→</button>
          </div>
          {q.isLoading && <div className="text-caption text-muted">Yuklanmoqda…</div>}
          {q.data && (
            <ul className="space-y-1 text-caption">
              {q.data.length === 0 && (
                <li className="text-muted">Bu yil uchun bayramlar yo'q.</li>
              )}
              {q.data.map((h) => (
                <li key={h.date} className="flex items-center justify-between border-b border-line/50 py-1 last:border-0">
                  <span className="font-mono text-ink-soft">{h.date}</span>
                  <span>{h.name_uz}</span>
                  <span className="text-micro uppercase tracking-wider text-muted">{h.kind}</span>
                </li>
              ))}
            </ul>
          )}
        </div>
      )}
    </div>
  );
}

// ── shared chrome ─────────────────────────────────────────────────────

/**
 * A settings section. `title` is optional: when a tab holds exactly one section
 * whose name IS the tab's own name, the header would restate the highlighted
 * rail row beside it (and the mobile header above it), so it is omitted — the
 * same redundancy the settings breadcrumb was. Tabs with several sections, or
 * one whose name differs from the tab, still title them.
 */
function SectionLayout({ title, hint, children }: { title?: string; hint?: string; children: React.ReactNode }) {
  return (
    <section className="card p-6">
      {title && (
      <div className="mb-5">
        <h3 className="text-subsection font-semibold">{title}</h3>
        {hint && <p className="mt-1 text-caption text-muted">{hint}</p>}
      </div>
      )}
      {children}
    </section>
  );
}

/**
 * The save path for every settings group. A rejected save must state itself:
 * without a `catch` the success flag is simply skipped and a failed save is
 * indistinguishable from a successful one. This makes the outcome explicit in
 * both directions, keeps entered values intact, and routes the message through
 * the shared localized error vocabulary so no raw backend token reaches the
 * user.
 */
function useSettingsSave() {
  const [ok, setOk] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const run = async (fn: () => Promise<unknown>) => {
    setOk(false); setErr(null);
    try {
      await fn();
      setOk(true);
      setTimeout(() => setOk(false), 2500);
    } catch (ex) {
      // The form keeps its state — nothing is reset on failure, so the user can
      // correct and retry without re-entering anything.
      setErr(apiErrorMessage(ex));
    }
  };
  return { ok, err, run };
}

function SaveBar({ onSave, pending, ok, error, onReset }: {
  onSave: () => void; pending: boolean; ok: boolean; error?: string | null; onReset?: () => void;
}) {
  const { t } = useTranslation();
  return (
    <div className="mt-6 space-y-3">
      {error && (
        <div role="alert" aria-live="assertive"
             className="rounded-control border border-danger/30 bg-[color:var(--c-danger-soft)] px-4 py-3 text-body text-danger">
          {error}
        </div>
      )}
      <div className="flex items-center justify-end gap-3">
        {ok && (
          <span role="status" aria-live="polite" className="inline-flex items-center gap-1.5 text-small text-success">
            <CheckCircle2 size={14} /> {t('tenantSettings.saved')}
          </span>
        )}
        {onReset && (
          <Button variant="ghost" size="md" onClick={onReset} loading={pending}>
            {t('common.reset', 'Tiklash')}
          </Button>
        )}
        <Button variant="primary" size="md" onClick={onSave} loading={pending}>
          {pending && <Loader2 size={14} className="animate-spin" />}
          {t('common.save')}
        </Button>
      </div>
    </div>
  );
}
