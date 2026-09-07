import { useEffect, useRef, type KeyboardEvent, type Ref } from 'react';
import { Search } from 'lucide-react';
import { cn } from '@/lib/cn';

/** Wiring for a search field that DRIVES a highlighted result list.
 *  Supplying it turns the input into a WAI-ARIA combobox so a screen reader can
 *  announce the arrow-highlighted option, and hands Enter to the owner. */
export interface ComboboxWiring {
  /** id of the `role="listbox"` element this field controls. */
  listId: string;
  expanded: boolean;
  /** id of the currently highlighted option, if any. */
  activeId?: string;
}

export function SearchInput({
  placeholder, value, onChange, className,
  onDebouncedChange, debounceMs = 250, minLength = 2,
  onKeyDown, combobox, inputRef,
}: {
  placeholder?: string;
  value?: string;
  /** Fires immediately on every keystroke — wire this to the controlled value
   *  so the field stays responsive while typing. */
  onChange?: (v: string) => void;
  className?: string;
  /** Opt-in debounced callback: fires the trimmed query after `debounceMs` of
   *  idle, but only once it reaches `minLength` characters (a 1-char query is
   *  ignored). Clearing or dropping below `minLength` emits '' so results reset.
   *  Pages that pass this can drop their own setTimeout debounce; pages that
   *  don't are unaffected (no double-debounce). */
  onDebouncedChange?: (v: string) => void;
  /** Idle delay before `onDebouncedChange` fires. Default 250ms. */
  debounceMs?: number;
  /** Minimum trimmed length before `onDebouncedChange` emits a query. Default 2. */
  minLength?: number;
  /** Arrow/Enter handling, owned by the caller that renders the list. */
  onKeyDown?: (e: KeyboardEvent<HTMLInputElement>) => void;
  combobox?: ComboboxWiring;
  inputRef?: Ref<HTMLInputElement>;
}) {
  // Track the last debounced value we emitted so we don't fire duplicate
  // callbacks (e.g. typing then deleting back to the same short query).
  const lastEmitted = useRef<string | null>(null);

  useEffect(() => {
    if (!onDebouncedChange) return;
    const trimmed = (value ?? '').trim();
    const next = trimmed.length >= minLength ? trimmed : '';
    const id = setTimeout(() => {
      if (lastEmitted.current !== next) {
        lastEmitted.current = next;
        onDebouncedChange(next);
      }
    }, debounceMs);
    return () => clearTimeout(id);
    // onDebouncedChange is assumed stable (or the caller accepts re-scheduling);
    // we intentionally key only on the inputs that change the query.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [value, debounceMs, minLength]);

  return (
    <label className={cn(
      // text-subsection (16px) on the input prevents iOS Safari's focus-zoom; the
      // viewport (index.html) intentionally allows pinch-zoom, so we can't rely
      // on maximum-scale to suppress it.
      // min-w-0 lets the field give room back to a fixed-width sibling (the
      // filter trigger) inside FilterBar instead of forcing the row to overflow.
      'flex h-10 w-full min-w-0 max-w-panel items-center gap-2 rounded-control border border-line bg-surface px-3 text-subsection',
      'focus-within:border-ink/25 focus-within:bg-white',
      className,
    )}>
      <Search size={16} className="text-muted shrink-0" />
      <input
        ref={inputRef}
        className="min-w-0 flex-1 bg-transparent outline-none placeholder:text-muted"
        aria-label={placeholder ?? 'Search…'}
        placeholder={placeholder ?? 'Search…'}
        value={value}
        onChange={(e) => onChange?.(e.target.value)}
        onKeyDown={onKeyDown}
        role={combobox ? 'combobox' : undefined}
        aria-autocomplete={combobox ? 'list' : undefined}
        aria-expanded={combobox ? combobox.expanded : undefined}
        aria-controls={combobox?.listId}
        aria-activedescendant={combobox?.activeId}
      />
    </label>
  );
}
