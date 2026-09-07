import { useEffect, useState } from 'react';
import { Trans, useTranslation } from 'react-i18next';
import { CheckCircle2, Copy, Link2, Loader2, Share2, ShieldAlert } from 'lucide-react';
import { Modal, Field, inputCls } from '@/components/ui/Modal';
import { Button } from '@/components/ui/Button';
import { Select } from '@/components/ui/Select';
import { useCreateAdminInvitation } from '@/lib/mutations';
import { apiErrorMessage } from '@/lib/errors';
import { shareViaTelegram } from '@/lib/telegram';
import { dateTime } from '@/lib/format';

interface Props { open: boolean; onClose: () => void }

/**
 * "Havola orqali admin qo'shish" — mints a single-use, role-scoped link.
 * The opener logs in via Telegram and is promoted to the chosen platform
 * role on claim. The role is baked into the token, so the claimer cannot
 * escalate it.
 */
export function AdminInviteLinkModal({ open, onClose }: Props) {
  const { t } = useTranslation();
  const create = useCreateAdminInvitation();
  const [role, setRole] = useState<'super_admin' | 'support'>('support');
  const [tgLink, setTgLink] = useState('');
  const [webLink, setWebLink] = useState('');
  const [expires, setExpires] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [copied, setCopied] = useState<'tg' | 'web' | null>(null);

  // Reset when reopened so a stale link from a previous role doesn't linger.
  useEffect(() => {
    if (!open) return;
    setTgLink(''); setWebLink(''); setExpires(null); setErr(null); setCopied(null); setRole('support');
  }, [open]);

  const generate = async () => {
    setErr(null); setCopied(null);
    try {
      const data = await create.mutateAsync({ role });
      setTgLink(data.telegram_url);
      setWebLink(data.web_url);
      setExpires(dateTime(data.expires_at));
    } catch (ex) {
      setErr(apiErrorMessage(ex));
    }
  };

  const copy = async (which: 'tg' | 'web', text: string) => {
    try { await navigator.clipboard.writeText(text); } catch { /* ignore */ }
    setCopied(which);
    setTimeout(() => setCopied(null), 1800);
  };

  // SHARE the invite with someone else — the admin must not "open" (claim) their
  // own invite. This used to rebuild `shareViaTelegram` inline and, in doing so,
  // dropped its `openLink` step: on a Telegram client that exposes `openLink`
  // but not `openTelegramLink`, sharing silently did nothing. Same helper as
  // the tenant invite and referral links now, copy-to-clipboard if it cannot
  // dispatch at all.
  const share = () => {
    const text = t('platform.adminInviteLink.shareText', 'Muddato admin taklifi');
    if (!shareViaTelegram(tgLink, text)) void copy('tg', tgLink);
  };

  const roleLabel = role === 'super_admin'
    ? t('platform.adminInviteLink.roleSuperAdmin')
    : t('platform.adminInviteLink.roleSupport');

  const roleOptions = [
    { value: 'support',     label: t('platform.adminInviteLink.roleSupport') },
    { value: 'super_admin', label: t('platform.adminInviteLink.roleSuperAdmin') },
  ];

  // ── Success state: generated link ready to copy/share ────────────────────
  if (tgLink) {
    return (
      <Modal
        open={open}
        onClose={onClose}
        title={t('platform.adminInviteLink.title')}
        subtitle={t('platform.adminInviteLink.generatedSubtitle', 'Havola tayyor — yuboring va u bir marta ishlaydi.')}
        footer={
          <Button variant="primary" size="md" onClick={onClose}>
            {t('platform.adminInviteLink.done')}
          </Button>
        }
      >
        <div className="space-y-4">
          {/* Claim context */}
          <p className="text-small text-ink-soft">
            <Trans
              i18nKey="platform.adminInviteLink.claimNote"
              values={{ role: roleLabel }}
              components={{ b: <b /> }}
            />
          </p>

          {/* Telegram link (preferred) */}
          <Field
            label={t('platform.adminInviteLink.tgLink')}
            hint={t('platform.adminInviteLink.tgLinkHint')}
          >
            <div className="flex gap-2">
              <input
                value={tgLink}
                readOnly
                className={inputCls + ' font-mono text-caption'}
                aria-label={t('platform.adminInviteLink.tgLink')}
              />
              <button
                type="button"
                onClick={() => copy('tg', tgLink)}
                aria-label={t('platform.adminInviteLink.copy', 'Nusxa olish')}
                className="inline-flex h-11 w-11 shrink-0 items-center justify-center rounded-control border border-line bg-cream transition hover:border-ink/30"
              >
                {copied === 'tg'
                  ? <CheckCircle2 size={16} className="text-success" />
                  : <Copy size={16} />}
              </button>
              <Button variant="primary" size="md" onClick={share}>
                <Share2 size={14} /> {t('platform.adminInviteLink.share', 'Ulashish')}
              </Button>
            </div>
          </Field>

          {/* Web link (fallback) */}
          <Field
            label={t('platform.adminInviteLink.webLink')}
            hint={t('platform.adminInviteLink.webLinkHint')}
          >
            <div className="flex gap-2">
              <input
                value={webLink}
                readOnly
                className={inputCls + ' font-mono text-caption'}
                aria-label={t('platform.adminInviteLink.webLink')}
              />
              <button
                type="button"
                onClick={() => copy('web', webLink)}
                aria-label={t('platform.adminInviteLink.copy', 'Nusxa olish')}
                className="inline-flex h-11 w-11 shrink-0 items-center justify-center rounded-control border border-line bg-cream transition hover:border-ink/30"
              >
                {copied === 'web'
                  ? <CheckCircle2 size={16} className="text-success" />
                  : <Link2 size={16} />}
              </button>
            </div>
          </Field>

          {expires && (
            <p className="text-micro text-muted">
              {t('platform.adminInviteLink.expires')}{' '}
              <span className="font-mono">{expires}</span>
            </p>
          )}
        </div>
      </Modal>
    );
  }

  // ── Pre-generate state: choose role + generate ────────────────────────────
  return (
    <Modal
      open={open}
      onClose={onClose}
      title={t('platform.adminInviteLink.title')}
      subtitle={t('platform.adminInviteLink.subtitle')}
      footer={
        <>
          <Button variant="ghost" size="md" type="button" onClick={onClose}>
            {t('common.cancel')}
          </Button>
          <Button
            variant="primary"
            size="md"
            onClick={generate}
            loading={create.isPending}
          >
            {create.isPending
              ? <><Loader2 size={16} className="animate-spin" /> {t('platform.adminInviteLink.generating', 'Yaratilmoqda…')}</>
              : <><Link2 size={16} /> {t('platform.adminInviteLink.generate')}</>}
          </Button>
        </>
      }
    >
      <div className="space-y-4">
        <Field label={t('platform.adminInviteLink.role')} required>
          <Select
            value={role}
            onChange={(v) => setRole(v as 'super_admin' | 'support')}
            options={roleOptions}
          />
        </Field>

        {role === 'super_admin' && (
          <div className="flex items-start gap-2 rounded-control border border-warn/30 bg-warn-soft px-3 py-2.5 text-caption text-ink">
            <ShieldAlert size={16} className="mt-0.5 shrink-0 text-warn" />
            <span>{t('platform.adminInviteLink.superAdminWarn')}</span>
          </div>
        )}

        {err && (
          <div className="rounded-control border border-danger/30 bg-[color:var(--c-danger-soft)] px-4 py-3 text-body text-danger">
            {err}
          </div>
        )}
      </div>
    </Modal>
  );
}
