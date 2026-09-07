/**
 * The upload gate every picker shares.
 *
 * The import picker shipped a correct `accept` attribute and no size check at
 * all, so a 400 MB spreadsheet was read and posted before anything objected.
 * These tests pin the rule, and pin the picker hint to the rule — an `accept`
 * that offers what the validator rejects is its own bug.
 */
import { describe, expect, it } from 'vitest';
import {
  IMPORT_FILE, RECEIPT_FILE, formatFileSize, validateFile, type FileKind,
} from './fileValidation';

/** A File of a given name and size, without allocating the bytes. */
function fakeFile(name: string, bytes: number): File {
  const f = new File(['x'], name);
  Object.defineProperty(f, 'size', { value: bytes });
  return f;
}

const MB = 1024 * 1024;

describe('the picker hint and the rule agree', () => {
  it.each([['import', IMPORT_FILE], ['receipt', RECEIPT_FILE]] as const)(
    '%s: every accepted extension appears in the accept attribute',
    (_label, kind: FileKind) => {
      for (const ext of kind.extensions) expect(kind.accept).toContain(ext);
    },
  );

  it.each([['import', IMPORT_FILE], ['receipt', RECEIPT_FILE]] as const)(
    '%s: accept also carries MIME types, not just extensions',
    (_label, kind: FileKind) => {
      // Extensions alone break on Android pickers; MIME alone breaks where the
      // OS reports octet-stream. Both, always.
      expect(kind.accept).toMatch(/\w+\/[\w.+-]+/);
    },
  );
});

describe('import accepts spreadsheets only', () => {
  it.each(['book.xlsx', 'rows.csv', 'UPPER.CSV', 'Mijozlar.XLSX'])('accepts %s', (name) => {
    expect(validateFile(fakeFile(name, 1024), IMPORT_FILE)).toBeNull();
  });

  it.each(['sheet.xls', 'data.json', 'notes.txt', 'photo.png', 'archive.zip', 'noext'])(
    'rejects %s', (name) => {
      expect(validateFile(fakeFile(name, 1024), IMPORT_FILE)?.key).toBe('file.errType');
    },
  );

  it('rejects a double extension that only ends in a safe one', () => {
    // `.csv` last wins — that is the extension the OS and the server both see.
    expect(validateFile(fakeFile('payload.exe.csv', 10), IMPORT_FILE)).toBeNull();
    expect(validateFile(fakeFile('payload.csv.exe', 10), IMPORT_FILE)?.key).toBe('file.errType');
  });
});

describe('receipts accept photos and bank PDFs', () => {
  it.each(['receipt.pdf', 'photo.png', 'photo.jpg', 'photo.jpeg', 'SCAN.PDF'])(
    'accepts %s', (name) => {
      expect(validateFile(fakeFile(name, 2048), RECEIPT_FILE)).toBeNull();
    },
  );

  it.each(['receipt.heic', 'clip.mp4', 'doc.docx', 'sheet.xlsx'])('rejects %s', (name) => {
    expect(validateFile(fakeFile(name, 2048), RECEIPT_FILE)?.key).toBe('file.errType');
  });
});

describe('the 5 MB ceiling', () => {
  it('accepts exactly the limit', () => {
    expect(validateFile(fakeFile('a.csv', 5 * MB), IMPORT_FILE)).toBeNull();
  });

  it('rejects one byte over', () => {
    const r = validateFile(fakeFile('a.csv', 5 * MB + 1), IMPORT_FILE);
    expect(r?.key).toBe('file.errTooLarge');
    expect(r?.params).toEqual({ max: '5 MB' });
  });

  it('rejects the 400 MB spreadsheet that motivated this', () => {
    expect(validateFile(fakeFile('huge.xlsx', 400 * MB), IMPORT_FILE)?.key)
      .toBe('file.errTooLarge');
  });

  it('reports the wrong TYPE first when a file is both wrong and huge', () => {
    // Otherwise the user re-exports at 4 MB in the same unsupported format and
    // is rejected a second time.
    expect(validateFile(fakeFile('huge.json', 400 * MB), IMPORT_FILE)?.key)
      .toBe('file.errType');
  });
});

describe('empty files', () => {
  it('are rejected before anything else', () => {
    expect(validateFile(fakeFile('a.csv', 0), IMPORT_FILE)?.key).toBe('file.errEmpty');
  });
});

describe('formatFileSize', () => {
  it.each([
    [512, '512 B'],
    [2048, '2 KB'],
    [5 * MB, '5.0 MB'],
    [Math.round(1.44 * MB), '1.4 MB'],
  ])('%i -> %s', (bytes, want) => {
    expect(formatFileSize(bytes)).toBe(want);
  });
});
