import { memo, useEffect, useMemo, useState, type ReactNode } from 'react';
import { useTranslation } from 'react-i18next';
import { ChevronDown, ChevronLeft, ChevronRight, ChevronsUpDown, ChevronUp } from 'lucide-react';
import { cn } from '@/lib/cn';
import { SkeletonList } from './Skeleton';
import { ErrorCard } from './ErrorCard';
import { EmptyState } from './EmptyState';

export interface Column<T> {
  key: string;
  label: string;
  width?: string;
  render: (row: T) => ReactNode;
  align?: 'left' | 'right';
  /** Provide a comparable value to make this column sortable (click the header). */
  sortValue?: (row: T) => string | number;
  /** (#293) Opt out of single-line ellipsis truncation for this column (e.g. a
   *  column whose value should wrap). Defaults to true for left-aligned text. */
  truncate?: boolean;
}

interface Props<T> {
  columns: Column<T>[];
  rows: T[] | undefined;
  isLoading?: boolean;
  isError?: boolean;
  /**
   * The failure itself, not just `isError` — the shared card classifies it to
   * say which kind of problem this is and whether retrying can help. Offline, a
   * 500, a 403 and a 422 must not all render the same message.
   */
  error?: unknown;
  emptyTitle?: string;
  emptyBody?: string;
  rowKey?: (row: T) => string;
  /** Rows per page; pagination controls appear only when rows exceed it. */
  pageSize?: number;
  /** Optional retry callback — renders a "try again" button on the error state. */
  onRetry?: () => void;
  /** Make rows clickable (e.g. open a detail view). Adds hover + keyboard support. */
  onRowClick?: (row: T) => void;
}

/** Compact, mobile-friendly admin list. Headers collapse on phones; rows
 *  become cards stacking column label + value. */
