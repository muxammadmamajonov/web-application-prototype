import { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { useNavigate } from 'react-router';
import { CheckCircle2, Download, ShieldCheck, Trash2 } from 'lucide-react';
import { BackChip } from '@/components/mobile/primitives';
import { api } from '@/lib/api';
import { saveBlob, safeFilename } from '@/lib/download';
import { apiErrorMessage } from '@/lib/errors';
import { Button } from '@/components/ui/Button';
import { toast, confirmDialog } from '@/lib/feedback';

/** (#248) Customer self-service privacy: download my own data (GDPR access) and
 *  request erasure (a request the shop reviews — never an auto-delete).
 *
 *  Rendered two ways: standalone at the deep-linkable /me/privacy route, and
 *  embedded as a tab inside ProfilePage's SettingsShell.
 *
 *  `embedded` is what the file's own comment used to only ASSERT. It claimed the
 *  shell "already owns the title chrome" and that the back chip was "a no-op
 *  extra affordance" — neither was acted on, so inside Profile this page painted
 *  a second `<h1>` under the shell's own, repeated the title and subtitle the
 *  rail row had just shown, added a second back arrow pointing somewhere else
 *  (`nav(-1)` vs the shell's "return to the tab list"), and re-capped itself to
 *  448px inside a ~1150px detail pane, so this one tab visibly shrank while
 *  every sibling filled the width. Embedded now means embedded. */
export default function PrivacyPage({ embedded = false }: { embedded?: boolean } = {}) {
  const { t } = useTranslation();
  const nav = useNavigate();
  const [exporting, setExporting] = useState(false);
  const [requesting, setRequesting] = useState(false);
  const [requested, setRequested] = useState(false);

  const exportData = async () => {
    setExporting(true);
    try {
      // (#10) The export is assembled server-side per request and has no stable
      // id, so there is nothing for the server to sign — this is the one flow
      // that legitimately builds its own blob. It now goes through `saveBlob`,
      // which fixes two real bugs in the hand-rolled version: the anchor was
      // never appended to the document (stricter engines ignore a detached
      // `click()`), and `revokeObjectURL` ran on the very next line, racing the
      // download it was meant to deliver. `saveBlob` defers revocation and
      // reports honestly when it cannot reach the filesystem at all, which is
      // the case inside Telegram — where the old code failed silently.
      const { data } = await api.get('/me/privacy/export');
      const blob = new Blob([JSON.stringify(data, null, 2)], { type: 'application/json' });
      saveBlob(blob, safeFilename('my-muddato-data', '.json'));
    } catch (ex) {
      // Keep the real cause rather than collapsing every failure into one line
      // — offline and refused are not the same.
      toast.error(apiErrorMessage(ex));
    } finally {
      setExporting(false);
    }
  };

  const requestErasure = async () => {
    const ok = await confirmDialog({
      danger: true,
      title: t('clientPrivacy.eraseTitle'),
      message: t('clientPrivacy.eraseConfirm'),
    });
    if (!ok) return;
    setRequesting(true);
    try {
      await api.post('/me/privacy/erasure-request');
      setRequested(true);
      toast.success(t('clientPrivacy.eraseDone'));
    } catch (ex) {
      toast.error(apiErrorMessage(ex));
    } finally {
      setRequesting(false);
    }
  };

  return (
    // Standalone keeps the phone-shaped column; embedded fills the detail pane
    // it is given, like every sibling tab.
    <div className={embedded ? undefined : 'mx-auto w-full max-w-panel'}>
      {!embedded && (
        <>
          <BackChip onClick={() => nav(-1)} label={t('client.back', 'Orqaga')} />

          <header className="flex items-center gap-3">
            <span className="grid h-9 w-9 shrink-0 place-items-center rounded-control bg-[color:var(--c-accent-soft)] text-accent">
              <ShieldCheck size={18} />
            </span>
            <div className="min-w-0">
              <h1 className="text-subsection font-semibold tracking-tight">{t('clientPrivacy.title')}</h1>
              <p className="truncate text-caption text-muted">{t('clientPrivacy.subtitle')}</p>
            </div>
          </header>
        </>
      )}

      <div className={embedded ? 'space-y-3' : 'mt-5 space-y-3'}>
        <section className="card space-y-2 p-4">
          <h2 className="text-body font-semibold">{t('clientPrivacy.exportTitle')}</h2>
          <p className="text-small text-ink-soft">{t('clientPrivacy.exportHint')}</p>
          <Button variant="outline" onClick={exportData} loading={exporting}>
            <Download size={16} />
            {t('clientPrivacy.exportBtn')}
          </Button>
        </section>

        <section className="card space-y-2 p-4">
          <h2 className="text-body font-semibold">{t('clientPrivacy.eraseTitle')}</h2>
          <p className="text-small text-ink-soft">{t('clientPrivacy.eraseHint')}</p>
          {requested ? (
            <p className="inline-flex items-center gap-1.5 text-small font-medium text-success">
              <CheckCircle2 size={16} /> {t('clientPrivacy.eraseDone')}
            </p>
          ) : (
            <Button variant="outline" onClick={requestErasure} loading={requesting}>
              <Trash2 size={16} />
              {t('clientPrivacy.eraseBtn')}
            </Button>
          )}
        </section>
      </div>
    </div>
  );
}
