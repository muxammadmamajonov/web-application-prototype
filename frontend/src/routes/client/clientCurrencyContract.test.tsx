// @vitest-environment node
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { describe, expect, it } from 'vitest';

const pages = [
  './HomePage.tsx',
  './InstallmentDetailPage.tsx',
  './PayPage.tsx',
  './PayoffPage.tsx',
  './ReceiptUploadPage.tsx',
];

describe('customer money labels', () => {
  it.each(pages)('%s uses the contract currency instead of the UZS translation default', (page) => {
    const source = readFileSync(fileURLToPath(new URL(page, import.meta.url)), 'utf8');
    expect(source).not.toContain("t('billing.currency')");
  });
});
