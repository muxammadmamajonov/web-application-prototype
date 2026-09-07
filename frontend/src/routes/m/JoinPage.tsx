import { useCallback, useEffect, useState } from 'react';
import { ShieldCheck } from 'lucide-react';
import { PhoneVerificationCard, type IdentitySnapshot } from '@/components/ui/PhoneVerificationCard';
import { useParams } from 'react-router';
import { useTranslation } from 'react-i18next';
import { AlertCircle, CheckCircle2, ChevronDown, Loader2 } from 'lucide-react';
import { api, setSessionToken } from '@/lib/api';
import { apiErrorMessage, isLocalizedError, LocalizedError } from '@/lib/errors';
import { getTelegramWebApp, isInsideTelegramClient } from '@/lib/telegram';
import { useSupportUsername } from '@/hooks/useSupportUsername';
import { Logo } from '@/components/layout/Logo';
import { LoadingSplash } from '@/components/ui/LoadingSplash';
import { Button } from '@/components/ui/Button';
import { Field, inputCls } from '@/components/ui/Modal';
import { PassportInput } from '@/components/ui/FormFields';
import { Select } from '@/components/ui/Select';
import { DatePicker } from '@/components/ui/DatePicker';

interface InviteContext {
  kind: 'customer' | 'staff' | 'platform_admin';
  tenant_name: string;
  tenant_slug: string;
  tenant_logo: string | null;
  role: string | null;
  preset: Record<string, unknown>;
  custom_fields: Array<{
    key: string; label: string;
    type: 'text' | 'phone' | 'date' | 'file' | 'select' | 'number';
    required: boolean; options?: string[];
  }>;
}

// Sentinel for the "no :token in the URL" case — set in an effect that must
// stay free of the `t` dependency (re-running it on a language switch would
// re-fetch the invite and reset in-progress form state), then translated only
// at the render site below where `error` is actually displayed.
const NO_TOKEN_ERROR = 'no_token';

/**
 * Public Mini-App invite handler at /m/:slug/i/:token.
 *
 *   1. Fetch invitation context → know kind/role/custom fields
 *   2. Capture Telegram initData (required for tg_id catch)
 *   3. Render the right form (customer / staff)
 *   4. Submit → backend creates the record and ties it to the user
 */
/** The server's answer about the person holding this link.
 *
 *  `matchesInvitation` is a boolean about the CALLER's own number only — it
 *  says nothing about the invited record beyond whether the two agree. */
interface InviteIdentity extends IdentitySnapshot {
  matchesInvitation: boolean | null;
  knownName: string | null;
  telegramName: string | null;
}

/**
 * Every kind requires proved ownership — the account it creates is identified by
 * a phone number, and this product has exactly one way to establish one.
 *
 * `staff` and `platform_admin` used to be absent from this set, and their branch
 * of the form asked for a phone number in a text box which the server wrote
 * straight onto the joining user. So an invitation link was a way to type an
 * account's phone: a second, unproved number sitting beside whatever Telegram
 * had proved for the same person, and — on the `platform_admin` branch — doing
 * so while granting a platform role.
 *
 * What differs between the kinds is the SERVER's bar, not this set: a customer
 * must also be on a number this market serves, while an operator or a shop
 * manager on a foreign number is a real person doing a real job. Both must have
 * proved the number is theirs.
 */
const PROOF_REQUIRED_KINDS = new Set(['customer', 'guarantor', 'staff', 'platform_admin']);

