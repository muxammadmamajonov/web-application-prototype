/**
 * What the receipt picker offers, and what it lets through.
 *
 * REPRODUCED ON DESKTOP: the picker accepted arbitrary files, HTML among them.
 * Two separate defects hid behind that one symptom.
 *
 *   1. The page carried its OWN list instead of the shared rule — and that list
 *      included HEIC, which the server cannot decode. An iPhone photo was
 *      accepted here, uploaded over mobile data, and refused at the end.
 *   2. `accept` was treated as the gate. It is not one: every desktop dialog
 *      offers "All files" and Telegram's in-app picker ignores the attribute
 *      entirely.
 *
 * None of this is security. The server re-decodes every byte and that copy is
 * the one that counts; this is about not wasting a customer's data allowance
 * and not lying to them about what will work.
 */
import { describe, expect, it } from 'vitest';
import { RECEIPT_FILE, validateFile } from './fileValidation';

function file(name: string, { type = '', size = 1024 } = {}): File {
  const f = new File([new Uint8Array(size)], name, { type });
  Object.defineProperty(f, 'size', { value: size });
  return f;
}

describe('receipt file rules', () => {
  describe('the picker hint', () => {
    it('offers exactly the formats the server accepts', () => {
      for (const ext of ['.pdf', '.png', '.jpg', '.jpeg', '.webp']) {
        expect(RECEIPT_FILE.accept).toContain(ext);
      }
    });

    it('does not offer HEIC, which the server cannot decode', () => {
      // Offering it means the OS hands over the original instead of converting
      // to JPEG, and the upload fails after the customer has already paid for
      // the data.
      expect(RECEIPT_FILE.accept.toLowerCase()).not.toContain('heic');
      expect(RECEIPT_FILE.accept.toLowerCase()).not.toContain('heif');
    });

    it('does not offer SVG', () => {
      // A document format with script execution, shown back to a signed-in
      // reviewer. It is refused by the server; offering it here would only
      // teach people to try.
      expect(RECEIPT_FILE.accept.toLowerCase()).not.toContain('svg');
    });

    it('offers the same set the validator enforces', () => {
      // A picker that offers what the validator rejects is its own bug.
      for (const ext of RECEIPT_FILE.extensions) {
        expect(RECEIPT_FILE.accept).toContain(ext);
      }
    });
  });

  describe('the gate that actually runs', () => {
    it.each(['chek.pdf', 'chek.png', 'chek.jpg', 'chek.jpeg', 'chek.webp'])(
      'accepts %s', (name) => {
        expect(validateFile(file(name), RECEIPT_FILE)).toBeNull();
      });

    it.each([
      ['chek.html', 'the file the founder was able to pick'],
      ['chek.svg', 'script execution in an image'],
      ['chek.heic', 'the server cannot decode it'],
      ['chek.exe', 'executable'],
      ['chek.zip', 'archive'],
      ['chek.js', 'script'],
      ['chek', 'no extension at all'],
    ])('rejects %s (%s)', (name) => {
      const r = validateFile(file(name), RECEIPT_FILE);
      expect(r?.key).toBe('file.errType');
    });

    it('is not fooled by an uppercase extension', () => {
      expect(validateFile(file('CHEK.HTML'), RECEIPT_FILE)?.key).toBe('file.errType');
      expect(validateFile(file('CHEK.PNG'), RECEIPT_FILE)).toBeNull();
    });

    it('ignores the declared MIME type, which the uploader controls', () => {
      // An HTML file renamed and relabelled is still refused by extension —
      // and by the server, by content.
      expect(
        validateFile(file('chek.html', { type: 'image/png' }), RECEIPT_FILE)?.key,
      ).toBe('file.errType');
    });

    it('rejects a double extension by its real one', () => {
      expect(validateFile(file('chek.png.html'), RECEIPT_FILE)?.key).toBe('file.errType');
    });

    it('reports an empty file as empty rather than as the wrong type', () => {
      expect(validateFile(file('chek.png', { size: 0 }), RECEIPT_FILE)?.key)
        .toBe('file.errEmpty');
    });

    it('reports the wrong type before the size', () => {
      // Otherwise a 20 MB HTML file is told to shrink, and the user re-exports
      // it at the same useless format.
      const huge = file('chek.html', { size: 20 * 1024 * 1024 });
      expect(validateFile(huge, RECEIPT_FILE)?.key).toBe('file.errType');
    });

    it('enforces a size ceiling on an accepted type', () => {
      const huge = file('chek.png', { size: RECEIPT_FILE.maxBytes + 1 });
      expect(validateFile(huge, RECEIPT_FILE)?.key).toBe('file.errTooLarge');
    });
  });
});
