/**
 * One file-validation rule set, shared by every upload in the product.
 *
 * Uploads were each deciding for themselves what they accepted. The import
 * picker carried a correct `accept` attribute and **no size check at all**, so a
 * 400 MB spreadsheet was read, base64'd and posted before anything objected —
 * on a phone, over Uzbek mobile data.
 *
 * `accept` is a hint, not a gate: every desktop file dialog offers an "All
 * files" escape hatch, and Telegram's in-app picker ignores the attribute
 * outright. So the attribute narrows the dialog and `validateFile` is what
 * actually decides. Both come from the same constant here, which is the point —
 * a picker that offers what the validator rejects is its own bug.
 *
 * `validateFile` returns a STRUCTURED rejection rather than a sentence, so tests
 * can assert which rule fired without matching on Uzbek prose. Rendering it is a
 * separate step (`fileRejectionMessage`) that lives here too, because the keys
 * have to be literal at the point of translation: passing a computed key to
 * the translate function at each call site cannot be statically checked
 * against both bundles, and a missing uz/ru entry would reach a shop owner as
 * a raw key. (Spelled out rather than shown — localeCompleteness scans for
 * that exact call shape, so quoting it in a comment trips the guard.)
 */
import i18n from '@/i18n';

/** A file kind the product accepts, with the picker hint and the real rule. */
export interface FileKind {
  /** Value for an `<input type="file" accept="…">` — extensions AND MIME types.
   *  Extensions alone break on Android; MIME alone breaks where the OS reports
   *  `application/octet-stream` for a known extension. Sending both is why. */
  readonly accept: string;
  /** Lowercase extensions, with the dot. The authoritative check. */
  readonly extensions: readonly string[];
  /** Hard ceiling in bytes. */
  readonly maxBytes: number;
  /** Human-facing size cap, for the error message. */
  readonly maxLabel: string;
}

const MB = 1024 * 1024;

/** Bulk import: spreadsheets only, 5 MB. */
export const IMPORT_FILE: FileKind = {
  accept:
    '.xlsx,.csv,' +
    'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet,' +
    'text/csv',
  extensions: ['.xlsx', '.csv'],
  maxBytes: 5 * MB,
  maxLabel: '5 MB',
};

/**
 * Payment receipt: a photo or a bank PDF, 5 MB.
 *
 * WebP is here because the server accepts it; HEIC deliberately is NOT. An
 * iPhone offers HEIC by default and the server cannot decode it, so listing it
 * meant a customer picked a photo, waited for the upload, and was refused at
 * the end. Leaving it out makes the OS picker convert to JPEG instead — the
 * user never sees the problem.
 *
 * SVG is absent for a different reason: it is a document format with script
 * execution, and a payment proof is served back to a signed-in reviewer.
 */
export const RECEIPT_FILE: FileKind = {
  accept:
    '.pdf,.png,.jpg,.jpeg,.webp,' +
    'application/pdf,image/png,image/jpeg,image/webp',
  extensions: ['.pdf', '.png', '.jpg', '.jpeg', '.webp'],
  maxBytes: 5 * MB,
  maxLabel: '5 MB',
};

export type FileRejection =
  | { readonly key: 'file.errType'; readonly params: { allowed: string } }
  | { readonly key: 'file.errTooLarge'; readonly params: { max: string } }
  | { readonly key: 'file.errEmpty'; readonly params?: undefined };

/**
 * Decide whether a file may be uploaded. `null` means it may.
 *
 * Extension-based on purpose. Browsers disagree about `File.type` — Windows
 * reports `application/vnd.ms-excel` for `.csv`, some Android pickers report
 * nothing at all — so trusting it would reject files the user legitimately
 * chose. The extension is what the user sees and what the server re-checks.
 *
 * This is a convenience gate, not a security control: the caller runs in the
 * browser and anything here can be bypassed. The server repeats every one of
 * these checks, and that copy is the one that counts.
 */
export function validateFile(file: File, kind: FileKind): FileRejection | null {
  if (file.size === 0) return { key: 'file.errEmpty' };

  const name = file.name.toLowerCase();
  if (!kind.extensions.some((ext) => name.endsWith(ext))) {
    return { key: 'file.errType', params: { allowed: kind.extensions.join(', ') } };
  }

  // Checked after the type so a wrong-type file is told it is the wrong type,
  // rather than being told it is too large and re-exported at the same format.
  if (file.size > kind.maxBytes) {
    return { key: 'file.errTooLarge', params: { max: kind.maxLabel } };
  }

  return null;
}

/**
 * Render a rejection as a sentence the user can act on.
 *
 * Lives here so every picker words the same refusal the same way, and so the
 * keys are LITERAL at the point of translation. Passing the rejection's key
 * straight to the translate function at each call site would hand it a
 * variable, which the locale-completeness guard rejects — rightly, because a
 * computed key cannot be statically checked against both bundles, and a
 * missing uz/ru entry would surface as a raw key in front of a shop owner.
 */
export function fileRejectionMessage(rejection: FileRejection): string {
  switch (rejection.key) {
    case 'file.errType':
      return i18n.t('file.errType', { allowed: rejection.params.allowed });
    case 'file.errTooLarge':
      return i18n.t('file.errTooLarge', { max: rejection.params.max });
    case 'file.errEmpty':
      return i18n.t('file.errEmpty');
  }
}

/** `1.4 MB` — for showing what was picked, not for validation. */
export function formatFileSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < MB) return `${Math.round(bytes / 1024)} KB`;
  return `${(bytes / MB).toFixed(1)} MB`;
}
