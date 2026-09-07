// @vitest-environment jsdom
/**
 * C3-10 — the shared list renders the shared empty state.
 *
 * `DataTable` built its own centred card, so the most-seen empty state in the
 * product was the one place that did not use `EmptyState`. That is the exact
 * duplication the standard's component table exists to prevent, and it drifts:
 * the hand-rolled version used `text-lg`/`text-sm text-muted` where the
 * primitive uses the type-scale tokens and `text-ink-soft`.
 */
import { describe, expect, it, beforeEach, vi } from 'vitest';
import { render, screen, cleanup } from '@testing-library/react';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import i18n from '@/i18n';

vi.mock('react-i18next', async (orig) => orig<Record<string, unknown>>());

import { DataTable } from './DataTable';
import { EmptyState } from './EmptyState';

type Row = { id: string; name: string };
const columns = [{ key: 'name', header: 'Name', render: (r: Row) => r.name }];

const renderTable = (props: Record<string, unknown> = {}) =>
  render(<DataTable<Row> columns={columns} rows={[]} rowKey={(r) => r.id} {...props} />);

beforeEach(async () => { cleanup(); await i18n.changeLanguage('uz'); });

describe('the empty branch uses the canonical primitive', () => {
  it('renders the same markup EmptyState produces', () => {
    const { container: table } = renderTable({ emptyTitle: 'Nothing', emptyBody: 'Not yet' });
    const tableHtml = table.innerHTML;
    cleanup();
    const { container: canonical } = render(<EmptyState title="Nothing" body="Not yet" />);
    expect(tableHtml).toBe(canonical.innerHTML);
  });

  it('keeps the caller\'s emptyTitle and emptyBody', () => {
    renderTable({ emptyTitle: 'No customers yet', emptyBody: 'Add your first one.' });
    expect(screen.getByRole('heading', { name: 'No customers yet' })).toBeTruthy();
    expect(screen.getByText('Add your first one.')).toBeTruthy();
  });

  it('falls back to the shared localized defaults', () => {
    renderTable();
    expect(screen.getByRole('heading', { name: i18n.t('common.emptyTitle') })).toBeTruthy();
    expect(screen.getByText(i18n.t('common.emptyBody'))).toBeTruthy();
  });

  it('renders the title as a heading, as the primitive does', () => {
    renderTable({ emptyTitle: 'Bo‘sh' });
    expect(screen.getByRole('heading', { name: 'Bo‘sh' }).tagName).toBe('H3');
  });

  it('shows no table semantics when there is nothing to show', () => {
    renderTable();
    expect(screen.queryByRole('grid')).toBeNull();
    expect(screen.queryByRole('row')).toBeNull();
  });

  it('still renders rows when there are any', () => {
    cleanup();
    render(<DataTable<Row> columns={columns} rows={[{ id: '1', name: 'Aziz' }]} rowKey={(r) => r.id} />);
    expect(screen.getByText('Aziz')).toBeTruthy();
    expect(screen.getByRole('grid')).toBeTruthy();
  });
});

describe('there is only one empty-state implementation', () => {
  it('DataTable imports it rather than rebuilding it', () => {
    const src = readFileSync(resolve(__dirname, 'DataTable.tsx'), 'utf8');
    expect(src).toContain("from './EmptyState'");
    expect(src, 'a second hand-rolled empty card is back')
      .not.toMatch(/className="card flex flex-col items-center gap-2/);
  });
});
