import { useState } from 'react';

/** Chip editor for a list of numeric tokens (#492). Each value is a discrete,
 *  removable chip; the draft input strips non-numeric characters and de-dupes
 *  on commit (Enter / comma / blur), so a stray letter or duplicate can never
 *  silently drop or corrupt an entry the way a free-text comma list could.
 *  Extracted from CatalogPage's variant-axis editor for reuse (reminder
 *  day-offsets in platform Settings). */
export function NumberChips({ values, onChange, placeholder, unit }: {
  values: string[]; onChange: (vals: string[]) => void; placeholder: string; unit: string;
}) {
  const [draft, setDraft] = useState('');
  const commit = () => {
    const v = draft.replace(/[^0-9.]/g, '').trim();
    if (v && !values.includes(v)) onChange([...values, v]);
    setDraft('');
  };
  return (
    <div className="flex flex-wrap items-center gap-1.5">
      {values.map((v) => (
        <span key={v} className="inline-flex items-center gap-1 rounded-pill border border-ink bg-ink px-2.5 py-1 text-caption text-white">
          {v}{unit ? ` ${unit}` : ''}
          <button type="button" onClick={() => onChange(values.filter((x) => x !== v))} className="text-white/70 hover:text-white">×</button>
        </span>
      ))}
      <input className="min-w-[80px] flex-1 rounded-pill border border-dashed border-line bg-surface px-3 py-1 text-caption outline-none focus:border-accent"
             value={draft} inputMode="numeric" onChange={(e) => setDraft(e.target.value)}
             onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ',') { e.preventDefault(); commit(); } }}
             onBlur={commit} placeholder={placeholder} />
    </div>
  );
}
