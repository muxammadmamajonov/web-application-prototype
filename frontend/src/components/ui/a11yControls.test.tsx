// @vitest-environment jsdom
/**
 * Phase 3 remediation R-04 — UX-028 / UX-029 on the controls that were missed.
 *
 * The first pass tested `Modal` and `Select` and declared the finding closed.
 * Two retained defects survived that, because nothing rendered them:
 *
 *   * `FilterDrawer`'s header close button had no accessible name. The drawer's
 *     *backdrop* was named, which is a different control — naming one does not
 *     name the other, and a keyboard user landed on an unlabelled button.
 *   * `ImageUploadField`'s remove control was a 16 px unnamed dot that destroys
 *     the attached image.
 *
 * These render the real components and assert the real accessibility tree.
 */
import { describe, expect, it, beforeEach, vi } from 'vitest';
import { render, screen, cleanup, within } from '@testing-library/react';
import i18n from '@/i18n';
import { FilterDrawer } from './FilterDrawer';
import { ImageUploadField } from './ImageUploadField';

beforeEach(async () => { cleanup(); await i18n.changeLanguage('uz'); });

describe('FilterDrawer (UX-028)', () => {
  const open = () =>
    render(<FilterDrawer open onClose={() => {}}><div>body</div></FilterDrawer>);

  it('names its header close button', () => {
    open();
    const named = screen.getAllByRole('button')
      .filter((b) => (b.getAttribute('aria-label') ?? '').trim().length > 0);
    expect(named.length, 'every control in the drawer header is named').toBeGreaterThan(0);
    expect(screen.getByRole('button', { name: i18n.t('common.closeFilters') })).toBeTruthy();
  });

  it('leaves no unnamed icon-only button behind', () => {
    open();
    for (const b of screen.getAllByRole('button')) {
      const hasText = (b.textContent ?? '').trim().length > 0;
      const hasName = (b.getAttribute('aria-label') ?? '').trim().length > 0
        || !!b.getAttribute('aria-labelledby');
      expect(hasText || hasName, `unnamed control: ${b.outerHTML.slice(0, 90)}`).toBe(true);
    }
  });

  it('names the close button and the backdrop separately', () => {
    // The bug was treating the backdrop's name as covering both.
    open();
    const labels = screen.getAllByRole('button')
      .map((b) => b.getAttribute('aria-label'))
      .filter(Boolean);
    expect(labels).toContain(i18n.t('common.close'));       // backdrop
    expect(labels).toContain(i18n.t('common.closeFilters')); // header button
  });

  it('announces its own identity as a dialog', () => {
    open();
    expect(screen.getByRole('dialog', { name: i18n.t('common.filter') })).toBeTruthy();
  });

  it('is localized', async () => {
    open();
    const uz = screen.getByRole('button', { name: i18n.t('common.closeFilters') });
    expect(uz).toBeTruthy();
    cleanup();
    await i18n.changeLanguage('ru');
    open();
    expect(screen.getByRole('button', { name: i18n.t('common.closeFilters') })).toBeTruthy();
    expect(i18n.t('common.closeFilters')).toBeTruthy();
    await i18n.changeLanguage('uz');
  });
});

describe('ImageUploadField (UX-028 / UX-071)', () => {
  it('names the remove control and says what it removes', () => {
    render(<ImageUploadField value="https://example.test/a.png" onChange={() => {}} />);
    const remove = screen.getByRole('button', { name: i18n.t('imageUpload.remove') });
    expect(remove).toBeTruthy();
  });

  it('the remove control carries the coarse-pointer target class', () => {
    // 16 px was the original size. `tap-target` is the shared rule that widens
    // the hit area at coarse pointers; the painted size is device-verified.
    render(<ImageUploadField value="https://example.test/a.png" onChange={() => {}} />);
    const remove = screen.getByRole('button', { name: i18n.t('imageUpload.remove') });
    expect(remove.className).toContain('tap-target');
    expect(remove.className).toMatch(/\bh-11\b/);
    expect(remove.className).toMatch(/\bw-11\b/);
  });

  it('actually clears the value', () => {
    const onChange = vi.fn();
    render(<ImageUploadField value="https://example.test/a.png" onChange={onChange} />);
    screen.getByRole('button', { name: i18n.t('imageUpload.remove') }).click();
    expect(onChange).toHaveBeenCalledWith('');
  });

  it('leaves no unnamed icon-only button behind', () => {
    render(<ImageUploadField value="https://example.test/a.png" onChange={() => {}} />);
    for (const b of screen.getAllByRole('button')) {
      const hasText = (b.textContent ?? '').trim().length > 0;
      const hasName = (b.getAttribute('aria-label') ?? '').trim().length > 0
        || !!b.getAttribute('aria-labelledby');
      expect(hasText || hasName, `unnamed control: ${b.outerHTML.slice(0, 90)}`).toBe(true);
    }
  });

  it('hides the decorative icon from assistive technology', () => {
    const { container } = render(
      <ImageUploadField value="https://example.test/a.png" onChange={() => {}} />,
    );
    const remove = screen.getByRole('button', { name: i18n.t('imageUpload.remove') });
    expect(within(remove).getByText((_, el) => el?.tagName.toLowerCase() === 'svg', {
      ignore: '',
    }).getAttribute('aria-hidden')).toBe('true');
    expect(container).toBeTruthy();
  });
});
