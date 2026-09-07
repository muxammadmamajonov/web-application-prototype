import { useEffect, useMemo, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { CornerDownLeft, Search, type LucideIcon } from 'lucide-react';
import { cn } from '@/lib/cn';
import { useFocusTrap } from '@/hooks/useFocusTrap';

/** A single entry in the command palette — a page to jump to or an action to run. */
export interface Command {
  key: string;
  label: string;
  /** Right-aligned group hint (e.g. the sidebar section). */
  hint?: string;
  icon?: LucideIcon;
  /** Extra match terms not shown (e.g. an English alias on a Uzbek label). */
  keywords?: string;
  perform: () => void;
}

/**
 * Cmd/Ctrl+K command palette — fuzzy-search every page (and a few global actions)
 * and jump there. Keyboard-first: ↑/↓ move, Enter runs, Esc closes. Opens over
 * any dashboard screen; the trigger lives here so no page has to wire it.
 */
export function CommandPalette({ commands }: { commands: Command[] }) {
  const { t } = useTranslation();
  const [open, setOpen] = useState(false);
  const [q, setQ] = useState('');
  const [active, setActive] = useState(0);
  const inputRef = useRef<HTMLInputElement>(null);
  const panelRef = useRef<HTMLDivElement>(null);
  const listRef = useRef<HTMLUListElement>(null);

  // Cmd/Ctrl+K toggles from anywhere.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'k') {
        e.preventDefault();
        setOpen((o) => !o);
      }
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, []);

  // On open: clear, focus the input, lock body scroll.
  useEffect(() => {
    if (!open) return;
    setQ('');
    setActive(0);
    const id = window.setTimeout(() => inputRef.current?.focus(), 0);
    const prev = document.body.style.overflow;
    document.body.style.overflow = 'hidden';
    return () => { window.clearTimeout(id); document.body.style.overflow = prev; };
  }, [open]);

  const results = useMemo(() => {
    const needle = q.trim().toLowerCase();
    if (!needle) return commands;
    return commands.filter((c) =>
      c.label.toLowerCase().includes(needle)
      || (c.keywords ?? '').toLowerCase().includes(needle)
      || (c.hint ?? '').toLowerCase().includes(needle));
  }, [q, commands]);

  // Keep the highlight in range as results shrink; scroll it into view.
  useEffect(() => {
    setActive((a) => Math.min(a, Math.max(0, results.length - 1)));
  }, [results.length]);
  useEffect(() => {
    listRef.current?.querySelector('[data-active="true"]')?.scrollIntoView({ block: 'nearest' });
  }, [active]);

  useFocusTrap(panelRef, open, () => setOpen(false));

  if (!open) return null;

  const run = (c?: Command) => { if (!c) return; setOpen(false); c.perform(); };

  const onKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'ArrowDown') { e.preventDefault(); setActive((a) => Math.min(a + 1, results.length - 1)); }
    else if (e.key === 'ArrowUp') { e.preventDefault(); setActive((a) => Math.max(a - 1, 0)); }
    else if (e.key === 'Enter') { e.preventDefault(); run(results[active]); }
    else if (e.key === 'Escape') { e.preventDefault(); setOpen(false); }
  };

  return (
    // The shared trap keeps focus inside while open and restores it on close,
    // so Tab cannot walk into the page behind and a keyboard user returns to
    // where they were.
    <div
      ref={panelRef}
      className="fixed inset-0 z-[90] flex items-start justify-center p-4 pt-[12vh]"
      role="dialog"
      aria-modal="true"
      aria-label={t('cmd.title', 'Buyruqlar paneli')}
    >
      <button
        className="absolute inset-0 bg-ink/40 backdrop-blur-sm"
        aria-label={t('common.close', 'Yopish')}
        onClick={() => setOpen(false)}
      />
      <div className="relative w-full max-w-lg overflow-hidden rounded-card border border-line bg-surface shadow-pop">
        <div className="flex items-center gap-2.5 border-b border-line px-4">
          <Search size={16} className="shrink-0 text-muted" />
          <input
            ref={inputRef}
            value={q}
            onChange={(e) => { setQ(e.target.value); setActive(0); }}
            onKeyDown={onKeyDown}
            placeholder={t('cmd.placeholder', 'Sahifa qidiring yoki oʻting…')}
            aria-label={t('cmd.placeholder', 'Sahifa qidiring yoki oʻting…')}
            className="h-12 w-full min-w-0 bg-transparent text-subsection outline-none placeholder:text-muted"
          />
          <kbd className="hidden shrink-0 rounded-micro border border-line px-1.5 py-0.5 text-micro text-muted sm:block">ESC</kbd>
        </div>
        <ul ref={listRef} className="max-h-[58vh] overflow-y-auto p-1.5">
          {results.length === 0 && (
            <li className="px-3 py-6 text-center text-small text-muted">{t('cmd.noResults', 'Hech narsa topilmadi')}</li>
          )}
          {results.map((c, i) => {
            const Icon = c.icon;
            const isActive = i === active;
            return (
              <li key={c.key}>
                <button
                  type="button"
                  data-active={isActive}
                  onMouseEnter={() => setActive(i)}
                  onClick={() => run(c)}
                  className={cn('flex w-full items-center gap-3 rounded-control px-3 py-2.5 text-left transition', isActive ? 'bg-ink/5' : '')}
                >
                  {Icon && <Icon size={16} className="shrink-0 text-ink-soft" />}
                  <span className="min-w-0 flex-1 truncate text-body">{c.label}</span>
                  {c.hint && <span className="shrink-0 text-micro text-muted">{c.hint}</span>}
                  {isActive && <CornerDownLeft size={14} className="shrink-0 text-muted" />}
                </button>
              </li>
            );
          })}
        </ul>
      </div>
    </div>
  );
}
