/**
 * Document request state: idle → sending → sent | failed.
 *
 * INVARIANT: `sent` is set only on a confirmed delivery. Showing it
 * optimistically sends the user hunting for a file that never arrived.
 *
 * INVARIANT: the double-tap guard here is a courtesy. The server holds the
 * uniqueness claim and answers a repeat with ALREADY_SENT, because a client
 * can be bypassed entirely.
 */
import { useCallback, useRef, useState } from 'react';
import i18n from '@/i18n';
import { toast } from './feedback';
import { downloadGranted, type DownloadKind, type DownloadRequest } from './download';

export type SendState = 'idle' | 'sending' | 'sent' | 'failed';

export interface DocumentSend {
  readonly state: SendState;
  /** Request the document. A call while `sending` is ignored, not queued. */
  readonly send: () => Promise<void>;
  /** Back to `idle` — for a retry after a failure. */
  readonly reset: () => void;
  /** True while the request is in flight; bind to the button's `disabled`. */
  readonly busy: boolean;
}

export function useDocumentSend(
  kind: DownloadKind,
  id: string,
  req: DownloadRequest,
): DocumentSend {
  const [state, setState] = useState<SendState>('idle');
  // A ref as well as the state: two taps in the same frame both read the OLD
  // state value, so the state alone does not prevent a double submit. The ref
  // is written synchronously and is what actually closes the window.
  const inFlight = useRef(false);

  const send = useCallback(async () => {
    if (inFlight.current) return;
    inFlight.current = true;
    setState('sending');
    try {
      const result = await downloadGranted(kind, id, req);
      // `delegated` is the Telegram path: the server confirmed the document is
      // in the chat. `saved`/`opened` are the browser paths. Anything else did
      // not arrive, and saying "sent" would send the user hunting for it.
      const arrived = result === 'delegated' || result === 'saved' || result === 'opened';
      setState(arrived ? 'sent' : 'failed');
    } catch {
      setState('failed');
      toast.error(i18n.t('file.sendFailed', 'Yuborib boʻlmadi. Qayta urinib koʻring.'));
    } finally {
      inFlight.current = false;
    }
  }, [kind, id, req]);

  const reset = useCallback(() => {
    if (inFlight.current) return;
    setState('idle');
  }, []);

  return { state, send, reset, busy: state === 'sending' };
}
