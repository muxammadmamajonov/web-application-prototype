/* eslint-disable react-refresh/only-export-components -- UI-kit module: input
   components colocated with their tightly-coupled formatters/validators (e.g.
   MoneyInput + formatMoney). The rule only guards dev-time Fast Refresh. */
import { useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react';
import { AtSign, Mail, Phone } from 'lucide-react';
import { cn } from '@/lib/cn';
import { inputCls } from './Modal';
import { formatUzPhone } from '@/lib/phone';
import { emailSuggestions } from '@/lib/email';
import { money } from '@/lib/format';

// ── AriaProps ────────────────────────────────────────────────────────────────
// (H20) `Field` (Modal.tsx) injects these via `cloneElement` so a validation
// error reaches the DOM — and a screen reader — as `aria-invalid` +
// `aria-errormessage` on the actual control, and names it via
// `aria-labelledby`/`aria-describedby`. A typed input that doesn't accept and
// forward them silently drops the injected attributes: the border still turns
// red, but nothing assistive tech reads announces the error.

interface AriaProps {
  'aria-labelledby'?: string;
  'aria-describedby'?: string;
  'aria-invalid'?: boolean | 'true' | 'false';
  'aria-errormessage'?: string;
}

// ── PhoneInput ───────────────────────────────────────────────────────────────
// Hard-locked +998 prefix, numeric keyboard, auto-spaced `+998 90 123 45 67`.
// Letters / symbols are stripped; the prefix can never be deleted.

interface PhoneProps extends AriaProps {
  value: string;
  onChange: (v: string) => void;
  id?: string;
  disabled?: boolean;
  className?: string;
  /** Show the leading phone glyph (default true). */
  icon?: boolean;
}

export function PhoneInput({ value, onChange, id = 'phone', disabled, className, icon = true, ...aria }: PhoneProps) {
  const handle = (raw: string) => onChange(formatUzPhone(raw));
  return (
    <div className="relative">
      {icon && <Phone size={16} className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-muted" />}
      <input
        id={id}
        type="tel"
        inputMode="numeric"
        autoComplete="tel"
        disabled={disabled}
        value={value}
        onChange={(e) => handle(e.target.value)}
        onFocus={(e) => { if (!e.target.value) onChange('+998 '); }}
        onKeyDown={(e) => {
          // Block deleting into the +998 prefix.
          const prefixLen = '+998 '.length;
          const caret = (e.target as HTMLInputElement).selectionStart ?? 0;
          if ((e.key === 'Backspace' && caret <= prefixLen) ||
              (e.key === 'Delete' && caret < prefixLen)) {
            e.preventDefault();
          }
        }}
        placeholder="+998 90 123 45 67"
        className={cn(inputCls, icon && 'pl-9', className)}
        {...aria}
      />
    </div>
  );
}

// ── EmailInput ───────────────────────────────────────────────────────────────
// Email keyboard + popular-domain autocomplete + typo correction. The domain
// list, validation and suggestion logic live in lib/email (shared 1:1 with
// SmartContact), so the two inputs can never drift apart again.

// Back-compat re-export for callers that imported nearestEmailDomain from here.
export { nearestEmailDomain } from '@/lib/email';

interface EmailProps extends AriaProps {
  value: string;
  onChange: (v: string) => void;
  id?: string;
  disabled?: boolean;
  className?: string;
  placeholder?: string;
  icon?: boolean;
}

export function EmailInput({ value, onChange, id = 'email', disabled, className, placeholder = 'salom@gmail.com', icon = true, ...aria }: EmailProps) {
  const [show, setShow] = useState(false);
  const ref = useRef<HTMLInputElement>(null);

  const suggestions = useMemo(() => emailSuggestions(value), [value]);

  return (
    <div className="relative">
      {icon && <Mail size={16} className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-muted" />}
      <input
        ref={ref}
        id={id}
        type="email"
        inputMode="email"
        autoComplete="email"
        autoCapitalize="off"
        spellCheck={false}
        disabled={disabled}
        value={value}
        onChange={(e) => { onChange(e.target.value.replace(/\s+/g, '')); setShow(true); }}
        onFocus={() => setShow(true)}
        onBlur={() => setTimeout(() => setShow(false), 150)}
        placeholder={placeholder}
        className={cn(inputCls, icon && 'pl-9', className)}
        {...aria}
      />
      {show && suggestions.length > 0 && (
        <ul className="absolute left-0 right-0 top-full z-30 mt-1 max-h-56 overflow-y-auto rounded-control border border-line bg-surface shadow-pop">
          {suggestions.map((s) => (
            <li key={s}>
              <button
                type="button"
                onMouseDown={(e) => { e.preventDefault(); onChange(s); setShow(false); }}
                className="block w-full px-3 py-2 text-left text-body hover:bg-ink/5"
              >{s}</button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

// ── MoneyInput ────────────────────────────────────────────────────────────────
// Numeric amount field that renders grouped thousands ("1 000 000") as you type,
// strips non-digits, and hard-caps the value (default 999,999,999 — the largest
// realistic single-deal sum in UZS, ~$80k). Emits a plain number.

export const MONEY_MAX = 999_999_999;

/** Grouping delegates to the one canonical formatter (`lib/format#money`) so
 *  this and the rest of the app never drift apart on thousands-separator or
 *  fractional-amount handling (M-18). ZERO IS EMPTY stays local to this
 *  function — `money(0)` renders "0", but the input needs '' so a deleted
 *  last digit leaves a genuinely empty field (see MoneyInput above). */
export function formatMoney(n: number): string {
  return n ? money(n) : '';
}

interface MoneyProps extends AriaProps {
  value: number;
  onChange: (n: number) => void;
  max?: number;
  id?: string;
  placeholder?: string;
  className?: string;
  disabled?: boolean;
  /** For a field whose label is not adjacent (e.g. the pay screen's amount card). */
  'aria-label'?: string;
  /** Opt out of the default input chrome — the caller supplies all of it. */
  unstyled?: boolean;
}

/**
 * The one money field in the product. Two behaviours are load-bearing:
 *
 * ZERO IS EMPTY — `formatMoney(0)` renders '', so deleting the last digit
 * leaves a genuinely empty field and the next keystroke starts from that digit
 * instead of appending to a stubborn "0".
 *
 * THE CARET SURVIVES REGROUPING — the display is regrouped on every keystroke
 * and a controlled re-render drops the caret to the end, which scrambles any
 * edit made mid-number. It is restored by DIGIT offset, not character offset:
 * count the digits before the caret, then put it after the same digit once the
 * separators have moved.
 *
 * Everything non-numeric is stripped, so "1 000 000", "1,000,000" and
 * "1000000 UZS" all paste as 1000000.
 */
export function MoneyInput({
  value, onChange, max = MONEY_MAX, id, placeholder, className, disabled,
  'aria-label': ariaLabel, unstyled, ...aria
}: MoneyProps) {
  const ref = useRef<HTMLInputElement>(null);
  // Set during onChange, applied after React has painted the new value. Null
  // whenever the value changed from OUTSIDE (a quick-pick chip), so those never
  // yank the caret.
  const caret = useRef<number | null>(null);

  useLayoutEffect(() => {
    const el = ref.current;
    if (!el || caret.current === null) return;
    const pos = caret.current;
    caret.current = null;
    el.setSelectionRange(pos, pos);
  });

  const handle = (e: React.ChangeEvent<HTMLInputElement>) => {
    const raw = e.target.value;
    const selectionEnd = e.target.selectionStart ?? raw.length;
    const digitsBefore = raw.slice(0, selectionEnd).replace(/\D/g, '').length;

    const digits = raw.replace(/\D/g, '').slice(0, 12);
    let n = digits ? Number(digits) : 0;
    if (n > max) n = max;

    const next = formatMoney(n);
    let seen = 0;
    let pos = digitsBefore === 0 ? 0 : next.length;
    for (let i = 0; i < next.length && digitsBefore > 0; i++) {
      if (next[i] >= '0' && next[i] <= '9') {
        seen += 1;
        if (seen === digitsBefore) { pos = i + 1; break; }
      }
    }
    caret.current = pos;
    onChange(n);
  };

  return (
    <input
      ref={ref}
      id={id}
      type="text"
      inputMode="numeric"
      aria-label={ariaLabel}
      disabled={disabled}
      value={formatMoney(value)}
      onChange={handle}
      placeholder={placeholder}
      className={cn(unstyled ? 'tabular-nums' : [inputCls, 'tabular-nums'], className)}
      {...aria}
    />
  );
}

// ── PassportInput ─────────────────────────────────────────────────────────────
// One field for an Uzbek passport: 2 letters + 7 digits ("AA1234567"). Letters
// come first (auto-uppercased); once two letters are in, only digits follow,
// capped at 7. We store the combined string and split for the API.

function sanitizePassport(raw: string): string {
  const up = raw.toUpperCase().replace(/[^A-Z0-9]/g, '');
  let out = '';
  let i = 0;
  while (i < up.length && out.length < 2 && up[i] >= 'A' && up[i] <= 'Z') { out += up[i]; i++; }
  // Digits are only valid AFTER the 2-letter series — ignore any typed digits
  // until both letters are present (so a number-first entry does nothing).
  if (out.length < 2) return out;
  let digits = '';
  for (; i < up.length && digits.length < 7; i++) {
    if (up[i] >= '0' && up[i] <= '9') digits += up[i];
  }
  return out + digits;
}
export function joinPassport(series?: string | null, number?: string | null): string {
  return sanitizePassport(`${series ?? ''}${number ?? ''}`);
}

export function PassportInput({ value, onChange, id = 'passport', className, ...aria }: AriaProps & {
  value: string; onChange: (v: string) => void; id?: string; className?: string;
}) {
  return (
    <input
      id={id}
      type="text"
      // Letters first → text keyboard; once the 2-letter series is in, switch the
      // mobile keyboard to numeric for the 7 digits.
      inputMode={value.length >= 2 ? 'numeric' : 'text'}
      autoCapitalize="characters"
      autoCorrect="off"
      spellCheck={false}
      maxLength={9}
      value={value}
      onChange={(e) => onChange(sanitizePassport(e.target.value))}
      placeholder="AA1234567"
      className={cn(inputCls, 'font-mono uppercase tracking-wider', className)}
      {...aria}
    />
  );
}

// ── TelegramInput ─────────────────────────────────────────────────────────────
// Fixed, non-editable "@" prefix so users never type "@" twice or paste a full
// https://t.me/… link. We strip "@", URL noise and whitespace, keeping only the
// bare username. The stored value is the username WITHOUT the leading "@".

/** Normalise any pasted telegram reference to a bare username. */
function normalizeTgUsername(raw: string): string {
  return raw
    .trim()
    .replace(/^https?:\/\//i, '')
    .replace(/^(t\.me|telegram\.me|telegram\.dog)\//i, '')
    .replace(/^@+/, '')
    .replace(/[^A-Za-z0-9_]/g, '');
}

interface TgProps extends AriaProps {
  value: string;
  onChange: (v: string) => void;
  id?: string;
  disabled?: boolean;
  className?: string;
  placeholder?: string;
}

export function TelegramInput({ value, onChange, id = 'telegram', disabled, className, placeholder = 'username', ...aria }: TgProps) {
  return (
    <div className={cn('flex items-stretch overflow-hidden rounded-control border border-line bg-cream focus-within:border-ink/30 focus-within:bg-white', className)}>
      <span className="flex items-center gap-1 border-r border-line bg-ink/5 px-3 text-subsection font-medium text-ink-soft">
        <AtSign size={16} className="text-muted" />
      </span>
      <input
        id={id}
        type="text"
        inputMode="text"
        autoCapitalize="off"
        autoCorrect="off"
        spellCheck={false}
        disabled={disabled}
        value={value}
        onChange={(e) => onChange(normalizeTgUsername(e.target.value))}
        placeholder={placeholder}
        className="h-11 min-w-0 flex-1 bg-transparent px-3 text-subsection placeholder:text-muted focus:outline-none"
        {...aria}
      />
    </div>
  );
}

// ── Textarea ──────────────────────────────────────────────────────────────────
// Multi-line input with an optional character counter + hard limit, and optional
// autosize. The counter turns amber near the limit and red at it; `maxLength`
// hard-caps input so the value can never exceed it (no silent server rejection).

const TEXTAREA_CLS =
  'w-full rounded-control border border-line bg-cream px-3 py-2.5 text-subsection leading-relaxed placeholder:text-muted focus:border-ink/30 focus:outline-none focus:ring-2 focus:ring-ink/20 focus:bg-white';

interface TextareaProps {
  value: string;
  onChange: (v: string) => void;
  /** Hard character cap + show an "N / max" counter. Omit for no limit/counter. */
  maxLength?: number;
  rows?: number;
  id?: string;
  placeholder?: string;
  className?: string;
  disabled?: boolean;
  /** Grow to fit content instead of scrolling. */
  autosize?: boolean;
  'aria-describedby'?: string;
  'aria-invalid'?: boolean | 'true' | 'false';
  'aria-errormessage'?: string;
}

export function Textarea({
  value, onChange, maxLength, rows = 4, id, placeholder, className, disabled, autosize, ...aria
}: TextareaProps) {
  const ref = useRef<HTMLTextAreaElement>(null);
  useEffect(() => {
    if (!autosize || !ref.current) return;
    ref.current.style.height = 'auto';
    ref.current.style.height = `${ref.current.scrollHeight}px`;
  }, [value, autosize]);

  const len = [...value].length; // count code points, so emoji count as 1
  const near = maxLength != null && len >= Math.floor(maxLength * 0.9);
  const over = maxLength != null && len > maxLength;

  return (
    <div>
      <textarea
        ref={ref}
        id={id}
        rows={rows}
        disabled={disabled}
        value={value}
        placeholder={placeholder}
        onChange={(e) => {
          const next = e.target.value;
          onChange(maxLength != null ? [...next].slice(0, maxLength).join('') : next);
        }}
        className={cn(TEXTAREA_CLS, autosize ? 'resize-none overflow-hidden' : 'resize-y', className)}
        {...aria}
      />
      {maxLength != null && (
        <div className={cn('mt-1 text-right text-micro tabular-nums', over ? 'text-danger' : near ? 'text-[color:var(--c-warn-strong)]' : 'text-muted')}>
          {len} / {maxLength}
        </div>
      )}
    </div>
  );
}

// ── PercentInput ──────────────────────────────────────────────────────────────
// Numeric 0–max (default 100) with a fixed "%" suffix. Strips non-numerics,
// clamps to range, never goes negative. Emits a plain number (0 = empty).

export function PercentInput({
  value, onChange, max = 100, id, placeholder, className, disabled, ...aria
}: AriaProps & {
  value: number; onChange: (n: number) => void; max?: number;
  id?: string; placeholder?: string; className?: string; disabled?: boolean;
}) {
  return (
    <div className="relative">
      <input
        id={id}
        type="text"
        inputMode="decimal"
        disabled={disabled}
        value={value ? String(value) : ''}
        onChange={(e) => {
          const cleaned = e.target.value.replace(/[^\d.]/g, '');
          let n = cleaned ? Number(cleaned) : 0;
          if (Number.isNaN(n)) n = 0;
          if (n < 0) n = 0;
          if (n > max) n = max;
          onChange(n);
        }}
        placeholder={placeholder ?? '0'}
        className={cn(inputCls, 'pr-9 tabular-nums', className)}
        {...aria}
      />
      <span className="pointer-events-none absolute right-3 top-1/2 -translate-y-1/2 text-muted">%</span>
    </div>
  );
}
