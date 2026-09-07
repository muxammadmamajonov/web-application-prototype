// @vitest-environment jsdom
/**
 * P2-07.2 — headings must carry a design-system type token, not a raw px value.
 *
 * `Wizard` and `MiniAppHeader` both hardcoded `text-[17px]`. The standard's
 * scale is `text-micro/caption/small/body/subsection/section/title/display`;
 * an arbitrary-value class opts out of it, so the heading stops responding to
 * the fluid scale and drifts the moment the scale is retuned.
 *
 * `text-section` is the right token here: it is
 * `clamp(1.0625rem, 1rem + 0.4vw, 1.25rem)` — a 17px floor, so a phone renders
 * exactly what the hardcoded value did, while wider viewports get the intended
 * 20px instead of staying stuck at 17.
 *
 * These two headers are documented as mirroring each other, so they are pinned
 * together — fixing one alone would silently split them at desktop widths.
 */
import { describe, expect, it, beforeEach, vi } from 'vitest';
import { render, screen, cleanup } from '@testing-library/react';
import { MemoryRouter } from 'react-router';
import { readdirSync, readFileSync } from 'node:fs';
import { resolve } from 'node:path';

/** MiniAppHeader renders only inside Telegram; Wizard changes chrome there. */
let INSIDE_TG = false;
vi.mock('@/lib/telegram', async (orig) => ({
  ...(await orig<Record<string, unknown>>()),
  isInsideTelegramClient: () => INSIDE_TG,
}));

import { Wizard } from './Wizard';
import { MiniAppHeader } from '@/components/layout/MiniAppHeader';

/** Every size in the documented scale. */
const SCALE = ['micro', 'caption', 'small', 'body', 'subsection', 'section', 'title', 'display'];
const ARBITRARY_TYPE = /\btext-\[\s*[\d.]+(px|rem|em)\b/;

const classes = (el: Element) => el.className.split(/\s+/);

beforeEach(() => { cleanup(); INSIDE_TG = false; });

describe('the wizard heading uses the type scale', () => {
  const renderWizard = () => render(
    <MemoryRouter>
      <Wizard
        open
        title="Yangi shartnoma"
        steps={[{ key: 'a', title: 'Mijoz', render: () => <p>step</p> }]}
        onSubmit={() => {}}
        onClose={() => {}}
      />
    </MemoryRouter>,
  );

  it('renders the title as a heading', () => {
    renderWizard();
    expect(screen.getByRole('heading', { name: 'Yangi shartnoma' })).toBeTruthy();
  });

  it('carries a scale token and no arbitrary px size', () => {
    renderWizard();
    const cls = screen.getByRole('heading', { name: 'Yangi shartnoma' }).className;
    expect(ARBITRARY_TYPE.test(cls), `arbitrary type size in "${cls}"`).toBe(false);
    expect(SCALE.some((s) => cls.split(/\s+/).includes(`text-${s}`)), `no scale token in "${cls}"`).toBe(true);
  });

  it('specifically uses text-section', () => {
    renderWizard();
    expect(classes(screen.getByRole('heading', { name: 'Yangi shartnoma' }))).toContain('text-section');
  });
});

describe('the mini-app header mirrors it', () => {
  /**
   * The TYPE contract is unchanged and still pinned. What changed (COR-01) is
   * the SEMANTIC one: this strip is chrome, not a heading, so it is no longer
   * found by role — the routed page owns the document's only `h1`. Locating it
   * by text keeps the token assertion honest without re-requiring the heading
   * semantics that produced a duplicate page title inside Telegram.
   */
  const chromeTitle = (root: ParentNode, text: string) =>
    Array.from(root.querySelectorAll('*'))
      .find((e) => e.textContent === text && e.children.length === 0)!;

  it('uses the same token, so the two headers cannot drift apart', () => {
    INSIDE_TG = true;
    const { container } = render(
      <MemoryRouter initialEntries={['/uz/tenant/overview']}>
        <MiniAppHeader navRoots={['/uz/tenant/overview']} title="Boshqaruv" />
      </MemoryRouter>,
    );
    const el = chromeTitle(container, 'Boshqaruv');
    expect(el, 'the chrome title is still rendered').toBeTruthy();
    expect(ARBITRARY_TYPE.test(el.className), `arbitrary type size in "${el.className}"`).toBe(false);
    expect(classes(el)).toContain('text-section');
  });

  it('is chrome, not a heading — it must not compete with the page h1', () => {
    INSIDE_TG = true;
    render(
      <MemoryRouter initialEntries={['/uz/tenant/overview']}>
        <MiniAppHeader navRoots={['/uz/tenant/overview']} title="Boshqaruv" />
      </MemoryRouter>,
    );
    expect(screen.queryAllByRole('heading')).toHaveLength(0);
  });
});

describe('no shared component reintroduces a raw px type size', () => {
  // A rendered assertion only covers what this suite mounts, so the shared
  // component tree is swept directly. Scoped to `src/components` sources —
  // never node_modules or build output.
  it('src/components carries no text-[Npx]', () => {
    const root = resolve(__dirname, '..');
    const offenders: string[] = [];
    const walk = (dir: string) => {
      for (const e of readdirSync(dir, { withFileTypes: true })) {
        const p = resolve(dir, e.name);
        if (e.isDirectory()) { walk(p); continue; }
        if (!/\.tsx?$/.test(e.name) || e.name.includes('.test.')) continue;
        for (const line of readFileSync(p, 'utf8').split('\n')) {
          if (ARBITRARY_TYPE.test(line)) offenders.push(`${p.slice(root.length + 1)}: ${line.trim()}`);
        }
      }
    };
    walk(root);
    expect(offenders, `use a scale token instead:\n${offenders.join('\n')}`).toEqual([]);
  });
});
