/**
 * Make a design token usable with Tailwind's opacity modifier (`bg-ink/50`).
 *
 * THE BUG THIS FIXES (found on a real device, 2026-08-08). Tailwind can only
 * apply `/N` to a color it can decompose into channels. Our tokens are bare
 * `var(--c-*)` holding COMPLETE colors (`oklch(...)`, `#fff`, `rgba(...)`),
 * which it cannot take apart — so every modifier compiled to an invalid
 * declaration and fell back to `transparent`. That silently killed 264
 * utilities across 88 files: every modal/drawer backdrop scrim (`bg-ink/50`,
 * `bg-ink/40`) rendered with no dim at all, and all 60 `hover:bg-ink/5` plus
 * the `hover:bg-ink/[0.03]` row hovers did nothing — the sidebar genuinely had
 * no hover state. It is invisible in review because the class name is correct
 * and Tailwind emits no warning; only the computed style shows `rgba(0,0,0,0)`.
 *
 * `color-mix` keeps each token EXACTLY as authored and lets the alpha through.
 * Tailwind substitutes `<alpha-value>` with the modifier's decimal (50 → `0.5`)
 * or with `1` when there is no modifier, so `bg-ink` still resolves to the solid
 * token. No token value changes — this repairs the plumbing, it does not retune
 * the palette.
 *
 * Support matches what the palette already requires: `color-mix()` and
 * `oklch()` both land in Safari 16.2+ / Chrome 111+.
 */
const alpha = (token) =>
  `color-mix(in oklab, ${token} calc(<alpha-value> * 100%), transparent)`;

