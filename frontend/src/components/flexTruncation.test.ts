/**
 * `truncate` on a flex child needs `min-w-0`, or it does nothing.
 *
 * THE BUG THIS PINS (reported from a phone, 2026-08-06). After creating a
 * customer, the "join link" row showed the full `t.me/MuddatoBot?startapp=…`
 * URL beside a Share button. The URL had `flex-1 truncate`, which reads like it
 * should clip — but a flex item's `min-width` defaults to `auto`, meaning it
 * refuses to shrink below its content. An unbreakable monospace URL therefore
 * forced the row wider than the sheet, and the whole modal scrolled sideways;
 * the Share button was off-screen until the user dragged the dialog left.
 *
 * `truncate` is `overflow:hidden` + `text-overflow:ellipsis` + `white-space:
 * nowrap`. None of those shrink a flex item. Only `min-width: 0` does.
 *
 * The failure is silent — it looks right in every desktop viewport wide enough
 * for the content, and only breaks where there is least room. That is exactly
 * the kind of defect a source guard is for.
 */
import { describe, expect, it } from 'vitest';
import { readdirSync, readFileSync } from 'node:fs';
import { resolve } from 'node:path';

const SRC = resolve(__dirname, '..');

function sourceFiles(dir = SRC, prefix = ''): Array<{ rel: string; text: string }> {
  const out: Array<{ rel: string; text: string }> = [];
  for (const e of readdirSync(dir, { withFileTypes: true })) {
    const rel = prefix ? `${prefix}/${e.name}` : e.name;
    if (e.isDirectory()) { out.push(...sourceFiles(resolve(dir, e.name), rel)); continue; }
    if (!e.name.endsWith('.tsx') || e.name.includes('.test.')) continue;
    out.push({ rel, text: readFileSync(resolve(dir, e.name), 'utf8') });
  }
  return out;
}

const FILES = sourceFiles();

describe('the sweep reaches the source', () => {
  it('scans a meaningful tree', () => {
    expect(FILES.length).toBeGreaterThan(100);
  });
});

describe('flex children that truncate can actually shrink', () => {
  it('every `flex-1` + `truncate` carries `min-w-0`', () => {
    const offenders: string[] = [];
    for (const { rel, text } of FILES) {
      text.split('\n').forEach((line, i) => {
        if (!line.includes('flex-1') || !line.includes('truncate')) return;
        if (line.includes('min-w-0')) return;
        offenders.push(`${rel}:${i + 1}  ${line.trim().slice(0, 90)}`);
      });
    }
    expect(offenders,
      `these will overflow their row instead of truncating:\n${offenders.join('\n')}\n` +
      'A flex item has `min-width: auto` and will not shrink below its content, ' +
      'so `truncate` never engages — long unbreakable text (a URL, a slug, an ' +
      'email) widens the whole row and scrolls the container sideways on a phone. ' +
      'Add `min-w-0`.',
    ).toEqual([]);
  });

  it('the rows that were reported stay fixed', () => {
    // The two invite-link rows a person actually hit.
    const named = ['routes/tenant/_modals/CustomerModal.tsx', 'routes/tenant/CustomersPage.tsx'];
    for (const rel of named) {
      const text = FILES.find((f) => f.rel === rel)?.text ?? '';
      expect(text, `${rel} not found`).toBeTruthy();
      const linkRow = text.split('\n').find((l) => l.includes('font-mono') && l.includes('truncate'));
      expect(linkRow, `${rel}: the link row disappeared — re-point this test`).toBeTruthy();
      expect(linkRow, `${rel}: link row lost min-w-0`).toContain('min-w-0');
    }
  });
});
