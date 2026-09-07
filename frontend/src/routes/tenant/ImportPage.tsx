import { useMemo, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { useQueryClient } from '@tanstack/react-query';
import { V2_SURFACES_ENABLED } from '@/routes/_scope';
import { apiErrorMessage } from '@/lib/errors';
import { confirmDialog } from '@/lib/feedback';
import { AlertTriangle, CheckCircle2, Download, FileUp, Loader2, Upload } from 'lucide-react';
import { PageHeader } from '@/components/ui/PageHeader';
import { MobileHeader } from '@/components/mobile/primitives';
import { Button } from '@/components/ui/Button';
import { Field } from '@/components/ui/Modal';
import { Select } from '@/components/ui/Select';
import {
  importPreview, importCommit,
  type ImportPreview, type ImportResult,
} from '@/lib/queries';
import { queryKeys } from '@/lib/queryKeys';
import { IMPORT_FILE, fileRejectionMessage, validateFile } from '@/lib/fileValidation';
import { safeFilename, saveBlob } from '@/lib/download';

type Target = 'products' | 'customers';

const FIELDS: Record<Target, { key: string; required?: boolean }[]> = {
  products: [
    { key: 'name_uz', required: true },
    { key: 'name_ru' },
    { key: 'sku' },
    { key: 'category' },
    { key: 'brand' },
    { key: 'base_price' },
    { key: 'cost_price' },
    { key: 'stock' },
    { key: 'min_stock_level' },
  ],
  customers: [
    { key: 'full_name', required: true },
    { key: 'phone', required: true },
    { key: 'secondary_phone' },
    { key: 'passport_series' },
    { key: 'passport_number' },
    { key: 'address' },
    { key: 'employment_place' },
  ],
};

/** Two-step import wizard: upload → map columns → commit. */
export default function ImportPage() {
  const { t } = useTranslation();
  const qc = useQueryClient();
  // Customers only — there is no product model behind a product import. The
  // `products` target stays defined but its selector is not presented.
  const [target, setTarget] = useState<Target>(V2_SURFACES_ENABLED ? 'products' : 'customers');
  const [file, setFile] = useState<File | null>(null);
  const [preview, setPreview] = useState<ImportPreview | null>(null);
  const [mapping, setMapping] = useState<Record<string, string>>({});
  const [result, setResult] = useState<ImportResult | null>(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  const fields = FIELDS[target];

  /** Best-effort auto-map: same-name column → field. */
  const autoMap = (cols: string[]): Record<string, string> => {
    const lc = cols.map((c) => c.toLowerCase().trim());
    const m: Record<string, string> = {};
    for (const f of fields) {
      const idx = lc.indexOf(f.key);
      if (idx >= 0) m[f.key] = cols[idx]!;
    }
    return m;
  };

  const onUpload = async (f: File) => {
    setFile(f); setErr(null); setResult(null); setBusy(true);
    try {
      const p = await importPreview(f);
      setPreview(p);
      setMapping(autoMap(p.columns));
    } catch (ex) {
      setErr(apiErrorMessage(ex));
      setPreview(null);
    } finally { setBusy(false); }
  };

  // Up to 10 000 records are written by one click, so restate the scale and the
  // mapping first and commit only on an explicit confirmation.
  const commit = async () => {
    if (!file) return;
    const missing = fields.filter((f) => f.required && !mapping[f.key]);
    if (missing.length) {
      setErr(t('import.errRequired', { fields: missing.map((f) => t(`import.field.${f.key}`, f.key)).join(', ') }));
      return;
    }
    const mapped = fields.filter((f) => mapping[f.key]).length;
    if (!(await confirmDialog({
      title: t('import.confirmTitle', 'Import qilishni tasdiqlang'),
      message: t('import.confirmBody', {
        rows: preview?.total_rows ?? 0,
        mapped,
        total: fields.length,
        defaultValue: '{{rows}} ta qator o‘qiladi va mijoz sifatida yaratiladi. {{total}} ta maydondan {{mapped}} tasi biriktirilgan. Xatolik bo‘lgan qatorlar yaratilmaydi va ro‘yxati ko‘rsatiladi.',
      }),
      confirmLabel: t('import.commit', 'Import qilish'),
    }))) return;
    setErr(null); setBusy(true);
    try {
      const r = await importCommit(target, file, mapping);
      setResult(r);
      await qc.invalidateQueries({ queryKey: queryKeys.tenant.importTarget(target) });
    } catch (ex) {
      // Localized, never a raw backend token. The file, mapping and preview all
      // survive, so a recoverable failure can be retried as-is.
      setErr(apiErrorMessage(ex));
    } finally { setBusy(false); }
  };

  // Rejected rows leave as CSV, so they can be corrected in the spreadsheet the
  // data came from and re-imported — not hand-copied off the screen.
  const downloadRejected = () => {
    const errs = result?.errors ?? [];
    if (errs.length === 0) return;
    const esc = (v: string) => `"${String(v).replace(/"/g, '""')}"`;
    const csv = [
      [t('import.errRow', 'Qator'), t('import.errReason', 'Sabab'), t('import.errFields', 'Maydonlar')].map(esc).join(','),
      ...errs.map((e) => [String(e.row), e.error, (e.fields ?? []).join(' ')].map(esc).join(',')),
    ].join('\r\n');
    // BOM so Excel opens the Cyrillic/Latin text in the right encoding.
    // Delivered through the shared helper: the hand-rolled anchor+blob this
    // replaced does nothing at all inside Telegram's WebView, so a shop owner
    // importing from their phone tapped "download rejected rows" and never got
    // the file \u2014 with no error to explain it.
    saveBlob(
      new Blob(['\uFEFF' + csv], { type: 'text/csv;charset=utf-8' }),
      safeFilename(`import-rejected-${target}`, '.csv'),
    );
  };

  const reset = () => {
    setFile(null); setPreview(null); setMapping({}); setResult(null); setErr(null);
  };

  const colChoices = useMemo(() => [{ label: t('import.skipColumn', '— tanlanmagan —'), value: '' },
    ...(preview?.columns ?? []).map((c) => ({ label: c, value: c }))], [preview, t]);

  return (
    <>
      <div className="hidden md:block">
        <PageHeader
          title={t('import.title', 'Excel / CSV import')}
          subtitle={t('import.subtitle', '.xlsx yoki .csv yuklang — ustunlarni moslang — saqlang.')}
        />
      </div>
      <div className="md:hidden">
        <MobileHeader title={t('import.title', 'Excel / CSV import')} />
        <div className="mb-3 rounded-card border border-dashed border-line bg-surface px-4 py-2.5 text-caption text-muted">
          {t('import.bestOnDesktop', 'Import kompyuterda qulayroq — fayl yuklash va ustunlarni moslash uchun.')}
        </div>
      </div>

      {/* Step 1: target + upload */}
      {!preview && !result && (
        <div className="card p-6">
          {V2_SURFACES_ENABLED && (
            <div className="mb-4 flex gap-2">
              {(['products', 'customers'] as const).map((tg) => (
                <button key={tg} onClick={() => { setTarget(tg); reset(); }}
                        className={`rounded-pill border px-4 py-1.5 text-body ${
                          target === tg ? 'border-accent bg-cream font-semibold text-accent' : 'border-line text-muted'
                        }`}>
                  {t(`import.target.${tg}`, tg)}
                </button>
              ))}
            </div>
          )}

          <label className="flex flex-col items-center justify-center gap-2 rounded-card border-2 border-dashed border-line bg-cream/40 px-6 py-12 text-center cursor-pointer hover:border-accent">
            <FileUp size={28} className="text-muted" />
            <div className="text-body">{t('import.dropHint', 'Faylni tanlash uchun bosing')}</div>
            <div className="text-caption text-muted">.xlsx · .csv · max 5MB</div>
            {/* `accept` comes from the same constant the validator uses, so the
                dialog cannot offer a format that is then rejected. It used to
                include application/vnd.ms-excel (.xls), which is NOT supported.
                The attribute is only a hint — every dialog has an "All files"
                escape and Telegram's picker ignores it outright — so the check
                below is what actually decides. */}
            <input type="file"
                   accept={IMPORT_FILE.accept}
                   className="hidden"
                   onChange={(e) => {
                     const f = e.target.files?.[0];
                     // Reset the input so picking the SAME rejected file again
                     // still fires onChange; without this a corrected re-pick of
                     // an identically-named file silently does nothing.
                     e.target.value = '';
                     if (!f) return;
                     const bad = validateFile(f, IMPORT_FILE);
                     if (bad) {
                       // Rejected here, before the file is read or posted. The
                       // page advertised "max 5MB" while enforcing nothing, so a
                       // 400 MB workbook was base64'd and uploaded over mobile
                       // data before anything objected.
                       setErr(fileRejectionMessage(bad));
                       return;
                     }
                     setErr(null);
                     onUpload(f);
                   }} />
          </label>
          {busy && <div className="mt-3 flex items-center gap-2 text-body text-muted"><Loader2 size={14} className="animate-spin" /> {t('common.loading')}</div>}
          {err && <div className="mt-3 text-body text-danger">{err}</div>}
        </div>
      )}

      {/* Step 2: map */}
      {preview && !result && (
        <div className="grid gap-4">
          <div className="card p-5">
            <div className="flex flex-wrap items-center justify-between gap-2">
              <div>
                <div className="text-micro uppercase tracking-wider text-muted">{file?.name}</div>
                <div className="text-body">{t('import.foundRows', { count: preview.total_rows })}</div>
              </div>
              <Button variant="ghost" size="sm" onClick={reset}>{t('common.cancel')}</Button>
            </div>
          </div>

          <div className="card p-5">
            <h3 className="mb-3 text-small font-semibold uppercase tracking-wider text-muted">
              {t('import.mapTitle', 'Ustunlarni moslang')}
            </h3>
            <div className="grid gap-3 sm:grid-cols-2">
              {fields.map((f) => (
                <Field key={f.key} label={`${t(`import.field.${f.key}`, f.key)}${f.required ? ' *' : ''}`}>
                  <Select
                    value={mapping[f.key] ?? ''}
                    onChange={(v) => setMapping((p) => ({ ...p, [f.key]: v }))}
                    options={colChoices.map((c) => ({ value: c.value, label: c.label }))}
                  />
                </Field>
              ))}
            </div>
          </div>

          <div className="card p-5">
            <h3 className="mb-3 text-small font-semibold uppercase tracking-wider text-muted">
              {t('import.preview', 'Namuna')}
            </h3>
            {/* Desktop (md+): wide sample grid. */}
            <div className="hidden overflow-x-auto md:block">
              <table className="w-full text-small">
                <thead>
                  <tr className="border-b border-line text-left text-muted">
                    {preview.columns.map((c) => <th scope="col" key={c} className="px-2 py-1">{c}</th>)}
                  </tr>
                </thead>
                <tbody>
                  {preview.sample.map((row, i) => (
                    <tr key={i} className="border-b border-line/50">
                      {preview.columns.map((c) => <td key={c} className="px-2 py-1">{row[c] ?? ''}</td>)}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            {/* Mobile (<md): each sample row as a stacked column → value card so the
                arbitrary spreadsheet columns stay readable instead of scrolling. */}
            <ul className="space-y-2.5 md:hidden">
              {preview.sample.map((row, i) => (
                <li key={i} className="rounded-card border border-line bg-surface px-3 py-2.5">
                  <div className="space-y-1">
                    {preview.columns.map((c) => (
                      <div key={c} className="flex items-baseline justify-between gap-3">
                        <span className="min-w-0 truncate text-caption text-muted">{c}</span>
                        <span className="min-w-0 truncate text-small text-ink">{row[c] ?? '—'}</span>
                      </div>
                    ))}
                  </div>
                </li>
              ))}
            </ul>
          </div>

          {err && <div className="text-body text-danger">{err}</div>}
          <div className="flex justify-end">
            <Button variant="primary" size="md" onClick={commit} loading={busy}>
              {busy ? <Loader2 size={14} className="animate-spin" /> : <Upload size={14} />}
              {t('import.commit', 'Import qilish')}
            </Button>
          </div>
        </div>
      )}

      {/* Step 3: result */}
      {result && (() => {
        const rejected = result.ok === false || (result.created === 0 && result.errors.length > 0);
        return (
          <div className="card overflow-hidden">
            <div className="flex flex-col items-center gap-2 px-6 pt-10 pb-6 text-center">
              {rejected ? (
                <>
                  <span className="grid h-12 w-12 place-items-center rounded-pill bg-[color:var(--c-danger-soft)]">
                    <AlertTriangle size={24} className="text-danger" />
                  </span>
                  <h3 className="text-section font-semibold">{t('import.rejectedTitle', 'Import bekor qilindi')}</h3>
                  <p className="max-w-panel text-body text-muted">
                    {t('import.rejectedBody', {
                      count: result.errors.length,
                      defaultValue: "Hech narsa qo'shilmadi. {{count}} ta qatorda xatolik bor — to'g'rilab, qaytadan yuklang.",
                    })}
                  </p>
                </>
              ) : (
                <>
                  <span className="grid h-12 w-12 place-items-center rounded-pill bg-[color:var(--c-success-soft)]">
                    <CheckCircle2 size={24} className="text-success" />
                  </span>
                  <h3 className="text-section font-semibold">{t('import.done', 'Tugadi')}</h3>
                  <p className="text-body text-muted">
                    {t('import.successBody', { created: result.created, defaultValue: '{{created}} ta yozuv qo\'shildi' })}
                  </p>
                </>
              )}
            </div>

            {result.errors.length > 0 && (
              <div className="border-t border-line">
                <div className="flex flex-wrap items-center justify-between gap-2 border-b border-line bg-cream/60 px-5 py-2">
                  <span className="text-micro font-medium uppercase tracking-wider text-muted">
                    {t('import.errorsTitle', { count: result.errors.length, defaultValue: 'Xatoliklar ({{count}})' })}
                  </span>
                  {/* Take the rejected rows away and fix them. */}
                  <Button variant="ghost" size="sm" onClick={downloadRejected}>
                    <Download size={14} /> {t('import.downloadRejected', 'Xatoli qatorlarni yuklab olish')}
                  </Button>
                </div>
                <ul className="max-h-72 divide-y divide-line overflow-y-auto">
                  {result.errors.map((e, i) => (
                    <li key={i} className="flex items-start gap-3 px-5 py-2.5 text-body">
                      <span className="mt-0.5 shrink-0 rounded-icon bg-[color:var(--c-danger-soft)] px-1.5 py-0.5 font-mono text-micro text-danger">
                        {t('import.rowLabel', 'qator')} {e.row}
                      </span>
                      <span className="min-w-0 text-ink-soft">{e.error}</span>
                    </li>
                  ))}
                </ul>
              </div>
            )}

            <div className="flex justify-center gap-2 border-t border-line px-6 py-4">
              <Button variant="primary" size="md" onClick={reset}>
                {rejected ? t('import.fixRetry', 'Tuzatib qaytadan') : t('import.again', 'Yana import')}
              </Button>
            </div>
          </div>
        );
      })()}
    </>
  );
}
