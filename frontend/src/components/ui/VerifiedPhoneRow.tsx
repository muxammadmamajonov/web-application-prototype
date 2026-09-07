import { useTranslation } from 'react-i18next';
import { ShieldCheck } from 'lucide-react';
import { Field } from '@/components/ui/Modal';
import { PhoneVerificationCard, type IdentitySnapshot } from '@/components/ui/PhoneVerificationCard';

/**
 * The phone that IS this person's identity, as opposed to the one they typed.
 *
 * Two different facts share a shape and a keyboard, and a screen that renders
 * them the same way teaches people the wrong thing:
 *
 *   verified phone — proved through Telegram, decides who receives this
 *                    person's contracts and notifications. Read-only here;
 *                    changing it is a support workflow, not a form field.
 *   contact phone  — what somebody typed. A business record. It is not proof,
 *                    it selects no recipient, and editing it changes nothing
 *                    about identity.
 *
 * Shown masked even to its owner: a profile screen is read over shoulders and
 * screenshotted, and the last two digits are enough to recognise your own
 * number.
 */
export function VerifiedPhoneRow({
  snapshot, onRefetch, onVerified,
}: {
  snapshot: IdentitySnapshot | null;
  onRefetch: () => Promise<IdentitySnapshot | null>;
  onVerified?: (snapshot: IdentitySnapshot) => void;
}) {
  const { t } = useTranslation();

  if (snapshot?.state === 'VERIFIED') {
    return (
      <Field label={t('profile.verifiedPhone', 'Tasdiqlangan raqam')}
             hint={t('profile.verifiedPhoneHint',
                     'Telegram orqali tasdiqlangan. O‘zgartirish uchun yordamga murojaat qiling.')}>
        <div data-testid="verified-phone"
             className="flex h-11 items-center gap-2 rounded-control border border-line bg-cream px-3 text-subsection tabular-nums text-ink-soft">
          <ShieldCheck size={16} className="shrink-0 text-success" aria-hidden="true" />
          <span className="truncate">{snapshot.phoneMasked}</span>
          <span className="ml-auto shrink-0 text-micro font-medium text-success">
            {t('profile.verifiedBadge', 'Tasdiqlangan')}
          </span>
        </div>
      </Field>
    );
  }

  return (
    <Field label={t('profile.verifiedPhone', 'Tasdiqlangan raqam')}>
      <div data-testid="verify-phone-slot">
        <PhoneVerificationCard snapshot={snapshot} onRefetch={onRefetch} onVerified={onVerified} />
      </div>
    </Field>
  );
}
