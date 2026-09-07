import { downloadGranted } from './download';

/**
 * Fetch a contract PDF and hand it to the browser's viewer.
 *
 * Lives here, not in a page component, so the contract wizard's success
 * hand-off can offer the same action without importing one. Failures go through
 * the shared localized error vocabulary — never the raw backend `detail`.
 */
export async function openContractPdf(installmentId: string): Promise<void> {
  // (#10) Was `window.open` on a `blob:` URL — intercepted inside Telegram, and
  // blocked as a popup by some desktop browsers. `downloadGranted` asks the
  // server for a signed https URL first and only falls back to the blob path in
  // an ordinary browser, where it works.
  await downloadGranted('contract', installmentId, {
    path: `/tenant/installments/${installmentId}/contract.pdf`,
    filename: `contract-${installmentId}.pdf`,
  });
}
