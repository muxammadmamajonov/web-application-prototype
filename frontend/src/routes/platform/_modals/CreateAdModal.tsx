import { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Loader2 } from 'lucide-react';
import { Modal, Field, inputCls } from '@/components/ui/Modal';
import { Select } from '@/components/ui/Select';
import { Button } from '@/components/ui/Button';
import { MoneyInput } from '@/components/ui/FormFields';
import { focusFirstInvalid, hasErrors } from '@/lib/forms';
import { useCreateAd, useUpdateAd } from '@/lib/mutations';
import type { AdRow } from '@/lib/queries';

interface Props { open: boolean; onClose: () => void; editing?: AdRow | null }

const CHANNELS = ['telegram', 'instagram', 'facebook', 'youtube', 'google', 'offline'] as const;
const STATUSES = ['draft', 'active', 'paused', 'ended'] as const;

export function CreateAdModal({ open, onClose, editing }: Props) {
  const { t } = useTranslation();
  const isEdit = !!editing;

  const [name, setName]       = useState('');
  const [channel, setChannel] = useState<string>('telegram');
  const [stat, setStat]       = useState<string>('draft');
  const [budget, setBudget]   = useState(0);
  const [spent, setSpent]     = useState(0);
  const [notes, setNotes]     = useState('');

  // Inline validation errors
  const [nameErr, setNameErr] = useState<string | null>(null);

  const create = useCreateAd();
  const update = useUpdateAd();
  const m = isEdit ? update : create;

  // Populate fields when editing or reset when creating.
  useEffect(() => {
    if (editing) {
      setName(editing.name);
      setChannel(editing.channel);
      setStat(editing.status);
      setBudget(editing.budget);
      setSpent(editing.spent);
      setNotes(editing.notes ?? '');
    } else {
      setName(''); setChannel('telegram'); setStat('draft');
      setBudget(0); setSpent(0); setNotes('');
    }
    setNameErr(null);
  }, [editing, open]);

  const reset = () => {
    setName(''); setChannel('telegram'); setStat('draft');
    setBudget(0); setSpent(0); setNotes(''); setNameErr(null);
  };
  const close = () => { reset(); onClose(); };

  const validateName = (v: string): string | null =>
    v.trim().length === 0
      ? t('platform.createAd.nameRequired', 'Kampaniya nomini kiriting')
      : v.trim().length < 2
        ? t('platform.createAd.nameTooShort', 'Kamida 2 ta belgi kiriting')
        : null;

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    const nErr = validateName(name);
    setNameErr(nErr);
    if (hasErrors({ name: nErr })) { focusFirstInvalid(); return; }

    const body = {
      name: name.trim(),
      channel,
      status: stat,
      budget,
      spent,
      notes: notes.trim() || null,
    };
    if (isEdit && editing) {
      await update.mutateAsync({ id: editing.id, body });
    } else {
      await create.mutateAsync(body);
    }
    close();
  };

  const channelOptions = CHANNELS.map((c) => ({
    value: c,
    label: t(`platformAds.channels.${c}`, c),
  }));

  const statusOptions = STATUSES.map((s) => ({
    value: s,
    label: t(`platformAds.status.${s}`, s),
  }));

  const currency = t('billing.currency');

  return (
    <Modal
      open={open}
      onClose={close}
      title={isEdit ? t('platform.createAd.editTitle') : t('platform.createAd.newTitle')}
      subtitle={isEdit
        ? t('platform.createAd.editSubtitle', "Kampaniya ma'lumotlarini yangilang")
        : t('platform.createAd.newSubtitle', 'Yangi reklama kampaniyasi yarating')}
      footer={
        <>
          <Button variant="ghost" type="button" onClick={close}>
            {t('common.cancel')}
          </Button>
          <Button
            variant="primary"
            type="submit"
            form="create-ad-form"
            disabled={m.isPending || name.trim().length < 2}
          >
            {m.isPending
              ? (<><Loader2 size={16} className="animate-spin" /> {t('platform.createAd.saving')}</>)
              : isEdit ? t('common.save') : t('platform.createAd.create')}
          </Button>
        </>
      }
    >
      <form id="create-ad-form" onSubmit={submit} className="flex flex-col gap-4">
        {/* Name — required, inline validation */}
        <Field label={t('platform.createAd.name')} required error={nameErr}>
          <input
            required
            minLength={2}
            maxLength={120}
            value={name}
            onChange={(e) => { setName(e.target.value); if (nameErr) setNameErr(validateName(e.target.value)); }}
            onBlur={(e) => setNameErr(validateName(e.target.value))}
            className={inputCls}
            placeholder={t('platform.createAd.namePh', 'Bahorgi Telegram kampaniyasi')}
          />
        </Field>

        {/* Channel + Status side by side */}
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
          <Field label={t('platform.createAd.channel')} required>
            <Select value={channel} onChange={setChannel} options={channelOptions} />
          </Field>
          <Field label={t('platform.createAd.status')}>
            <Select value={stat} onChange={setStat} options={statusOptions} />
          </Field>
        </div>

        {/* Budget + Spent — MoneyInput (never raw number inputs) */}
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
          <Field label={t('platform.createAd.budget')} hint={currency}>
            <MoneyInput value={budget} onChange={setBudget} />
          </Field>
          <Field label={t('platform.createAd.spent')} hint={currency}>
            <MoneyInput value={spent} onChange={setSpent} />
          </Field>
        </div>

        {/* Notes — optional free text */}
        <Field label={t('platform.createAd.notes')} hint={t('platform.createTenant.optional')}>
          <input
            value={notes}
            onChange={(e) => setNotes(e.target.value)}
            maxLength={500}
            className={inputCls}
            placeholder={t('platform.createAd.notesPh', "Qo'shimcha izoh (ixtiyoriy)")}
          />
        </Field>

        {/* Mutation error banner */}
        {m.isError && (
          <div className="rounded-xl border border-danger/30 bg-[color:var(--c-danger-soft)] px-4 py-3 text-sm text-danger">
            {t('platform.createAd.saveFailed', "Saqlab bo'lmadi.")} · {(m.error as Error)?.message}
          </div>
        )}
      </form>
    </Modal>
  );
}
