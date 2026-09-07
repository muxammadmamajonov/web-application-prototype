// @vitest-environment jsdom
/**
 * Leaving Settings must WALK BACK, not build a longer way in.
 *
 * THE BUG (production, 2026-08-11). Reported as: open Main Settings, change
 * something, press Telegram's Back, press it again — and Main Settings opens
 * again instead of continuing to the previous screen.
 *
 * `SettingsShell` pushed a history entry for every transition, including the one
 * its own back affordance performs. Leaving a sub-page therefore did not undo
 * entering it; it appended a third entry:
 *
 *     /settings          (A)
 *     /settings?tab=x    (B)  ← opened a tab
 *     /settings          (C)  ← pressed Back — a PUSH
 *
 * Inside Telegram that turned into a closed loop, because the native BackButton
 * is a singleton owned by a STACK: `MiniAppHeader` sits at the bottom with
 * `navigate(-1)`, and the open sub-page's `MobileHeader` sits on top with
 * `open(null)`. Press once → the top handler runs → C. The sub-page unmounts and
 * its handler pops. Press again → `navigate(-1)` → **B, the sub-page, again**.
 * Press again → C. Forever, between the same two URLs, with no way out.
 *
 * So the test is not "does the URL change" — the buggy version changed the URL
 * every time. It presses the REAL native BackButton repeatedly, through the REAL
 * handler stack, and asserts the sequence terminates: Settings is left, and
 * never re-entered by pressing Back.
 */
import { describe, expect, it, beforeEach, afterEach, vi } from 'vitest';
import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter, Route, Routes, useLocation } from 'react-router';
import { Settings as SettingsIcon, Bell } from 'lucide-react';
import { MiniAppHeader } from './MiniAppHeader';
import { SettingsShell, type SettingsTab } from './SettingsShell';

/** The one control Telegram actually gives the user. */
interface FakeBack { handlers: (() => void)[]; shown: boolean }
let back: FakeBack;

function pressNativeBack() {
  // Telegram invokes every registered onClick; the hook keeps exactly one bound.
  //
  // `act` IS LOAD-BEARING — do not "simplify" it away. A press must return with
  // the whole interaction finished, because the next press reads state the
  // previous one is still settling. `useTelegramBackButton` splits itself across
  // two phases: `onBackRef.current` is reassigned during RENDER, while the
  // handler is popped off the shared stack in the PASSIVE EFFECT cleanup. When a
  // sub-page closes, `MobileHeader` stays mounted, so in the gap between those
  // two the stack still names it while its ref already holds the `() => {}`
  // fallback — present but inert. A press landing there is a no-op and the route
  // stalls one screen short. `await waitFor(...)` cannot close the gap: it
  // observes the commit, which is the EARLIER of the two events. Real Telegram
  // presses arrive through the event loop, so production never sees this window.
  act(() => { for (const h of [...back.handlers]) h(); });
}

const TABS: SettingsTab[] = [
  { key: 'defaults',  label: 'Asosiy qiymatlar', sub: 'Standart tarif', icon: SettingsIcon },
  { key: 'reminders', label: 'Eslatmalar',       sub: 'Kunlar',         icon: Bell },
];

/** The URL trail, so the assertion can talk about the whole journey. */
const trail: string[] = [];
function Trail() {
  const loc = useLocation();
  const here = loc.pathname + loc.search;
  if (trail[trail.length - 1] !== here) trail.push(here);
  return <div data-testid="here">{here}</div>;
}

/** `AppShell`'s composition, reduced to the two things that own Back. */
function SettingsScreen() {
  return (
    <>
      <Trail />
      {/* Settings is not a bottom-nav root, so this owns Back for the page. */}
      <MiniAppHeader navRoots={['/uz/platform/overview', '/uz/platform/menu']} />
      <SettingsShell title="Sozlamalar" tabs={TABS}>
        {(key) => <div data-testid="pane">{key}</div>}
      </SettingsShell>
    </>
  );
}

function renderApp() {
  return render(
    <MemoryRouter initialEntries={['/uz/platform/menu', '/uz/platform/settings']} initialIndex={1}>
      <Routes>
        <Route path="/uz/platform/menu" element={<><Trail /><div data-testid="menu">MENU</div></>} />
        <Route path="/uz/platform/settings" element={<SettingsScreen />} />
      </Routes>
    </MemoryRouter>,
  );
}

/** Open a sub-page the way a phone user does: tap the row in the mobile list. */
function openTab(label: string) {
  const rows = screen.getAllByRole('button', { name: new RegExp(label) });
  fireEvent.click(rows[rows.length - 1]);
}

