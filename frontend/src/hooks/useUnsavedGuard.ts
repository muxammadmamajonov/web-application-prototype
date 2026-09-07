import { useEffect, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { confirmDialog } from '@/lib/feedback';

/**
 * (#532) Unsaved-changes guard for modals/forms.
 *
 * Pass the current `open` flag and a JSON `draftSig` of the editable draft. The
 * returned `requestClose` prompts a discard confirmation when the draft differs
 * from the baseline captured just AFTER open (i.e. after reset/prefill effects
 * apply), and closes immediately when the form is untouched.
 *
 * Extracted from InstallmentModal so CustomerModal / ProductModal / TopUpModal
 * can share one implementation.
 */
export function useUnsavedGuard(open: boolean, draftSig: string, onClose: () => void): () => void {
  const { t } = useTranslation();
  const baselineSig = useRef('');
  const [armBaseline, setArmBaseline] = useState(0);
  useEffect(() => { if (open) setArmBaseline((n) => n + 1); }, [open]);
  // Capture the baseline on the render AFTER reset/prefill have applied.
  // eslint-disable-next-line react-hooks/exhaustive-deps
  useEffect(() => { if (open && armBaseline) baselineSig.current = draftSig; }, [armBaseline]);

  return () => {
    void (async () => {
      if (open && baselineSig.current && draftSig !== baselineSig.current) {
        const ok = await confirmDialog({
          title: t('common.discardTitle', { defaultValue: "Saqlanmagan o'zgarishlar" }),
          message: t('common.discardMsg', { defaultValue: "Kiritilgan ma'lumotlar saqlanmaydi. Yopilsinmi?" }),
          confirmLabel: t('common.discardClose', { defaultValue: 'Yopish' }),
          cancelLabel: t('common.keepEditing', { defaultValue: 'Davom ettirish' }),
          danger: true,
        });
        if (!ok) return;
      }
      onClose();
    })();
  };
}