/** @type {import('tailwindcss').Config} */
export default {
  content: ['./index.html', './src/**/*.{ts,tsx}'],
  theme: {
    extend: {
      fontFamily: {
        sans: ['"Inter Tight"', 'system-ui', 'sans-serif'],
        mono: ['"JetBrains Mono"', 'ui-monospace', 'SFMono-Regular', 'monospace'],
      },
      // The single authoritative source for every font size in the product.
      // `globals.css` declares none of its own — the `.h-*` heading classes are
      // `@apply` aliases over these steps that add only weight/tracking — so a
      // size changed here moves every consumer. (The one exception is the iOS
      // input-zoom safeguard in `globals.css`, which is a browser workaround,
      // not a scale step.)
      //
      // Steps are named for their ROLE, not their pixel value, so two that
      // happen to share a number stay separable if the design later diverges.
      // Under `extend`, so Tailwind's own scale still resolves; `designSystem`
      // is the guard that keeps raw `text-sm|base|lg|…` out of source.
      fontSize: {
        micro:      ['11px', { lineHeight: '1.3' }],   // kicker / micro-labels / bottom-nav
        caption:    ['12px', { lineHeight: '1.4' }],   // hints, dense meta
        small:      ['13px', { lineHeight: '1.45' }],  // secondary text
        body:       ['14px', { lineHeight: '1.5' }],   // default body
        subsection: ['16px', { lineHeight: '1.4' }],   // card/sub headers, marketing lead
        // FLUID steps interpolate with the viewport so they never overflow a
        // 320px phone nor look undersized on a wide monitor. Min = smallest
        // phone; max = the desktop size.
        section:    ['clamp(1.0625rem, 1rem + 0.4vw, 1.25rem)', { lineHeight: '1.25' }],  // 17 → 20px — section headers
        title:      ['clamp(1.375rem, 1.1rem + 1.4vw, 1.75rem)', { lineHeight: '1.1' }],  // 22 → 28px — KPI / metric figures
        // Route-level <h1>. Shares `title`'s 22px floor but climbs higher on
        // desktop, which is the deliberate hierarchy between a page heading and
        // a KPI number sitting under it. Consumed via `.h-page`.
        'page-title': ['clamp(1.375rem, 1.05rem + 1.55vw, 2rem)', { lineHeight: '1.1' }], // 22 → 32px
        // The largest in-app step: hero money figures and auth headings.
        display:    ['clamp(1.5rem, 1.05rem + 2.1vw, 2.5rem)', { lineHeight: '1.05' }],   // 24 → 40px
        // A bank-card number must scale with the card tile it sits on rather
        // than with the page, so it keeps its monospace grouping legible on a
        // 320px phone without overflowing the tile. Values are exactly the ones
        // the tile already rendered — this names the ramp, it does not retune it.
        'card-number': ['clamp(13px, 4.4vw, 20px)'],
        // Marketing/auth display ramps — deliberately larger than the admin
        // scale. Consumed via `.h-hero*`; sized to match the pre-token px at
        // ~390px mobile and ~1280px desktop.
        hero:       ['clamp(2.25rem, 1.375rem + 3.6vw, 4.25rem)'],  // 36 → 68px — landing <h1>
        'hero-2':   ['clamp(2.5rem, 2.06rem + 1.8vw, 3.5rem)'],     // 40 → 56px — secondary marketing <h1>
        'hero-3':   ['clamp(2.25rem, 2.03rem + 0.9vw, 2.75rem)'],   // 36 → 44px — marketing section display
      },
      colors: {
        // The design uses oklch tokens — we keep them as CSS vars in globals.css
        // and expose semantic aliases here, each wrapped by `alpha()` so the
        // opacity modifier works. See the `alpha` docstring: without it every
        // `bg-ink/5` in the app silently rendered transparent.
        ink:        alpha('var(--c-ink)'),
        'ink-soft': alpha('var(--c-ink-soft)'),
        'ink-300':  alpha('var(--c-ink-300)'),
        muted:      alpha('var(--c-muted)'),
        cream:      alpha('var(--c-cream)'),
        'cream-soft': alpha('var(--c-bg-soft)'),
        surface:    alpha('var(--c-surface)'),
        line:       alpha('var(--c-line)'),
        accent: {
          DEFAULT: alpha('var(--c-accent)'),
          light:   alpha('var(--c-accent-light)'),
          soft:    alpha('var(--c-accent-soft)'),
          ink:     alpha('var(--c-accent-ink)'),
          strong:  alpha('var(--c-accent-strong)'),
          fill:    alpha('var(--c-accent-fill)'),
        },
        // (#629) Each semantic family exposes DEFAULT + soft + strong so callers
        // can write text-success-strong / bg-info-soft instead of inline var().
        success: { DEFAULT: alpha('var(--c-success)'), soft: alpha('var(--c-success-soft)'), strong: alpha('var(--c-success-strong)') },
        warn:    { DEFAULT: alpha('var(--c-warn)'),    soft: alpha('var(--c-warn-soft)'),    strong: alpha('var(--c-warn-strong)') },
        danger:  { DEFAULT: alpha('var(--c-danger)'),  soft: alpha('var(--c-danger-soft)'),  strong: alpha('var(--c-danger-strong)') },
        info:    { DEFAULT: alpha('var(--c-info)'),    soft: alpha('var(--c-info-soft)'),    strong: alpha('var(--c-info-strong)') },
      },
      // One token per shape role; no raw `rounded-xl|2xl|lg|md|full` in source.
      // `control` is 12px because that is what the controls actually render —
      // the token was aligned to the product rather than re-radiusing every
      // control to a value almost nothing used.
      borderRadius: {
        card: '16px',     // cards, panels, sheets
        control: '12px',  // inputs, buttons, selects, menu surfaces
        icon: '8px',      // compact icon/ghost buttons, chips, skeletons
        // 16px checkboxes and the ESC `kbd` badge: `icon`'s 8px on a 16px box
        // reads as a squircle, so this is a real role rather than drift.
        micro: '4px',
        pill: '999px',    // pills, avatars, fully-round controls
      },
      // `header` is a genuine third step — a hairline plus a soft drop, used
      // only by the sticky public header when scrolled.
      boxShadow: {
        card: '0 1px 2px rgba(20, 22, 28, 0.04), 0 1px 6px rgba(20, 22, 28, 0.04)',
        pop: '0 6px 24px rgba(20, 22, 28, 0.10)',
        header: '0 1px 0 rgba(0, 0, 0, 0.04), 0 8px 24px -12px rgba(0, 0, 0, 0.18)',
      },
      letterSpacing: {
        kicker: '0.14em',
      },
      // Named width intents. Each value equals the Tailwind step it replaces,
      // so these are readable/centrally-changeable names, not a layout change.
      maxWidth: {
        content: '1480px', // page shell
        lead: '42rem',     // marketing lead paragraph / intro copy (= max-w-2xl)
        panel: '28rem',    // form, dialog and search columns (= max-w-md)
        narrow: '24rem',   // compact centred card, e.g. the crash screens (= max-w-sm)
        'hero-col': '560px', // landing hero copy + image column
      },
    },
  },
  plugins: [],
};