function DataTableInner<T>({
  columns, rows, isLoading, isError, error, emptyTitle,
  emptyBody, rowKey, pageSize = 25, onRetry, onRowClick,
}: Props<T>) {
  const { t } = useTranslation();
  const gridTemplate = columns.map((c) => c.width ?? '1fr').join(' ');
  const [page, setPage] = useState(0);
  const [sort, setSort] = useState<{ key: string; dir: 'asc' | 'desc' } | null>(null);

  const sorted = useMemo(() => {
    if (!rows || !sort) return rows ?? [];
    const col = columns.find((c) => c.key === sort.key);
    if (!col?.sortValue) return rows;
    const acc = col.sortValue;
    const dir = sort.dir === 'asc' ? 1 : -1;
    return [...rows].sort((a, b) => {
      const va = acc(a); const vb = acc(b);
      if (va < vb) return -1 * dir;
      if (va > vb) return 1 * dir;
      return 0;
    });
  }, [rows, sort, columns]);

  const total = sorted.length;
  const pageCount = Math.max(1, Math.ceil(total / pageSize));
  // Clamp when the dataset shrinks (e.g. after a delete) or filters change.
  useEffect(() => { if (page > pageCount - 1) setPage(0); }, [pageCount, page]);

  const toggleSort = (key: string) =>
    setSort((s) => s?.key === key ? (s.dir === 'asc' ? { key, dir: 'desc' } : null) : { key, dir: 'asc' });

  if (isLoading) {
    return <SkeletonList rows={Math.min(pageSize, 8)} />;
  }
  if (isError) {
    return <ErrorCard error={error} onRetry={onRetry} />;
  }
  if (!rows || rows.length === 0) {
    // The canonical primitive, not a second hand-rolled card: every list in the
    // product renders through this component, so an empty state written here
    // would be the app's most-seen deviation from the shared one.
    return (
      <EmptyState
        title={emptyTitle ?? t('common.emptyTitle', "Hozircha bo'sh")}
        body={emptyBody ?? t('common.emptyBody', "Birinchi yozuv kelganda shu yerda paydo bo'ladi.")}
      />
    );
  }

  const start = page * pageSize;
  const pageRows = total > pageSize ? sorted.slice(start, start + pageSize) : sorted;

  return (
    /*
     * `grid`, not `table`, because rows ARE interactive: ARIA permits focusable
     * rows in a grid, which is the "click the row to open the record" pattern
     * every list here uses.
     *
     * The hierarchy must stay complete — rowgroup → row → columnheader/gridcell
     * — and any layout wrapper between a row and its cells must be marked
     * presentational, or the parent/child relationship breaks and a reader
     * announces a table with nothing inside it. The same DOM serves the mobile
     * card view, so this holds at both breakpoints.
     */
    <div role="grid" className="space-y-2.5 p-0 md:card md:space-y-0 md:overflow-hidden">
      {/* Sort state belongs on the header cell's `aria-sort`, not in an
          aria-label on the control — an aria-label would replace the column
          name a screen-reader user needs with a sentence about sorting. */}
      <div role="rowgroup" className="contents">
      <div
        role="row"
        className="hidden gap-4 border-b border-line px-5 py-3 text-micro uppercase tracking-wider text-muted md:grid"
        style={{ gridTemplateColumns: gridTemplate }}
      >
        {columns.map((c) => (
          c.sortValue ? (
            <div
              key={c.key}
              role="columnheader"
              aria-sort={sort?.key === c.key ? (sort.dir === 'asc' ? 'ascending' : 'descending') : 'none'}
              className={cn(c.align === 'right' && 'text-right')}
            >
              <button
                onClick={() => toggleSort(c.key)}
                className={cn('inline-flex items-center gap-1 uppercase tracking-wider hover:text-ink', c.align === 'right' && 'justify-end')}
              >
                {c.label}
                {sort?.key === c.key
                  ? (sort.dir === 'asc' ? <ChevronUp size={12} aria-hidden="true" /> : <ChevronDown size={12} aria-hidden="true" />)
                  : <ChevronsUpDown size={12} className="opacity-40" aria-hidden="true" />}
              </button>
            </div>
          ) : (
            <div key={c.key} role="columnheader" aria-sort="none"
                 className={cn(c.align === 'right' && 'text-right')}>{c.label}</div>
          )
        ))}
      </div>
      </div>
      <ul role="rowgroup" className="space-y-2.5 md:space-y-0">
        {pageRows.map((r, i) => (
          <li
            key={rowKey?.(r) ?? i}
            role="row"
            {...(onRowClick ? {
              // A focusable row is the grid pattern for "activate this record".
              // `role="button"` here would have replaced the row in the
              // hierarchy, orphaning its cells.
              tabIndex: 0,
              onClick: () => onRowClick(r),
              onKeyDown: (e: React.KeyboardEvent) => {
                if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); onRowClick(r); }
              },
            } : {})}
            className={cn(
              'grid grid-cols-1 gap-2 rounded-card border border-line bg-surface px-4 py-4 last:md:border-0 md:gap-4 md:rounded-none md:border-x-0 md:border-b md:border-t-0 md:bg-transparent md:px-5',
              onRowClick && 'cursor-pointer transition hover:border-ink/15 hover:shadow-card focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ink/20 md:hover:shadow-none md:hover:bg-ink/[0.03]',
            )}
            style={{ gridTemplateColumns: undefined } as React.CSSProperties}
          >
            <div
              role="presentation"
              className="contents md:!grid md:items-center"
              style={{ gridTemplateColumns: gridTemplate }}
            >
              {columns.map((c) => {
                const content = c.render(r);
                // (#293) Only ellipsis-clip left-aligned plain text. Rendered
                // nodes (badges) and right-aligned money/numeric cells are never
                // clipped; truncated text cells expose the full value via title.
                const isText = typeof content === 'string' || typeof content === 'number';
                const doTruncate = c.truncate !== false && isText && c.align !== 'right';
                return (
                  <div
                    key={c.key}
                    role="gridcell"
                    className={cn(
                      'flex justify-between gap-2 text-body md:block',
                      c.align === 'right' && 'md:text-right',
                    )}
                  >
                    <span className="text-micro uppercase tracking-wider text-muted md:hidden">
                      {c.label}
                    </span>
                    <span
                      // (#647) Truncate only on the desktop grid (md+); on the
                      // mobile card layout let long values WRAP so they stay fully
                      // readable (title hover is useless on touch).
                      className={cn('min-w-0', doTruncate && 'md:truncate')}
                      title={isText ? String(content) : undefined}
                    >{content}</span>
                  </div>
                );
              })}
            </div>
          </li>
        ))}
      </ul>
      {total > pageSize && (
        <div className="flex items-center justify-between gap-3 px-2 py-3 text-body text-muted md:px-5">
          <span className="tabular-nums">{start + 1}–{Math.min(start + pageSize, total)} / {total}</span>
          <div className="flex items-center gap-1">
            <button
              onClick={() => setPage((p) => Math.max(0, p - 1))}
              disabled={page === 0}
              className="grid h-8 w-8 tap-target place-items-center rounded-icon border border-line text-ink-soft hover:bg-ink/5 disabled:opacity-30"
              aria-label={t('common.prevPage', 'Oldingi sahifa')}
            >
              <ChevronLeft size={16} />
            </button>
            <span className="px-1 tabular-nums">{page + 1} / {pageCount}</span>
            <button
              onClick={() => setPage((p) => Math.min(pageCount - 1, p + 1))}
              disabled={page >= pageCount - 1}
              className="grid h-8 w-8 tap-target place-items-center rounded-icon border border-line text-ink-soft hover:bg-ink/5 disabled:opacity-30"
              aria-label={t('common.nextPage', 'Keyingi sahifa')}
            >
              <ChevronRight size={16} />
            </button>
          </div>
        </div>
      )}
    </div>
  );
}

/** Memoized so a parent re-render that doesn't change `columns`/`rows` (e.g. a
 *  debounced search keystroke, an unrelated filter-drawer toggle) skips
 *  re-rendering every row (H17). `React.memo` erases the generic signature —
 *  the cast restores it so callers still get `DataTable<Row>` inference. */
export const DataTable = memo(DataTableInner) as typeof DataTableInner;