export default function JoinPage() {
  // The canonical /i/:token form carries no slug — the backend resolves the
  // tenant from the token. (Legacy /m/:slug/i/:token still routes here.)
  const { token } = useParams<{ slug?: string; token: string }>();
  const { t } = useTranslation();
  const support = useSupportUsername();
  const [ctx, setCtx]     = useState<InviteContext | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [done, setDone]   = useState(false);
  const insideTg = isInsideTelegramClient();

  // Form state (works for both kinds; only relevant fields submitted)
  const [fullName, setFullName] = useState('');
  const [firstName, setFirstName] = useState('');
  const [lastName, setLastName] = useState('');
  const [passport, setPassport] = useState('');
  const [jshshir, setJshshir] = useState('');
  const [birthdate, setBirthdate] = useState('');
  const [address, setAddress] = useState('');
  const [extra, setExtra] = useState<Record<string, string>>({});
  const [showMore, setShowMore] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  // What the SERVER already knows about whoever opened this link. Loaded before
  // the identity fields render, so the form can stop asking for facts that are
  // already established — and so a typed number can never stand in for proof.
  const [identity, setIdentity] = useState<InviteIdentity | null>(null);

  /** Re-read the server's view. This is the polling target while a contact is
   *  in flight: `/me/identity` would answer about the account but not about
   *  THIS invitation, and the mismatch state only exists here. */
  const loadIdentity = useCallback(async (): Promise<InviteIdentity | null> => {
    const tg = getTelegramWebApp();
    const initData = tg?.initData ?? '';
    if (!initData) return null;
    try {
      const r = await api.post<InviteIdentity>(`/inv/${token}/identity`, { init_data: initData });
      setIdentity(r.data);
      return r.data;
    } catch (ex) {
      // A link that expired or was claimed while we were waiting is a real
      // answer, not a transient error: stop the form rather than let it submit
      // into a 410.
      const gone = (ex as { response?: { status?: number } })?.response?.status;
      if (gone === 404 || gone === 410) setError(apiErrorMessage(ex));
      return null;
    }
  }, [token]);

  useEffect(() => {
    if (!token) { setError(NO_TOKEN_ERROR); setLoading(false); return; }
    const tg = getTelegramWebApp();
    try { tg?.ready(); (tg as { expand?: () => void } | undefined)?.expand?.(); } catch { /* ignore */ }
    api.get<InviteContext>(`/inv/${token}`)
      .then(async (r) => {
        setCtx(r.data);
        // Pre-fill names from Telegram if available
        const u = (tg as { initDataUnsafe?: { user?: { first_name?: string; last_name?: string } } } | undefined)?.initDataUnsafe?.user;
        if (u?.first_name) {
          setFirstName(u.first_name);
          if (r.data.kind === 'customer')
            setFullName(`${u.first_name} ${u.last_name ?? ''}`.trim());
        }
        if (u?.last_name) setLastName(u.last_name);
        if (PROOF_REQUIRED_KINDS.has(r.data.kind)) await loadIdentity();
      })
      .catch((ex) => setError(apiErrorMessage(ex)))
      .finally(() => setLoading(false));
  }, [token, loadIdentity]);

  const proofRequired = PROOF_REQUIRED_KINDS.has(ctx?.kind ?? '');
  // Verified, eligible, AND the number this invitation expects. Anything less
  // is a state to render, never a form to submit.
  //
  // Staff and platform invitations stop at OWNERSHIP: market eligibility is a
  // customer rule ("can this number trade here?"), and applying it to an
  // employee would lock a manager or an operator on a foreign number out of an
  // invitation that is legitimately theirs. This mirrors the server's own bar
  // for those kinds exactly — a client that asked for more would refuse joins
  // the API would accept.
  const ownershipOnly = ctx?.kind === 'staff' || ctx?.kind === 'platform_admin';
  const identitySettled = !!identity?.verified
    && (ownershipOnly || identity.state === 'VERIFIED')
    && identity.matchesInvitation !== false;
  const canSubmit = !proofRequired || identitySettled;

  const onSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError(null);
    setSubmitting(true);
    try {
      const tg = getTelegramWebApp();
      const initData = tg?.initData ?? '';
      if (!initData) {
        // Tagged so the catch below can tell an already-localized validation
        // message from an Axios transport failure, whose `message` is raw
        // English ("Network Error").
        throw new LocalizedError(t('join.openInTelegramFirst'));
      }
      // A proved number is the record's number. The form never sends one for a
      // customer or guarantor: there is nothing it could say that the server
      // does not already know better.
      const payload = ctx?.kind === 'customer'
        ? {
            init_data: initData, full_name: fullName,
            passport_series: passport.slice(0, 2) || null, passport_number: passport.slice(2) || null,
            jshshir: jshshir || null, birthdate: birthdate || null,
            address: address || null, locale: 'uz', extra,
          }
        // No `phone`: the account's number is the proved one, and the server
        // ignores anything a claim body says about it.
        : { init_data: initData, first_name: firstName, last_name: lastName || null, locale: 'uz' };
      const res = await api.post<{ token?: string }>(`/inv/${token}/claim`, payload);
      // Log the joiner straight in and send them to their dashboard — a full
      // reload re-bootstraps the session and the app routes them by persona, so
      // there's no second login and no "link expired" if they reload.
      if (res.data?.token) {
        setSessionToken(res.data.token);
        setDone(true);
        setTimeout(() => { window.location.replace('/'); }, 1200);
        return;
      }
      setDone(true);
    } catch (ex) {
      // Only an explicitly-tagged local validation error may show its own
      // message. Everything else — including offline, where axios also has a
      // `message` — goes through the shared localized vocabulary.
      setError(isLocalizedError(ex) ? ex.message : apiErrorMessage(ex));
    } finally {
      setSubmitting(false);
    }
  };

  if (loading) return <LoadingSplash label={t('join.loading')} />;

  if (error && !ctx) {
    return (
      <main className="grid min-h-screen place-items-center bg-cream px-6">
        <div className="card w-full max-w-panel p-8 text-center">
          <Logo className="mx-auto mb-4" />
          <AlertCircle size={28} className="mx-auto text-danger" />
          <h1 className="mt-3 text-subsection font-semibold">{t('join.errTitle')}</h1>
          <p className="mt-1 text-small text-ink-soft">
            {error === NO_TOKEN_ERROR ? t('join.errNoToken') : error}
          </p>
          {/* Invitation links are single-use and time-limited by design, so the
              recovery is always the same — ask the shop that sent it for a fresh
              one — and must be stated with a way to act on it. */}
          <p className="mt-3 text-small text-ink-soft">
            {t('join.errRecovery', 'Havola bir marta ishlatiladi va muddati cheklangan. Sizni taklif qilgan do‘kondan yangi havola so‘rang.')}
          </p>
          <a href={`https://t.me/${support}`} target="_blank" rel="noreferrer"
             className="tap-target mt-4 inline-flex min-h-11 items-center gap-1.5 rounded-control border border-line px-4 text-small font-medium text-ink-soft hover:bg-ink/5">
            {t('join.contactSupport', 'Yordam olish')}
          </a>
        </div>
      </main>
    );
  }

  if (done) {
    return (
      <main className="grid min-h-screen place-items-center bg-cream px-6">
        <div className="card w-full max-w-panel p-8 text-center">
          <CheckCircle2 size={36} className="mx-auto text-success" />
          <h1 className="mt-3 text-subsection font-semibold">{t('join.doneTitle')}</h1>
          <p className="mt-2 text-small text-ink-soft">
            {ctx?.kind === 'customer'
              ? t('join.doneCustomer')
              : ctx?.kind === 'platform_admin'
                ? t('join.doneAdmin', 'Siz platforma admini sifatida qo‘shildingiz.')
                : t('join.doneStaff')}
          </p>
          <div className="mt-4 inline-flex items-center gap-2 text-small text-muted">
            <Loader2 size={16} className="animate-spin" /> {t('join.redirecting', 'Panelga yo‘naltirilmoqda…')}
          </div>
        </div>
      </main>
    );
  }

  return (
    <main className="grid min-h-screen place-items-start bg-cream px-4 py-8 sm:px-6">
      <div className="card mx-auto w-full max-w-panel p-6">
        {/* Tenant brand */}
        <header className="mb-5 flex items-center gap-3">
          {ctx?.tenant_logo
            ? <img src={ctx.tenant_logo} alt="" className="h-10 w-10 rounded-control object-cover" />
            : <div className="h-10 w-10 rounded-control bg-ink/10" />}
          <div className="min-w-0">
            <div className="truncate font-semibold">{ctx?.tenant_name}</div>
            <div className="text-micro uppercase tracking-wider text-muted">
              {ctx?.kind === 'customer'
                ? t('join.kindCustomer')
                : ctx?.kind === 'platform_admin'
                  ? t('join.kindAdmin', 'Platforma admini')
                  : t('join.kindStaff')}
              {ctx?.role && ` · ${ctx.role}`}
            </div>
          </div>
        </header>

        {!insideTg && (
          <div className="mb-4 rounded-control border border-warn/30 bg-warn-soft px-3 py-2 text-caption text-ink">
            {t('join.openInTelegramHint')}
          </div>
        )}

        <form onSubmit={onSubmit} className="grid gap-4">
          {ctx?.kind === 'customer' ? (
            <>
              <Field label={t('join.fullName')} required>
                <input className={inputCls} value={fullName} required onChange={(e) => setFullName(e.target.value)} />
              </Field>

              {/* The phone is NOT a form field here. Either Telegram has proved
                  who owns this number — in which case asking again is asking a
                  person to retype something we already know, and accepting the
                  answer would mean a typed value could contradict a proof — or
                  it has not, in which case the way forward is to prove it. */}
              {identitySettled ? (
                <Field label={t('join.phone')}>
                  <div data-testid="verified-phone"
                       className="flex h-11 items-center gap-2 rounded-control border border-line bg-cream px-3 text-subsection tabular-nums text-ink-soft">
                    <ShieldCheck size={16} className="shrink-0 text-success" aria-hidden="true" />
                    <span className="truncate">{identity.phoneMasked}</span>
                  </div>
                </Field>
              ) : (
                <PhoneVerificationCard
                  snapshot={identity}
                  onRefetch={loadIdentity}
                  onVerified={(fresh) => setIdentity(fresh as InviteIdentity)}
                />
              )}

              {/* Passport / JSHSHIR / birthdate / address are not needed to join —
                  the shop collects them later at sale time. Keep onboarding to
                  name + phone; surface the rest behind an opt-in disclosure so a
                  customer can still pre-fill them if they want. */}
              <button type="button" onClick={() => setShowMore((v) => !v)}
                      className="flex items-center justify-between rounded-control border border-line bg-cream px-4 py-3 text-body font-medium text-ink-soft hover:bg-ink/5">
                <span>{t('join.moreDetails', "Qo'shimcha ma'lumotlar (ixtiyoriy)")}</span>
                <ChevronDown size={16} className={showMore ? 'rotate-180 transition' : 'transition'} />
              </button>
              {showMore && (
                <div className="grid gap-4">
                  <Field label={t('join.passport', 'Pasport (AA1234567)')}>
                    <PassportInput value={passport} onChange={setPassport} />
                  </Field>
                  <Field label={t('join.jshshir')} hint={t('join.jshshirHint', 'Faqat raqam, 14 ta')}>
                    <input className={inputCls} maxLength={14} value={jshshir} inputMode="numeric"
                           onChange={(e) => setJshshir(e.target.value.replace(/\D/g, '').slice(0, 14))} />
                  </Field>
                  <div className="grid grid-cols-2 gap-3">
                    <Field label={t('join.birthdate')}>
                      <DatePicker value={birthdate} onChange={(iso) => setBirthdate(iso)} />
                    </Field>
                    <Field label={t('join.address')}>
                      <input className={inputCls} value={address}
                             onChange={(e) => setAddress(e.target.value)} />
                    </Field>
                  </div>
                </div>
              )}

              {/* Tenant-defined extra fields */}
              {(ctx?.custom_fields ?? []).map((f) => (
                <Field key={f.key} label={f.label} required={f.required}>
                  {f.type === 'select' ? (
                    <Select
                      value={extra[f.key] ?? ''}
                      onChange={(v) => setExtra({ ...extra, [f.key]: v })}
                      placeholder="—"
                      options={(f.options ?? []).map((o) => ({ value: o, label: o }))}
                    />
                  ) : f.type === 'date' ? (
                    <DatePicker
                      value={extra[f.key] ?? ''}
                      onChange={(iso) => setExtra({ ...extra, [f.key]: iso })}
                    />
                  ) : (
                    <input className={inputCls} type={f.type === 'number' ? 'number' : 'text'}
                           required={f.required} value={extra[f.key] ?? ''}
                           onChange={(e) => setExtra({ ...extra, [f.key]: e.target.value })} />
                  )}
                </Field>
              ))}
            </>
          ) : (
            <>
              <div className="grid grid-cols-2 gap-3">
                <Field label={t('join.firstName')} required>
                  <input className={inputCls} value={firstName} required
                         onChange={(e) => setFirstName(e.target.value)} />
                </Field>
                <Field label={t('join.lastName')}>
                  <input className={inputCls} value={lastName}
                         onChange={(e) => setLastName(e.target.value)} />
                </Field>
              </div>
              {/* The same rule the customer branch above already follows: either
                  Telegram has proved who owns this number, in which case asking
                  again invites a person to contradict it, or it has not, in
                  which case the way forward is to prove it — not to type one. */}
              {identitySettled ? (
                <Field label={t('join.phone')}>
                  <div data-testid="verified-phone"
                       className="flex h-11 items-center gap-2 rounded-control border border-line bg-cream px-3 text-subsection tabular-nums text-ink-soft">
                    <ShieldCheck size={16} className="shrink-0 text-success" aria-hidden="true" />
                    <span className="truncate">{identity.phoneMasked}</span>
                  </div>
                </Field>
              ) : (
                <PhoneVerificationCard
                  snapshot={identity}
                  onRefetch={loadIdentity}
                  onVerified={(fresh) => setIdentity(fresh as InviteIdentity)}
                />
              )}
            </>
          )}

          {error && (
            <div className="rounded-control border border-danger/30 bg-[color:var(--c-danger-soft)] px-4 py-3 text-body text-danger">
              {error}
            </div>
          )}

          <Button variant="primary" size="lg" type="submit" data-testid="join-submit"
                  disabled={submitting || !canSubmit} className="w-full">
            {submitting && <Loader2 size={16} className="animate-spin" />}
            {t('join.submit')}
          </Button>
        </form>
      </div>
    </main>
  );
}
