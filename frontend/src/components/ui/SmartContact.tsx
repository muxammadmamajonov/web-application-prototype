/* eslint-disable react-refresh/only-export-components -- UI-kit module: the
   SmartContact input is colocated with the validator re-exports its callers use.
   The rule only guards dev-time Fast Refresh. */
import { useEffect, useMemo, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { cn } from '@/lib/cn';
import { inputCls, inputErrCls } from './Modal';
import { formatUzPhone, isValidUzPhone, isValidUzMobile } from '@/lib/phone';
import { isValidEmail, emailSuggestions } from '@/lib/email';

interface Props {
  value: string;
  onChange: (v: string) => void;
  onMetaChange?: (m: { kind: 'phone' | 'email' | 'empty'; valid: boolean }) => void;
  required?: boolean;
  id?: string;
  label?: string;
  className?: string;
  /** (#284) Render the shared error state (red border + aria-invalid). */
  error?: boolean;
}

/**
 * Auto-detects whether the user is typing a phone or an email.
 *
 *  - Starts with a digit / "+"  → phone mode. Forces the +998 prefix and the UZ
 *    mask (max 9 national digits) via the shared {@link formatUzPhone}, and warns
 *    when the operator code isn't a recognised UZ mobile prefix.
 *  - Starts with a letter       → email mode. Suggests popular domains and offers
 *    a typo correction (gmial.com → gmail.com) via the shared email helpers.
 *
 *  Pasting / re-typing flips the mode automatically. All phone/email logic lives
 *  in lib/phone + lib/email — this component is just the UI around them.
 */
export function SmartContact({
  value, onChange, onMetaChange, required, id = 'contact', label, className, error,
}: Props) {
  const { t } = useTranslation();
  const [mode, setMode] = useState<'phone' | 'email' | 'empty'>(detectMode(value));
  const [showSuggest, setShowSuggest] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    const m = detectMode(value);
    setMode(m);
    onMetaChange?.({
      kind: m,
      valid: m === 'phone' ? isValidUzPhone(value) : m === 'email' ? isValidEmail(value) : false,
    });
  }, [value, onMetaChange]);

  const suggestions = useMemo(() => (mode === 'email' ? emailSuggestions(value) : []), [mode, value]);

  const handleChange = (raw: string) => {
    let next = raw;
    const detected = detectMode(next);
    if (detected === 'phone') next = formatUzPhone(next);
    else if (detected === 'email') next = next.replace(/\s+/g, '');
    setMode(detected);
    onChange(next);
    setShowSuggest(detected === 'email');
  };

  // The live status line under the field: green when valid, amber when the
  // number is complete but the operator code looks off, neutral hint otherwise.
  const status = (() => {
    if (mode === 'phone') {
      if (isValidUzMobile(value)) return { tone: 'text-[color:var(--c-success-strong)]', text: t('smartContact.phoneValid', 'Telefon raqami ✓') };
      if (isValidUzPhone(value)) return { tone: 'text-[color:var(--c-warn-strong)]', text: t('smartContact.phoneOperator', 'Operator kodini tekshiring') };
      return { tone: 'text-muted', text: t('smartContact.phoneHint', '+998 bilan boshlanadi · 9 ta raqam') };
    }
    if (mode === 'email') {
      return isValidEmail(value)
        ? { tone: 'text-[color:var(--c-success-strong)]', text: t('smartContact.emailValid', 'E-pochta ✓') }
        : { tone: 'text-muted', text: t('smartContact.emailHint', 'E-pochta manzili') };
    }
    return { tone: 'text-muted', text: t('smartContact.emptyHint', 'Telefon raqami yoki e-pochta manzili') };
  })();

  return (
    <label htmlFor={id} className={cn('flex flex-col gap-1.5 text-body font-medium', className)}>
      {label && (
        <span>
          {label} {required && <span className="text-accent">*</span>}
        </span>
      )}
      <div className="relative">
        <input
          ref={inputRef}
          id={id}
          required={required}
          inputMode={mode === 'phone' ? 'tel' : 'email'}
          autoComplete={mode === 'phone' ? 'tel' : 'email'}
          autoCapitalize="off"
          spellCheck={false}
          value={value}
          onChange={(e) => handleChange(e.target.value)}
          onFocus={() => setShowSuggest(mode === 'email')}
          onBlur={() => setTimeout(() => setShowSuggest(false), 150)}
          placeholder={t('smartContact.placeholder', '+998 90 123 45 67 yoki you@example.com')}
          aria-invalid={error ? 'true' : undefined}
          className={cn(inputCls, error && inputErrCls)}
        />
        {showSuggest && suggestions.length > 0 && (
          <ul className="absolute left-0 right-0 top-full z-20 mt-1 max-h-56 overflow-y-auto rounded-control border border-line bg-surface shadow-pop">
            {suggestions.map((s) => (
              <li key={s}>
                <button
                  type="button"
                  onMouseDown={(e) => { e.preventDefault(); onChange(s); setShowSuggest(false); }}
                  className="block w-full px-3 py-2 text-left text-body hover:bg-ink/5"
                >{s}</button>
              </li>
            ))}
          </ul>
        )}
      </div>
      <span className={cn('text-micro', status.tone)}>{status.text}</span>
    </label>
  );
}

function detectMode(value: string): 'phone' | 'email' | 'empty' {
  const v = value.trim();
  if (!v) return 'empty';
  if (/^[+\d]/.test(v)) return 'phone';
  return 'email';
}

// Re-exported so existing call sites (DemoPage / ContactPage) keep importing
// `isValidPhone` / `isValidEmail` from here — now backed by the single source of
// truth in lib/phone + lib/email rather than a private copy.
export function isValidPhone(v: string): boolean { return isValidUzPhone(v); }
export { isValidEmail };
