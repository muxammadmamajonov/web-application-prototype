import { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { CheckCircle2, Copy, Link2, Loader2, Share2 } from 'lucide-react';
import { Modal, Field, inputCls } from '@/components/ui/Modal';
import { Button } from '@/components/ui/Button';
import { shareViaTelegram } from '@/lib/telegram';
import { useCreateInvitation } from '@/lib/mutations';
import { apiErrorMessage } from '@/lib/errors';
import { dateTime } from '@/lib/format';

type StaffRole = 'manager' | 'sales' | 'accountant';

interface Props {
  open: boolean;
  onClose: () => void;
  kind: 'customer' | 'staff';
  /** For staff invites — pre-fill the role and skip the in-modal picker. */
  role?: StaffRole;
  /**
   * Roles to choose from INSIDE this modal, so one intent is one flow: choose,
   * and the link appears in place rather than in a second modal.
   */
  roleChoices?: StaffRole[];
  /** For customer invites — pre-fill the catalog model or product. */
  preset?: Record<string, unknown>;
}

/**
 * Universal "Get invite link" modal.
 *
 * On open it POSTs to /tenant/invitations and shows the two URL shapes
 * (Telegram deep-link + web fallback) with one-click copy. The tenant can
 * forward either to the recipient.
 */
export function InvitationLinkModal({ open, onClose, kind, role, roleChoices, preset }: Props) {
  const { t } = useTranslation();
  const create = useCreateInvitation();
  const [tgLink, setTgLink] = useState('');
  const [webLink, setWebLink] = useState('');
  const [expires, setExpires] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [copied, setCopied] = useState<'tg' | 'web' | null>(null);
  // The role this link will grant: the caller's, or the one chosen here.
  const [chosenRole, setChosenRole] = useState<StaffRole | undefined>(role);
  const needsRole = kind === 'staff' && !!roleChoices?.length && !chosenRole;
  const grantedRole = chosenRole ?? role;

  // (#549) Extracted so the error banner's Retry can re-run link creation
  // without forcing a close/reopen cycle.
  const generate = async (forRole: StaffRole | undefined = grantedRole) => {
    setErr(null); setCopied(null); setTgLink(''); setWebLink('');
    try {
      const data = await create.mutateAsync({ kind, role: forRole, preset });
      setTgLink(data.telegram_url);
      setWebLink(data.web_url);
      setExpires(dateTime(data.expires_at));
    } catch (ex) {
      setErr(apiErrorMessage(ex));   // localized; never a raw backend token
    }
  };

  useEffect(() => {
    if (!open) { setChosenRole(role); return; }
    // Mint immediately unless a role still has to be chosen in this modal.
    if (!(kind === 'staff' && roleChoices?.length && !role)) void generate(role);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open]);

  const copy = async (which: 'tg' | 'web', text: string) => {
    try { await navigator.clipboard.writeText(text); }
    catch { /* ignore */ }
    setCopied(which);
    setTimeout(() => setCopied(null), 1800);
  };

  const share = (text: string) => {
    // Share through Telegram (forward to a chat) — not the OS share sheet.
    if (!shareViaTelegram(text)) void copy('tg', text);
  };

  return (
    <Modal
      open={open} onClose={onClose}
      title={kind === 'customer' ? t('invite.customerTitle') : t('invite.staffTitle')}
      subtitle={t('invite.subtitle')}
      footer={
        <Button variant="primary" size="md" onClick={onClose}>
          {t('common.confirm')}
        </Button>
      }
    >
      {/* Step one, in the same modal: which role does this link grant?
          Choosing mints the link in place. */}
      {needsRole && !create.isPending && (
        <div className="space-y-3">
          <p className="text-small text-ink-soft">
            {t('invite.pickRolePrompt', 'Bu havola qanday huquq beradi?')}
          </p>
          <div className="grid gap-2">
            {roleChoices!.map((r) => (
              <button
                key={r}
                type="button"
                onClick={() => { setChosenRole(r); void generate(r); }}
                className="tap-target flex min-h-11 items-center justify-between gap-3 rounded-control border border-line px-4 py-3 text-left transition hover:border-ink/30"
              >
                <span className="min-w-0">
                  <span className="block font-medium">{t(`tenantStaff.role_${r}`, r)}</span>
                  <span className="block truncate text-caption text-muted">
                    {t(`tenantStaff.role${r === 'sales' ? 'Sales' : r === 'manager' ? 'Manager' : 'Accountant'}Hint`, '')}
                  </span>
                </span>
                <Link2 size={16} className="shrink-0 text-accent" aria-hidden="true" />
              </button>
            ))}
          </div>
        </div>
      )}

      {create.isPending && (
        <div className="grid place-items-center py-12 text-body text-muted">
          <Loader2 size={20} className="animate-spin" />
        </div>
      )}

      {err && (
        <div className="rounded-control border border-danger/30 bg-[color:var(--c-danger-soft)] px-4 py-3 text-body text-danger">
          <p>{err}</p>
          <Button variant="outline" size="sm" className="mt-2" onClick={() => void generate()} disabled={create.isPending}>
            {create.isPending && <Loader2 size={14} className="animate-spin" />}
            {t('common.retry', 'Qayta urinish')}
          </Button>
        </div>
      )}

      {tgLink && (
        <div className="space-y-4">
          <p className="text-small text-ink-soft">{t('invite.howToUse')}</p>

          {/* Telegram link (preferred) */}
          <Field label={t('invite.telegramLink')} hint={t('invite.telegramLinkHint')}>
            <div className="flex gap-2">
              <input value={tgLink} readOnly className={inputCls + ' font-mono text-caption'} />
              <button
                type="button"
                onClick={() => copy('tg', tgLink)}
                className="tap-target inline-flex h-11 w-11 shrink-0 items-center justify-center rounded-control border border-line bg-cream hover:border-ink/30"
                aria-label={t('invite.copyTelegramLink', 'Telegram havolasini nusxalash')}
              >
                {copied === 'tg' ? <CheckCircle2 size={16} className="text-success" /> : <Copy size={16} />}
              </button>
              <button
                type="button"
                onClick={() => share(tgLink)}
                className="inline-flex h-11 items-center justify-center gap-1 rounded-control bg-accent px-3 text-small font-semibold text-white hover:brightness-110"
              >
                <Share2 size={14} /> {t('invite.share', 'Ulashish')}
              </button>
            </div>
          </Field>

          {/* (#16) Web fallback — CUSTOMER invites only.
              Staff onboarding is Telegram-first: an employee is invited into the
              Mini App, and offering a second URL only invites the question of
              which one to send. Two links for one action is a decision the shop
              owner should not have to make while a new hire waits.
              The customer path keeps it: a customer may be handed a link by a
              clerk with no Telegram open, and `/i/<token>` serves them fine. */}
          {kind === 'customer' && (
            <Field label={t('invite.webLink')} hint={t('invite.webLinkHint')}>
              <div className="flex gap-2">
                <input value={webLink} readOnly className={inputCls + ' font-mono text-caption'} />
                <button
                  type="button"
                  onClick={() => copy('web', webLink)}
                  className="tap-target inline-flex h-11 w-11 shrink-0 items-center justify-center rounded-control border border-line bg-cream hover:border-ink/30"
                  aria-label={t('invite.copyWebLink', 'Veb havolani nusxalash')}
                >
                  {copied === 'web' ? <CheckCircle2 size={16} className="text-success" /> : <Link2 size={16} />}
                </button>
              </div>
            </Field>
          )}

          {/* Anyone forwarding this link must be able to see what they are
              handing over: the role it grants, when it expires, and that it is
              single-use. */}
          <div className="rounded-control border border-line bg-cream px-3 py-2.5 text-caption text-ink-soft">
            {grantedRole && (
              <div className="flex items-center justify-between gap-3">
                <span className="text-muted">{t('invite.grantsRole', 'Beriladigan huquq')}</span>
                <span className="font-medium">{t(`tenantStaff.role_${grantedRole}`, grantedRole)}</span>
              </div>
            )}
            {expires && (
              <div className="mt-1 flex items-center justify-between gap-3">
                <span className="text-muted">{t('invite.expiresAt')}</span>
                <span className="font-mono">{expires}</span>
              </div>
            )}
            <p className="mt-1.5 text-micro text-muted">
              {t('invite.singleUse', 'Havola bir marta ishlatiladi — birinchi ochgan odam biriktiriladi.')}
            </p>
          </div>
        </div>
      )}
    </Modal>
  );
}
