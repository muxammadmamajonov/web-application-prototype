// @vitest-environment jsdom
/**
 * Phase 3 Batch 6 — accessibility guarantees for the shared primitives.
 *
 * Every retained MVP surface renders through these components, so pinning the
 * behaviour here covers the whole product rather than one screen. The findings
 * these lock in are UX-030 (dialogs announce their identity), UX-031/UX-032
 * (every control has a name, including the custom ones a wrapping <label>
 * cannot name), UX-067 (lists convey column relationships and sort state) and
 * UX-072 (names exist in both languages).
 */
import { describe, expect, it, beforeEach, vi } from 'vitest';
import { fireEvent, render, screen, within, cleanup } from '@testing-library/react';
import i18n from '@/i18n';
import { Modal, Field } from './Modal';
import { Select } from './Select';
import { DataTable, type Column } from './DataTable';

beforeEach(() => {
  cleanup();
  // jsdom does not implement scrollIntoView; Select calls it to keep the
  // arrow-highlighted option in view. Stub it so the keyboard path under test
  // runs — this is an environment gap, not product behaviour.
  if (!Element.prototype.scrollIntoView) {
    Element.prototype.scrollIntoView = () => {};
  }
});

describe('Field — control naming (UX-032)', () => {
  it('names a custom button-based control from its visible label', () => {
    // A wrapping <label> cannot name a <button>, which is what Select renders —
    // this is exactly the case that left 42 controls unnamed.
    render(
      <Field label="Hisoblash usuli">
        <Select value="none" onChange={() => {}} options={[{ value: 'none', label: 'Ustamasiz' }]} />
      </Field>,
    );
    expect(screen.getByRole('combobox', { name: /Hisoblash usuli/ })).toBeTruthy();
  });

  it('names a native input from its visible label', () => {
    render(
      <Field label="Summa">
        <input />
      </Field>,
    );
    expect(screen.getByRole('textbox', { name: /Summa/ })).toBeTruthy();
  });

  it('lets a control keep a name it declares itself', () => {
    render(
      <Field label="Umumiy">
        <input aria-label="Aniq nom" />
      </Field>,
    );
    expect(screen.getByRole('textbox', { name: 'Aniq nom' })).toBeTruthy();
  });

  it('still associates the error message with the control', () => {
    render(
      <Field label="Telefon" error="Notoʻgʻri raqam">
        <input />
      </Field>,
    );
    const input = screen.getByRole('textbox', { name: /Telefon/ });
    expect(input.getAttribute('aria-invalid')).toBe('true');
    expect(screen.getByRole('alert').textContent).toContain('Notoʻgʻri raqam');
  });
});

describe('Modal — dialog naming (UX-030)', () => {
  it('announces its own title', () => {
    render(<Modal open onClose={() => {}} title="Shartnoma yaratildi">body</Modal>);
    expect(screen.getByRole('dialog', { name: 'Shartnoma yaratildi' })).toBeTruthy();
  });

  it('names its close control in the active language, not English', async () => {
    await i18n.changeLanguage('uz');
    render(<Modal open onClose={() => {}} title="Sarlavha">body</Modal>);
    // "Close" was hard-coded; the name must now come from the translation layer.
    expect(screen.queryAllByRole('button', { name: 'Close' })).toHaveLength(0);
    expect(screen.getAllByRole('button', { name: i18n.t('common.close') }).length).toBeGreaterThan(0);
  });
});

interface Row { id: string; name: string; amount: number }
const ROWS: Row[] = [
  { id: '1', name: 'Alisher', amount: 300 },
  { id: '2', name: 'Bekzod', amount: 100 },
  { id: '3', name: 'Dilnoza', amount: 200 },
];
const COLS: Column<Row>[] = [
  { key: 'name', label: 'Mijoz', render: (r) => r.name },
  { key: 'amount', label: 'Summa', render: (r) => r.amount, sortValue: (r) => r.amount },
];

