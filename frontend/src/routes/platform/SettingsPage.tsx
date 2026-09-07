import { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import {
  Bell, CheckCircle2, Coins, ListChecks, Loader2, Megaphone, Plus,
  Settings as SettingsIcon, Trash2, Wallet,
} from 'lucide-react';
import { SettingsShell, type SettingsTab } from '@/components/layout/SettingsShell';
import { V2_SURFACES_ENABLED } from '@/routes/_scope';
import { SectionCard } from '@/components/ui/SectionCard';
import { Button } from '@/components/ui/Button';
import { Field, inputCls } from '@/components/ui/Modal';
import { PaymentProviderCard } from '@/components/ui/PaymentProviderCard';
import { NumberChips } from '@/components/ui/NumberChips';
import { Select } from '@/components/ui/Select';
import { CardManager, type CardValidation, type PayoutCard } from '@/components/ui/CardManager';
import { TelegramInput, EmailInput } from '@/components/ui/FormFields';
import { EmptyState } from '@/components/ui/EmptyState';
import { ErrorCard } from '@/components/ui/ErrorCard';
import { SkeletonList } from '@/components/ui/Skeleton';
import { toast } from '@/lib/feedback';
import { apiErrorMessage } from '@/lib/errors';
import { usePlatformSettings, usePlatformPlans, useOnboardingOptions, type BizType, type HowFoundOption } from '@/lib/queries';
import { usePutSetting } from '@/lib/mutations';
import { slugify } from '@/lib/catalogSchema';

type TabKey = 'defaults' | 'reminders' | 'support' | 'billing' | 'onboarding';

/** The single settings row this page's Asosiy qiymatlar tab reads and writes.
 *  Mirrors `DEFAULTS_KEY` in `backend/app/services/platform_defaults.py`. */
const DEFAULTS_KEY = 'defaults';

/**
 * Platforma admin · Sozlamalar — URL-addressable master/detail via SettingsShell
 * (`?tab=`): deep-linkable, refresh-safe, Back-aware. Each sub-page saves its
 * own group; billing is read-only.
 */
export default function PlatformSettingsPage() {
  const { t, i18n } = useTranslation();
  const q = usePlatformSettings();
  // The default-plan choices come from the LIVE plan catalogue, never a literal
  // list — see `planOptions` below for what the literal list had drifted into.
  const plansQuery = usePlatformPlans();
  const put = usePutSetting();
  const [saved, setSaved] = useState(false);

  const get = (key: string, fallback: unknown = '') => {
    const row = q.data?.find((s) => s.key === key);
    return row?.value ?? fallback;
  };

  /**
   * Read one field of the `defaults` GROUP — the row this page actually writes.
   *
   * THE BUG THIS FIXES (production, 2026-08-11). The Save button writes the whole
   * group under the single key `defaults` (see `saveGroup('defaults', …)` below),
   * exactly as `reminders` and `support` do. The load, alone among the three, read
   * four SEPARATE keys — `default_plan`, `default_locale`, `default_timezone`,
   * `trial_days`. So the write landed in a row nothing read: choose Starter, save,
   * reload, and the Select comes back empty because `default_plan` has no row —
   * the selection was never lost, it was never looked for.
   *
   * The legacy flat rows are still consulted underneath, because an install may
   * hold either shape and an operator's stored configuration must not depend on
   * which era it was saved in. The group wins where both exist; saving only ever
   * produces the group, so the two shapes converge instead of drifting further.
   * The backend reader (`app/services/platform_defaults.py`) merges them the same
   * way, so what this form shows is what provisioning applies.
   */
  /** The stored row's value, or `undefined`. Deliberately NOT `get(key, undefined)`:
   *  `get`'s fallback is a DEFAULT PARAMETER, so passing `undefined` explicitly
   *  selects it — the call would hand back `''`, and `'' ?? fallback` is `''`
   *  because `??` only catches null/undefined. That is how a blank Select and a
   *  `trial_days` of `0` appear on a settings page whose store is simply empty. */
  const rowValue = (key: string): unknown => q.data?.find((s) => s.key === key)?.value;

  /** "The row holds nothing usable for this field." Empty string counts: a blank
   *  stored value must fall through to the default, not render as a blank field. */
  const blank = (v: unknown) => v === undefined || v === null || v === '';

  const getDefault = (key: string, fallback: unknown = '') => {
    const group = rowValue(DEFAULTS_KEY);
    if (group && typeof group === 'object' && !Array.isArray(group)) {
      const v = (group as Record<string, unknown>)[key];
      if (!blank(v)) return v;
    }
    // A scalar stored under a flat key round-trips as `{v: scalar}` — the PUT
    // endpoint wraps non-objects — so unwrap before handing it to the form.
    const flat = rowValue(key);
    const unwrapped = (flat && typeof flat === 'object' && 'v' in (flat as object))
      ? (flat as { v: unknown }).v
      : flat;
    return blank(unwrapped) ? fallback : unwrapped;
  };

  const [defaults, setDefaults] = useState({
    // A plan CODE, not an enum member. Plan codes became owner-chosen slugs in
    // R36; the union type this used to carry is what let the form drift away
    // from the catalogue.
    default_plan: '',
    default_locale: 'uz' as 'uz' | 'ru',
    default_timezone: 'Asia/Tashkent',
    trial_days: 7,
  });
  const [reminders, setReminders] = useState({
    before_due_days: [3, 1, 0] as number[],
    after_overdue_days: [1, 3, 7] as number[],
    receipt_review_sla_hours: 24,
  });
  // Yearly promo — a platform-wide percentage, not a per-plan price. Stored in
  // its own settings row so a campaign change is one number in one place and
  // reaches the landing page immediately.
  const [yearly, setYearly] = useState({ enabled: true, discount_pct: 20 });
  const [support, setSupport] = useState({
    support_username: 'MuddatoSupport',
    contact_channel_id: '-1001234567890',
    company_email: 'salom@muddato.uz',
  });

  // (#486) Persisted-state snapshots (JSON) per group — back the dirty flags
  // and the SaveBar Reset, so a half-typed edit can be reverted without a
  // reload and a clean group shows no Reset at all.
  const [baseline, setBaseline] = useState<{ d: string; r: string; s: string } | null>(null);

  useEffect(() => {
    if (!q.data) return;
    const d = {
      // Whatever is stored is shown as-is. It is NOT silently rewritten to a
      // plausible-looking value: if the stored code is not a real plan the
      // operator has to see that, because every shop provisioned meanwhile was
      // stamped with it. `planOptions` makes such a value visible and selectable
      // rather than letting the Select fall back to a blank or a wrong label.
      default_plan:     String(getDefault('default_plan', '')),
      default_locale:   (getDefault('default_locale', 'uz') as 'uz' | 'ru'),
      default_timezone: String(getDefault('default_timezone', 'Asia/Tashkent')),
      trial_days:       Number(getDefault('trial_days', 7)),
    };
    const r0 = get('reminders', {}) as Record<string, unknown>;
    const r = (r0 && typeof r0 === 'object') ? {
      before_due_days: Array.isArray(r0.before_due_days) ? r0.before_due_days as number[] : reminders.before_due_days,
      after_overdue_days: Array.isArray(r0.after_overdue_days) ? r0.after_overdue_days as number[] : reminders.after_overdue_days,
      receipt_review_sla_hours: typeof r0.receipt_review_sla_hours === 'number' ? r0.receipt_review_sla_hours : reminders.receipt_review_sla_hours,
    } : reminders;
    const y0 = get('yearly_billing', {}) as Record<string, unknown>;
    const y = (y0 && typeof y0 === 'object') ? {
      enabled: y0.enabled === undefined ? true : !!y0.enabled,
      discount_pct: typeof y0.discount_pct === 'number' ? y0.discount_pct : 20,
    } : yearly;
    setYearly(y);
    const s0 = get('support', {}) as Record<string, unknown>;
    const s = (s0 && typeof s0 === 'object') ? {
      support_username: String(s0.support_username ?? support.support_username),
      contact_channel_id: String(s0.contact_channel_id ?? support.contact_channel_id),
      company_email: String(s0.company_email ?? support.company_email),
    } : support;
    setDefaults(d); setReminders(r); setSupport(s);
    setBaseline({ d: JSON.stringify(d), r: JSON.stringify(r), s: JSON.stringify(s) });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [q.data]);

  // Which baseline slot a saved settings key refreshes (onboarding keys: none).
  const BASELINE_SLOT: Record<string, 'd' | 'r' | 's'> = { defaults: 'd', reminders: 'r', support: 's' };

  const saveGroup = async (key: string, value: unknown) => {
    try {
      await put.mutateAsync({ key, value });
      const slot = BASELINE_SLOT[key];
      if (slot) setBaseline((b) => (b ? { ...b, [slot]: JSON.stringify(value) } : b));
      setSaved(true);
      setTimeout(() => setSaved(false), 2000);
    } catch (ex) {
      toast.error(apiErrorMessage(ex));
    }
  };

  /**
   * The default-plan choices, built from the live plan catalogue.
   *
   * THE BUG THIS FIXES (found on a real device, 2026-08-08). This Select carried
   * a hardcoded `Lite / Pro / Custom`, left over from when plan codes were a
   * fixed enum. Codes became owner-chosen slugs in R36 and the catalogue moved
   * on to starter/business/pro/enterprise/flex — so of the three options offered
   * here, only "Pro" was a real plan, and the stored default was `lite`, which
   * no longer exists. Every shop provisioned since was stamped with a plan code
   * the catalogue does not contain, and an operator had no way to choose most of
   * the real plans. A literal list cannot track a catalogue the operator edits
   * on the next page over, so it must not be a literal list.
   *
   * Inactive plans are excluded — a new shop must not be defaulted onto a plan
   * that has been retired — EXCEPT when one is the stored value, which is kept
   * visible for the same reason the unknown-code row below exists.
   */
  const planOptions = (() => {
    const ru = i18n.language?.startsWith('ru');
    const plans = plansQuery.data ?? [];
    const opts = plans
      .filter((p) => p.is_active || p.code === defaults.default_plan)
      // A trial DEFAULT would put every newly provisioned shop on a trial
      // lifecycle the MVP does not run. That guarantee predates this list being
      // catalogue-driven and must survive it: sourcing the options from the
      // catalogue would otherwise re-offer `trial` the moment someone adds it
      // there. Still shown when it is the STORED value, so an operator can see
      // and correct a legacy row rather than have it silently hidden.
      .filter((p) => V2_SURFACES_ENABLED || p.code !== 'trial' || p.code === defaults.default_plan)
      .map((p) => ({ value: p.code, label: (ru ? p.name_ru : p.name_uz) || p.code }));
    // A stored code with no matching plan is shown rather than hidden: silently
    // dropping it makes the Select render blank or, worse, snap to the first
    // plan — which reads as "the default is Starter" when the value actually
    // being applied to new shops is something else entirely.
    const stored = defaults.default_plan;
    if (stored && !opts.some((o) => o.value === stored)) {
      opts.unshift({ value: stored, label: t('platformSettings.unknownPlan', { code: stored, defaultValue: '{{code}} — mavjud emas' }) });
    }
    return opts;
  })();

  const dirtyDefaults  = !!baseline && JSON.stringify(defaults)  !== baseline.d;
  const dirtyReminders = !!baseline && JSON.stringify(reminders) !== baseline.r;
  const dirtySupport   = !!baseline && JSON.stringify(support)   !== baseline.s;

  // Only the groups an operator actually needs: platform defaults, the support
  // contact, and the receiving cards a tenant tops up into.
  const tabs: SettingsTab[] = [
    { key: 'defaults',   label: t('platformSettings.defaultsTitle'),              sub: t('platformSettings.defaultsHint'),   icon: SettingsIcon },
    ...(V2_SURFACES_ENABLED
      ? [{ key: 'reminders', label: t('platformSettings.remindersTitle'), sub: t('platformSettings.remindersHint'), icon: Bell }]
      : []),
    { key: 'support',    label: t('platformSettings.supportTitle'),               sub: t('platformSettings.supportHint'),    icon: Megaphone },
    ...(V2_SURFACES_ENABLED
      ? [{ key: 'onboarding', label: t('platformSettings.onboardingTitle', 'Onboarding'), sub: t('platformSettings.onboardingHint', 'Biznes turlari va manbalar'), icon: ListChecks }]
      : []),
    { key: 'billing',    label: t('platformSettings.billingTitle'),               sub: t('platformSettings.billingHint'),    icon: Coins },
  ];

  return (
    <SettingsShell
      title={t('nav.settings')}
      tabs={tabs}
      loading={q.isLoading || (!q.data && !q.isError)}
      error={q.error}
      onRetry={() => q.refetch()}
    >
      {/* Tab bodies carry NO section header: the title and hint would be the
          very translation keys the highlighted rail row already renders beside
          them (and that `MobileHeader` shows above them on a phone), with the
          same icon. `SectionCard` draws no header when `title` is absent. */}
      {(key) => {
        switch (key as TabKey) {

          /* ── Defaults ─────────────────────────────────────────────────── */
          case 'defaults': return (
            <SectionCard>
              <div className="grid gap-4 sm:grid-cols-2">
                <Field
                  label={t('platformSettings.defaultPlan')}
                  hint={t('platformSettings.defaultPlanHint', "Yangi do'kon shu tarifda ochiladi")}
                >
                  <Select
                    value={defaults.default_plan}
                    onChange={(v) => setDefaults((p) => ({ ...p, default_plan: v }))}
                    options={planOptions}
                    disabled={plansQuery.isLoading}
                  />
                </Field>

                <Field label={t('platformSettings.defaultLocale')}>
                  <Select
                    value={defaults.default_locale}
                    onChange={(v) => setDefaults((p) => ({ ...p, default_locale: v as 'uz' | 'ru' }))}
                    options={[
                      { value: 'uz', label: "O'zbekcha" },
                      { value: 'ru', label: 'Русский' },
                    ]}
                  />
                </Field>

                <Field label={t('platformSettings.defaultTimezone')} hint={t('platformSettings.timezoneHint', 'Telegram bot va hisobotlar uchun')}>
                  <Select
                    value={defaults.default_timezone}
                    onChange={(v) => setDefaults((p) => ({ ...p, default_timezone: v }))}
                    options={[
                      'Asia/Tashkent', 'Asia/Samarkand', 'Asia/Almaty',
                      'Asia/Bishkek', 'Asia/Dushanbe', 'Asia/Ashgabat',
                      'Asia/Baku', 'Asia/Tbilisi', 'Asia/Yerevan',
                      'Asia/Dubai', 'Europe/Moscow', 'UTC',
                    ].map((z) => ({ value: z, label: z }))}
                  />
                </Field>

                {/* Trial length configures a deferred capability. */}
                {V2_SURFACES_ENABLED && (
                <Field
                  label={t('platformSettings.trialDays')}
                  hint={t('platformSettings.trialDaysHint', '0 – 90 kun')}
                >
                  <div className="flex items-center gap-2">
                    <input
                      type="number"
                      min={0}
                      max={90}
                      className={inputCls + ' w-24 tabular-nums'}
                      value={defaults.trial_days}
                      onChange={(e) => {
                        const n = Math.min(90, Math.max(0, Number(e.target.value || 0)));
                        setDefaults((p) => ({ ...p, trial_days: n }));
                      }}
                    />
                    <span className="shrink-0 text-body text-muted">{t('platformSettings.daysUnit', 'kun')}</span>
                  </div>
                </Field>
                )}
              </div>

              <SaveBar
                onSave={() => saveGroup(DEFAULTS_KEY, defaults)}
                pending={put.isPending}
                ok={saved}
                dirty={dirtyDefaults}
                onReset={() => baseline && setDefaults(JSON.parse(baseline.d))}
              />
            </SectionCard>
          );

          /* ── Reminders ────────────────────────────────────────────────── */
          case 'reminders': return (
            <SectionCard
              icon={<Bell size={16} />}
              title={t('platformSettings.remindersTitle')}
              meta={t('platformSettings.remindersHint')}
            >
              {/* (#492) Chip editor: each offset is a discrete, validated, removable
                  token — a stray letter or "3, , 1" can no longer silently drop a
                  reminder day. */}
              <div className="space-y-4">
                <Field
                  label={t('platformSettings.beforeDueDays')}
                  hint={t('platformSettings.beforeDueDaysHint')}
                >
                  <NumberChips
                    values={reminders.before_due_days.map(String)}
                    unit={t('platformSettings.daysUnit', 'kun')}
                    placeholder={t('platformSettings.addDayOffset', 'Kun — Enter')}
                    onChange={(vals) => setReminders((p) => ({
                      ...p,
                      before_due_days: vals.map(Number).filter((x) => !isNaN(x)),
                    }))}
                  />
                </Field>

                <Field
                  label={t('platformSettings.afterOverdueDays')}
                  hint={t('platformSettings.afterOverdueDaysHint')}
                >
                  <NumberChips
                    values={reminders.after_overdue_days.map(String)}
                    unit={t('platformSettings.daysUnit', 'kun')}
                    placeholder={t('platformSettings.addDayOffset', 'Kun — Enter')}
                    onChange={(vals) => setReminders((p) => ({
                      ...p,
                      after_overdue_days: vals.map(Number).filter((x) => !isNaN(x)),
                    }))}
                  />
                </Field>

                <Field
                  label={t('platformSettings.receiptSla')}
                  hint={t('platformSettings.receiptSlaHint', 'Chek tasdiqlash muddati')}
                >
                  <div className="flex items-center gap-2">
                    <input
                      type="number"
                      min={1}
                      max={168}
                      className={inputCls + ' w-24 tabular-nums'}
                      value={reminders.receipt_review_sla_hours}
                      onChange={(e) => {
                        const n = Math.max(1, Number(e.target.value || 1));
                        setReminders((p) => ({ ...p, receipt_review_sla_hours: n }));
                      }}
                    />
                    <span className="shrink-0 text-body text-muted">{t('platformSettings.hoursUnit', 'soat')}</span>
                  </div>
                </Field>
              </div>

              <SaveBar
                onSave={() => saveGroup('reminders', reminders)}
                pending={put.isPending}
                ok={saved}
                dirty={dirtyReminders}
                onReset={() => baseline && setReminders(JSON.parse(baseline.r))}
              />
            </SectionCard>
          );

          /* ── Support / Contact ────────────────────────────────────────── */
          case 'support': return (
            <SectionCard>
              <div className="space-y-4">
                <Field label={t('platformSettings.supportUsername')}>
                  <TelegramInput
                    value={support.support_username}
                    onChange={(v) => setSupport((p) => ({ ...p, support_username: v }))}
                  />
                </Field>

                <Field
                  label={t('platformSettings.contactChannelId')}
                  hint={t('platformSettings.channelIdHint', 'Masalan: -1001234567890')}
                >
                  <input
                    className={inputCls + ' font-mono'}
                    value={support.contact_channel_id}
                    onChange={(e) => setSupport((p) => ({ ...p, contact_channel_id: e.target.value }))}
                    placeholder="-100XXXXXXXXXX"
                  />
                </Field>

                <Field label={t('platformSettings.companyEmail')}>
                  {/* An explicit placeholder: EmailInput's shared default is
                      `salom@gmail.com`, which is right for a customer's personal
                      address and wrong as the example for the platform's own
                      corporate contact — a consumer mailbox on a fintech
                      settings screen sets exactly the wrong expectation. */}
                  <EmailInput
                    value={support.company_email}
                    onChange={(v) => setSupport((p) => ({ ...p, company_email: v }))}
                    placeholder="support@muddato.uz"
                  />
                </Field>
              </div>

              <SaveBar
                onSave={() => saveGroup('support', support)}
                pending={put.isPending}
                ok={saved}
                dirty={dirtySupport}
                onReset={() => baseline && setSupport(JSON.parse(baseline.s))}
              />
            </SectionCard>
          );

          /* ── Onboarding taxonomies ────────────────────────────────────── */
          case 'onboarding': return <OnboardingTab />;

          /* ── Billing ─────────────────────────────────────────────────── */
          case 'billing': return (
            <div className="space-y-4">
              <BillingCardsTab />
              {/* Provider credentials belong to the deferred online rails. */}
              {V2_SURFACES_ENABLED && <BillingProvidersTab />}
            </div>
          );

          default: return null;
        }
      }}
    </SettingsShell>
  );
}

// ── Platform receiving cards (tenants transfer subscription fees here) ────────

function BillingCardsTab() {
  const { t } = useTranslation();
  const q = usePlatformSettings();
  const put = usePutSetting();
  const [cards, setCards] = useState<PayoutCard[]>([]);
  const [ok, setOk] = useState(false);

  // Per-card resolution state from the server, computed with the same matrix
  // that filters the tenant top-up screen. Held BESIDE the stored value so a
  // save cannot round-trip the annotation into the setting.
  const [validation, setValidation] = useState<CardValidation[]>([]);

  useEffect(() => {
    const row = q.data?.find((s) => s.key === 'billing_cards');
    const v = (row?.value ?? {}) as { items?: PayoutCard[] };
    setCards(Array.isArray(v.items) ? v.items : []);
    setValidation(Array.isArray(row?.validation) ? row.validation : []);
  }, [q.data]);

  const save = async () => {
    setOk(false);
    try {
      await put.mutateAsync({ key: 'billing_cards', value: { items: cards } });
      setOk(true);
      setTimeout(() => setOk(false), 2500);
    } catch (ex) {
      // The server refuses to store a card whose network it cannot resolve,
      // since that card would appear on no top-up screen. Name the offending
      // rows by their last four digits instead of the generic failure.
      const detail = (ex as { response?: { data?: { detail?: unknown } } }).response?.data?.detail;
      const bad = (detail as { error?: string; cards?: { last4: string }[] } | undefined);
      if (bad?.error === 'billing_card_network_required' && bad.cards?.length) {
        toast.error(t('platformSettings.cardsNetworkRequired', {
          cards: bad.cards.map((c) => `••${c.last4}`).join(', '),
          defaultValue: "Bu kartalarda to'lov tarmog'i ko'rsatilmagan: {{cards}}",
        }));
      } else {
        toast.error(apiErrorMessage(ex));
      }
    }
  };

  const unresolved = validation.filter((v) => !v.resolved).length;

  return (
    <SectionCard
      icon={<Coins size={16} />}
      title={t('platformSettings.cardsTitle', 'Qabul qiluvchi kartalar')}
      meta={t('platformSettings.cardsHint', "Bizneslar obuna to'lovini shu kartalarga o'tkazadi.")}
    >
      {unresolved > 0 && (
        <p role="alert" className="mb-3 rounded-control border border-danger/30 bg-[color:var(--c-danger-soft)] px-3 py-2 text-caption font-medium text-danger">
          {t('platformSettings.cardsUnresolved', {
            count: unresolved,
            defaultValue: "{{count}} ta kartada to'lov tarmog'i ko'rsatilmagan — ular to'ldirish ekranida ko'rinmaydi.",
          })}
        </p>
      )}
      <CardManager cards={cards} onChange={setCards} validation={validation} />
      <SaveBar onSave={save} pending={put.isPending} ok={ok} />
    </SectionCard>
  );
}

// ── Online payment providers (Payme / Click merchant credentials) ────────────

interface ProviderCfg { merchant_id?: string; service_id?: string; secret_key?: string; enabled?: boolean }

function BillingProvidersTab() {
  const { t } = useTranslation();
  const q = usePlatformSettings();
  const put = usePutSetting();
  const [payme, setPayme] = useState<ProviderCfg>({});
  const [click, setClick] = useState<ProviderCfg>({});
  const [ok, setOk] = useState(false);

  useEffect(() => {
    const row = q.data?.find((s) => s.key === 'billing_providers');
    const v = (row?.value ?? {}) as { payme?: ProviderCfg; click?: ProviderCfg };
    setPayme(v.payme ?? {});
    setClick(v.click ?? {});
  }, [q.data]);

  const save = async () => {
    setOk(false);
    try {
      await put.mutateAsync({ key: 'billing_providers', value: { payme, click } });
      setOk(true);
      setTimeout(() => setOk(false), 2500);
    } catch (ex) {
      toast.error(apiErrorMessage(ex));
    }
  };

  const secretHint = t('platformSettings.secretMasked', 'Yashirilgan');

  return (
    <SectionCard
      icon={<Coins size={16} />}
      title={t('platformSettings.providersTitle', "Onlayn to'lov (Payme / Click)")}
      meta={t('platformSettings.providersHint', "Merchant ma'lumotlarini kiriting. Yoqilmaguncha bizneslarga ko'rinmaydi.")}
    >
      <div className="space-y-3">
        <PaymentProviderCard
          label="Payme" icon={<Wallet size={16} />} creds={payme}
          onChange={(c) => setPayme({ ...payme, ...c })}
          enabled={!!payme.enabled} onToggle={(v) => setPayme({ ...payme, enabled: v })}
          secretHint={secretHint}
        />
        <PaymentProviderCard
          label="Click" icon={<Wallet size={16} />} withService creds={click}
          onChange={(c) => setClick({ ...click, ...c })}
          enabled={!!click.enabled} onToggle={(v) => setClick({ ...click, enabled: v })}
          secretHint={secretHint}
        />
      </div>
      <SaveBar onSave={save} pending={put.isPending} ok={ok} />
    </SectionCard>
  );
}

// ── Onboarding taxonomies (business types + acquisition channels) ─────────────

function OnboardingTab() {
  const { t } = useTranslation();
  const options = useOnboardingOptions();
  const put = usePutSetting();
  const [types, setTypes] = useState<BizType[]>([]);
  const [channels, setChannels] = useState<HowFoundOption[]>([]);
  const [ok, setOk] = useState(false);

  useEffect(() => {
    if (options.data) {
      setTypes(options.data.business_types);
      setChannels(options.data.how_found_options);
    }
  }, [options.data]);

  const save = async () => {
    setOk(false);
    // Normalise: derive a slug/value from the name when missing; drop blanks.
    const cleanTypes = types
      .filter((x) => x.name_uz.trim() || x.name_ru.trim())
      .map((x) => ({ ...x, slug: x.slug || slugify(x.name_uz || x.name_ru) }));
    const cleanChannels = channels
      .filter((x) => x.label_uz.trim() || x.label_ru.trim())
      .map((x) => ({ ...x, value: x.value || slugify(x.label_uz || x.label_ru).replace(/-/g, '_') }));
    try {
      await put.mutateAsync({ key: 'business_types', value: cleanTypes });
      await put.mutateAsync({ key: 'how_found_options', value: cleanChannels });
      setTypes(cleanTypes);
      setChannels(cleanChannels);
      setOk(true);
      setTimeout(() => setOk(false), 2500);
      options.refetch();
    } catch (ex) {
      toast.error(apiErrorMessage(ex));
    }
  };

  const setType = (i: number, p: Partial<BizType>) =>
    setTypes((a) => a.map((x, idx) => idx === i ? { ...x, ...p } : x));
  const setChan = (i: number, p: Partial<HowFoundOption>) =>
    setChannels((a) => a.map((x, idx) => idx === i ? { ...x, ...p } : x));

  // A failed fetch must never fall through to the "no business types yet"
  // empty state below — that copy invites adding a first item and saving,
  // which would silently overwrite the real (un-fetched) backend taxonomy.
  if (options.isLoading) return <SkeletonList rows={4} />;
  if (options.isError) return <ErrorCard error={options.error} onRetry={() => options.refetch()} />;

  return (
    <div className="space-y-4">
      {/* Business types */}
      <SectionCard
        icon={<ListChecks size={16} />}
        title={t('platformSettings.bizTypesTitle', 'Biznes turlari')}
        meta={t('platformSettings.bizTypesHint', "Onboarding sehrgarida ko'rsatiladi. Emoji + ikki tildagi nom.")}
      >
        {types.length === 0 ? (
          <EmptyState
            icon={<ListChecks size={20} />}
            title={t('platformSettings.bizTypesEmpty', "Hali biznes turi yo'q")}
            body={t('platformSettings.bizTypesEmptyBody', "Pastdagi tugmadan birinchi turni qo'shing.")}
          />
        ) : (
          <ul className="space-y-2">
            {types.map((bt, i) => (
              <li key={i} className="flex items-center gap-2">
                <input
                  className={inputCls + ' w-14 shrink-0 text-center'}
                  value={bt.emoji ?? ''}
                  maxLength={4}
                  onChange={(e) => setType(i, { emoji: e.target.value })}
                  placeholder="📦"
                  aria-label={t('platformSettings.emojiLabel', 'Emoji')}
                />
                <input
                  className={inputCls + ' min-w-0 flex-1'}
                  value={bt.name_uz}
                  onChange={(e) => setType(i, { name_uz: e.target.value })}
                  placeholder="UZ"
                  aria-label="UZ"
                />
                <input
                  className={inputCls + ' min-w-0 flex-1'}
                  value={bt.name_ru}
                  onChange={(e) => setType(i, { name_ru: e.target.value })}
                  placeholder="RU"
                  aria-label="RU"
                />
                <Button
                  variant="danger"
                  size="sm"
                  onClick={() => setTypes((a) => a.filter((_, idx) => idx !== i))}
                  aria-label={t('common.delete')}
                >
                  <Trash2 size={14} />
                </Button>
              </li>
            ))}
          </ul>
        )}
        <div className="mt-3">
          <Button
            variant="outline"
            size="sm"
            onClick={() => setTypes((a) => [...a, { slug: '', name_uz: '', name_ru: '', emoji: '' }])}
          >
            <Plus size={14} /> {t('common.add', "Qo'shish")}
          </Button>
        </div>
      </SectionCard>

      {/* How-found channels */}
      <SectionCard
        icon={<Megaphone size={16} />}
        title={t('platformSettings.howFoundTitle', 'Bizni qanday topdingiz?')}
        meta={t('platformSettings.howFoundHint', 'Onboardingdagi javob variantlari.')}
      >
        {channels.length === 0 ? (
          <EmptyState
            icon={<Megaphone size={20} />}
            title={t('platformSettings.howFoundEmpty', "Hali variant yo'q")}
            body={t('platformSettings.howFoundEmptyBody', "Pastdagi tugmadan birinchi variantni qo'shing.")}
          />
        ) : (
          <ul className="space-y-2">
            {channels.map((c, i) => (
              <li key={i} className="flex items-center gap-2">
                <input
                  className={inputCls + ' min-w-0 flex-1'}
                  value={c.label_uz}
                  onChange={(e) => setChan(i, { label_uz: e.target.value })}
                  placeholder="UZ"
                  aria-label="UZ"
                />
                <input
                  className={inputCls + ' min-w-0 flex-1'}
                  value={c.label_ru}
                  onChange={(e) => setChan(i, { label_ru: e.target.value })}
                  placeholder="RU"
                  aria-label="RU"
                />
                <Button
                  variant="danger"
                  size="sm"
                  onClick={() => setChannels((a) => a.filter((_, idx) => idx !== i))}
                  aria-label={t('common.delete')}
                >
                  <Trash2 size={14} />
                </Button>
              </li>
            ))}
          </ul>
        )}
        <div className="mt-3">
          <Button
            variant="outline"
            size="sm"
            onClick={() => setChannels((a) => [...a, { value: '', label_uz: '', label_ru: '' }])}
          >
            <Plus size={14} /> {t('common.add', "Qo'shish")}
          </Button>
        </div>
      </SectionCard>

      <SaveBar onSave={save} pending={put.isPending} ok={ok} />
    </div>
  );
}

// ── SaveBar ───────────────────────────────────────────────────────────────────

function SaveBar({
  onSave, pending, ok, dirty, onReset,
}: {
  onSave: () => void;
  pending: boolean;
  ok: boolean;
  /** (#486) When provided, a secondary Revert button appears while dirty and
   *  restores the last persisted values without a reload. */
  dirty?: boolean;
  onReset?: () => void;
}) {
  const { t } = useTranslation();
  return (
    <div className="mt-6 flex items-center justify-end gap-3 border-t border-line pt-4">
      {ok && (
        <span className="inline-flex items-center gap-1.5 text-small text-success">
          <CheckCircle2 size={14} /> {t('common.saved', 'Saqlandi')}
        </span>
      )}
      {onReset && dirty && (
        <Button variant="outline" size="md" onClick={onReset} loading={pending}>
          {t('common.revert', 'Qaytarish')}
        </Button>
      )}
      <Button variant="primary" size="md" onClick={onSave} loading={pending}>
        {pending
          ? <Loader2 size={16} className="animate-spin" />
          : <CheckCircle2 size={16} />}
        {t('common.save')}
      </Button>
    </div>
  );
}