describe('Settings · native Back does not loop', () => {
  beforeEach(() => {
    trail.length = 0;
    back = { handlers: [], shown: false };
    (window as unknown as Record<string, unknown>).Telegram = {
      WebApp: {
        platform: 'ios',
        initData: 'x'.repeat(32),
        BackButton: {
          onClick: (h: () => void) => { back.handlers.push(h); },
          offClick: (h: () => void) => {
            const i = back.handlers.indexOf(h);
            if (i >= 0) back.handlers.splice(i, 1);
          },
          show: () => { back.shown = true; },
          hide: () => { back.shown = false; },
        },
      },
    };
    Element.prototype.scrollIntoView = vi.fn();
  });
  afterEach(() => {
    cleanup();
    delete (window as unknown as Record<string, unknown>).Telegram;
    vi.restoreAllMocks();
  });

  it('walks out of Settings instead of re-entering it', async () => {
    renderApp();
    openTab('Asosiy qiymatlar');
    await waitFor(() => expect(screen.getByTestId('here').textContent).toContain('tab=defaults'));

    // Press 1 — leave the sub-page.
    pressNativeBack();
    await waitFor(() => expect(screen.getByTestId('here').textContent).not.toContain('tab='));

    // Press 2 — leave Settings. THE ASSERTION: before the fix this landed back
    // on `?tab=defaults`, and press 3 returned here, and so on without end.
    pressNativeBack();
    await waitFor(() => expect(screen.queryByTestId('menu')).not.toBeNull());

    expect(trail.filter((u) => u.includes('tab=defaults'))).toHaveLength(1);
    expect(trail[trail.length - 1]).toBe('/uz/platform/menu');
  });

  it('never oscillates — no URL is visited twice by pressing Back', async () => {
    renderApp();
    openTab('Asosiy qiymatlar');
    await waitFor(() => expect(screen.getByTestId('here').textContent).toContain('tab=defaults'));

    const beforeBack = trail.length;
    for (let i = 0; i < 6; i++) {
      pressNativeBack();
      await waitFor(() => expect(screen.getByTestId('here').textContent).toBeTruthy());
    }

    // Back is a walk, not a cycle: each press either moves somewhere new or does
    // nothing at the root. The buggy version appended a fresh entry on every
    // press, so this trail grew by six and alternated between two URLs.
    const afterBack = trail.slice(beforeBack);
    expect(afterBack.length).toBeLessThanOrEqual(2);
    expect(new Set(afterBack).size).toBe(afterBack.length);
    expect(trail[trail.length - 1]).toBe('/uz/platform/menu');
  });

  it('does not stack a history entry per tab tapped', async () => {
    renderApp();
    openTab('Asosiy qiymatlar');
    await waitFor(() => expect(screen.getByTestId('here').textContent).toContain('tab=defaults'));

    // Switching sub-pages is a lateral move, not a drill-down: five taps must
    // not cost five Back presses to undo.
    openTab('Eslatmalar');
    await waitFor(() => expect(screen.getByTestId('here').textContent).toContain('tab=reminders'));
    openTab('Asosiy qiymatlar');
    await waitFor(() => expect(screen.getByTestId('here').textContent).toContain('tab=defaults'));

    pressNativeBack();
    await waitFor(() => expect(screen.getByTestId('here').textContent).not.toContain('tab='));
    pressNativeBack();
    await waitFor(() => expect(screen.queryByTestId('menu')).not.toBeNull());
  });

  it('a deep link straight to a sub-page leaves Settings on the first Back', async () => {
    // Nobody pushed this entry, so leaving it must not try to pop past the
    // screen the user arrived from.
    render(
      <MemoryRouter initialEntries={['/uz/platform/menu', '/uz/platform/settings?tab=defaults']} initialIndex={1}>
        <Routes>
          <Route path="/uz/platform/menu" element={<><Trail /><div data-testid="menu">MENU</div></>} />
          <Route path="/uz/platform/settings" element={<SettingsScreen />} />
        </Routes>
      </MemoryRouter>,
    );
    await waitFor(() => expect(screen.getByTestId('here').textContent).toContain('tab=defaults'));

    pressNativeBack();
    await waitFor(() => expect(screen.getByTestId('here').textContent).not.toContain('tab='));
    pressNativeBack();
    await waitFor(() => expect(screen.queryByTestId('menu')).not.toBeNull());
  });
});