describe('DataTable — list semantics (UX-067)', () => {
  it('exposes column headers', () => {
    render(<DataTable columns={COLS} rows={ROWS} rowKey={(r) => r.id} />);
    const headers = screen.getAllByRole('columnheader');
    expect(headers.map((h) => h.textContent?.trim())).toEqual(expect.arrayContaining([
      expect.stringContaining('Mijoz'),
      expect.stringContaining('Summa'),
    ]));
  });

  it('announces sort state on the sorted column only', () => {
    render(<DataTable columns={COLS} rows={ROWS} rowKey={(r) => r.id} />);
    const sortable = screen.getAllByRole('columnheader')
      .find((h) => h.textContent?.includes('Summa'))!;
    expect(sortable.getAttribute('aria-sort')).toBe('none');

    fireEvent.click(within(sortable).getByRole('button'));
    expect(sortable.getAttribute('aria-sort')).toMatch(/ascending|descending/);

    // …and the other column stays unsorted.
    const other = screen.getAllByRole('columnheader')
      .find((h) => h.textContent?.includes('Mijoz'))!;
    expect(other.getAttribute('aria-sort')).toBe('none');
  });

  it('exposes a valid grid hierarchy: rowgroup → row → columnheader/gridcell', () => {
    // (R-05) The first pass declared role="grid" while body items were
    // role="button" and no cell had a cell role — a hierarchy that described a
    // table which did not exist.
    const { container } = render(<DataTable columns={COLS} rows={ROWS} rowKey={(r) => r.id} />);
    const grid = screen.getByRole('grid');

    // Every row belongs to a rowgroup, and every rowgroup to the grid.
    const rowgroups = within(grid).getAllByRole('rowgroup');
    expect(rowgroups.length).toBeGreaterThanOrEqual(2);   // header + body

    const rows = within(grid).getAllByRole('row');
    expect(rows.length).toBe(ROWS.length + 1);            // header row + data rows
    for (const row of rows) {
      expect(row.closest('[role="rowgroup"]'), 'row inside a rowgroup').toBeTruthy();
    }

    // Header cells live in the header row; data cells live in data rows.
    for (const h of within(grid).getAllByRole('columnheader')) {
      expect(h.closest('[role="row"]'), 'columnheader inside a row').toBeTruthy();
    }
    const cells = within(grid).getAllByRole('gridcell');
    expect(cells.length).toBe(ROWS.length * COLS.length);
    for (const cell of cells) {
      expect(cell.closest('[role="row"]'), 'gridcell inside a row').toBeTruthy();
    }
    expect(container).toBeTruthy();
  });

  it('each data row owns exactly one cell per column', () => {
    render(<DataTable columns={COLS} rows={ROWS} rowKey={(r) => r.id} />);
    const dataRows = screen.getAllByRole('row')
      .filter((r) => within(r).queryAllByRole('columnheader').length === 0);
    expect(dataRows).toHaveLength(ROWS.length);
    for (const row of dataRows) {
      expect(within(row).getAllByRole('gridcell')).toHaveLength(COLS.length);
    }
  });

  it('a clickable row stays a row and stays keyboard-operable', () => {
    // role="button" on the <li> would have removed the row from the hierarchy.
    const onRowClick = vi.fn();
    render(<DataTable columns={COLS} rows={ROWS} rowKey={(r) => r.id} onRowClick={onRowClick} />);

    const dataRows = screen.getAllByRole('row')
      .filter((r) => within(r).queryAllByRole('columnheader').length === 0);
    expect(dataRows).toHaveLength(ROWS.length);
    expect(screen.queryAllByRole('button', { name: /Alisher/ })).toHaveLength(0);

    const first = dataRows[0];
    expect(first.getAttribute('tabindex')).toBe('0');
    fireEvent.keyDown(first, { key: 'Enter' });
    expect(onRowClick).toHaveBeenCalledTimes(1);
    fireEvent.keyDown(first, { key: ' ' });
    expect(onRowClick).toHaveBeenCalledTimes(2);
    fireEvent.click(first);
    expect(onRowClick).toHaveBeenCalledTimes(3);
  });

  it('keeps the visible column label as the sort control name', () => {
    // The control used to replace its own label with a sentence about sorting,
    // so a screen-reader user never heard the column name.
    render(<DataTable columns={COLS} rows={ROWS} rowKey={(r) => r.id} />);
    expect(screen.getByRole('button', { name: /Summa/ })).toBeTruthy();
  });
});

describe('Select — keyboard operation', () => {
  it('opens and selects with the keyboard alone', () => {
    const onChange = vi.fn();
    render(
      <Field label="Tarif">
        <Select value="" onChange={onChange} options={[
          { value: 'lite', label: 'Lite' },
          { value: 'pro', label: 'Pro' },
        ]} />
      </Field>,
    );
    const combo = screen.getByRole('combobox', { name: /Tarif/ });
    combo.focus();
    // Enter opens the listbox; arrows move the active option; Enter commits —
    // the WAI-ARIA combobox pattern, driven entirely from the keyboard.
    fireEvent.keyDown(combo, { key: 'Enter' });
    expect(screen.getByRole('listbox')).toBeTruthy();
    fireEvent.keyDown(combo, { key: 'ArrowDown' });
    fireEvent.keyDown(combo, { key: 'Enter' });
    expect(onChange).toHaveBeenCalled();
  });
});

describe('localization of accessible names (UX-072)', () => {
  it('renders the close control differently in uz and ru', async () => {
    await i18n.changeLanguage('uz');
    const uz = i18n.t('common.close');
    await i18n.changeLanguage('ru');
    const ru = i18n.t('common.close');
    expect(uz).toBeTruthy();
    expect(ru).toBeTruthy();
    expect(uz).not.toBe(ru);
    await i18n.changeLanguage('uz');
  });
});
