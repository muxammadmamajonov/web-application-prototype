/**
 * Where a Telegram deep link lands before it knows where it is going.
 *
 * The `startapp` parameter is opaque on purpose — it rides in a group message
 * that anyone can read or forward, so it names a screen and proves nothing.
 * This page hands it to the server, which checks the signature, the expiry AND
 * whether THIS signed-in person may see what it points at, then answers with a
 * route or with nothing.
 *
 * "Nothing" is a single state for every reason: forged, expired, already
 * deleted, or simply not yours. Distinguishing them here would turn the screen
 * into an oracle for which payments exist and who they belong to — and none of
 * the distinctions help the person actually looking at it.
 */
import { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { useNavigate, useParams } from 'react-router';
import { FileQuestion, Loader2 } from 'lucide-react';
import { api } from '@/lib/api';
import { Button } from '@/components/ui/Button';
import { EmptyState } from '@/components/ui/EmptyState';
import { useSession } from '@/store/session';

type Phase = 'resolving' | 'unknown';

export default function ReviewIntentPage() {
  const { intent } = useParams<{ intent: string }>();
  const navigate = useNavigate();
  const { t, i18n } = useTranslation();
  const [phase, setPhase] = useState<Phase>('resolving');
  const setActiveTenant = useSession((s) => s.setActiveTenant);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      if (!intent) { setPhase('unknown'); return; }
      try {
        const r = await api.post('/review-intents/resolve', { intent });
        const body = r.data as { status: string; route?: string; tenant_id?: string };
        if (cancelled) return;
        if (body.status === 'ok' && body.route) {
          // The notification may have been sent to a shop the reader is not
          // currently switched into. Selecting it here is what makes the
          // destination resolve — the route is tenant-scoped.
          if (body.tenant_id) setActiveTenant(body.tenant_id);
          const lng = i18n.language?.startsWith('ru') ? 'ru' : 'uz';
          navigate(`/${lng}${body.route}`, { replace: true });
          return;
        }
        setPhase('unknown');
      } catch {
        // A failed request is indistinguishable from a refused one, on purpose.
        if (!cancelled) setPhase('unknown');
      }
    })();
    return () => { cancelled = true; };
  }, [intent, navigate, i18n.language, setActiveTenant]);

  if (phase === 'resolving') {
    return (
      <main id="main" tabIndex={-1} className="grid min-h-screen place-items-center focus:outline-none">
        <Loader2 size={28} className="animate-spin text-accent" aria-label={t('common.loading', 'Yuklanmoqda…')} />
      </main>
    );
  }

  return (
    <main id="main" tabIndex={-1} className="grid min-h-screen place-items-center px-4 focus:outline-none">
      <EmptyState
        icon={<FileQuestion size={28} />}
        title={t('review.unknownTitle', 'Havola ochilmadi')}
        body={t(
          'review.unknownBody',
          'Bu havola eskirgan yoki sizga tegishli emas. Ilovadan toʻlovlar roʻyxatini oching.',
        )}
        action={
          <Button variant="primary" size="md" onClick={() => navigate('/', { replace: true })}>
            {t('common.toHome', 'Bosh sahifaga')}
          </Button>
        }
      />
    </main>
  );
}
