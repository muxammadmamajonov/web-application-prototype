# MUDDATO — PLATFORM STANDARD (read this BEFORE changing or adding anything)

> This is the single source of truth for how the Muddato product looks, behaves, and
> is built. **Read it before you touch any UI, add any screen, or wire any action.**
> It is not aspirational — every rule below is already implemented by a shared
> component or token. Your job is to *reuse*, not reinvent.

---

## 0. MINDSET — what we are building

Muddato is a **production, global-platform-grade** fintech SaaS that runs **inside
Telegram** (Mini App) and on the web. Operators run real money through it. Hold every
change to the bar of **Stripe Dashboard / Linear / Vercel / Shopify admin**.

- ❌ Never ship "prototype", "MVP", "good enough", "student dashboard", or "generic
  shadcn" quality. If a screen feels 7/10, it is not done.
- ❌ Never assume "2–3 people use this." Assume thousands of tenants and a Platform
  Owner who lives in this console all day.
- ✅ Think before you type. Open the page, find what is actually wrong, fix the root
  cause across **all** affected screens — not one patch on one view.

---

## 1. PRE-FLIGHT CHECKLIST (run this every time)

Before writing a component, a page, or an action, confirm:

1. **Does a shared component already exist?** (§3) If yes, use it. Do not hand-roll a
   second filter button, a second select, a second empty state, a second money input.
2. **Same action → same icon + same component** everywhere. (§4) A filter is *always*
   `FilterButton`. A create is *always* a `Button variant="primary"` with `Plus`.
3. **No hardcoded user-facing text. No mixed languages.** Every string goes through
   `t()` with uz + ru locale entries. (§5)
4. **Inputs cannot crash or accept garbage.** Money/phone/percent fields use the
   typed inputs from `FormFields.tsx`, which cap length and format as you type. (§6)
5. **Checked at every breakpoint?** Mobile (~360px) AND desktop (≥1024px). Never fix
   "web only" or "mobile only". (§10)
6. **Telegram-safe?** No competing with native chrome; overlays portal correctly. (§9)
7. **Verified?** `npm run typecheck && npm run lint && npm run test && npm run build`
   all green before you call it done. (§11)

If you cannot check a box, you are not finished.

---

## 2. DESIGN SYSTEM — DO NOT CHANGE IT

The look is fixed. You apply it; you do not redesign it.

**Color tokens** — aligned 1:1 to the product prototype, all `oklch` (CSS vars in
`globals.css`, Tailwind aliases in `tailwind.config.js`):
- `--c-cream` `oklch(0.985 0.005 80)` — app background (warm cream). `--c-bg-soft`
  `oklch(0.96 0.012 75)` — inset/secondary surface. `--c-surface` `#fff` — cards/sidebar/modals.
- Ink scale (hue 265): `--c-ink` `0.18` (primary) · `--c-ink-soft` `0.34` (secondary) ·
  `--c-muted` `0.55` (tertiary) · `--c-ink-300` `0.82` (faint/dividers).
- `--c-accent` `oklch(0.64 0.18 38)` — the **one** terracotta brand accent (dots, fills,
  active highlights, chart-1). `--c-accent-light` `0.78 0.14 45` (gradients/hover),
  `--c-accent-soft` (`/0.16` tint, icon tiles/badges), `--c-accent-fill` (solid button,
  white text AA), `--c-accent-strong` (text on accent-soft, AA).
- `--c-line` — hairline borders.
- Status: `--c-success` `0.62 0.13 155`, `--c-warn` `0.72 0.15 75`, `--c-danger`
  `0.58 0.21 25`, `--c-info` (+ each one's `-soft` / `-strong`).
- ❌ Never introduce a new accent color, a new gray, or a raw hex in JSX. Use the token.

**Shape** — five radius roles, no raw `rounded-xl/2xl/lg/md/full` and no bare `rounded`:
`rounded-card` (**16px**, cards/panels/sheets) · `rounded-control` (**12px**, inputs,
buttons, selects, menu surfaces) · `rounded-icon` (**8px**, compact icon/ghost buttons,
chips, skeletons) · `rounded-micro` (**4px**, checkboxes, `kbd`) · `rounded-pill` (full).
**Elevation** — three steps, no raw `shadow-sm/md/lg`: `shadow-card` (resting surface) ·
`shadow-pop` (overlays) · `shadow-header` (the sticky public header, scrolled).
**Type:** **Inter Tight** (display 600, tight tracking) for text; **JetBrains Mono** for
figures (`font-mono` + `tabular-nums`). Both loaded in `index.html`.

**Typography** — `tailwind.config.js → fontSize` is the **single authority**. Never
`text-[Npx]`, and never a raw Tailwind size (`text-sm/base/lg/xl/2xl/3xl`):
`text-micro 11 · text-caption 12 · text-small 13 · text-body 14 · text-subsection 16 ·
text-section 17→20 · text-title 22→28 · text-page-title 22→32 · text-display 24→40`,
plus `text-hero / hero-2 / hero-3` (marketing) and `text-card-number` (bank-card tiles).
The `.h-display / .h-page / .h-section / .h-sub / .h-hero*` classes are **aliases** that
`@apply` those steps and add only weight/tracking — they define no size of their own, so
the scale and the headings can never drift apart again.

**No design-scale `font-size` declaration may live outside `tailwind.config.js`.**
`globals.css` carries exactly **one** `font-size` declaration, and it is not a scale step:
the `@media (max-width: 767px)` rule that pins `input`/`select`/`textarea` to 16px so iOS
Safari does not zoom the page on focus. That safeguard is the single permitted exception —
do not remove it, and do not add a second exception. If you need a new size, name it in the
scale. `designSystem.test.ts` enforces both halves of this rule.

**Width intents:** `max-w-content` (page shell) · `max-w-lead` (marketing lead copy) ·
`max-w-panel` (form/dialog/search column) · `max-w-narrow` (compact centred card) ·
`max-w-hero-col` (landing hero column).
**Public section rhythm:** `.section-hero` (opening band) · `.section-band` (standard) ·
`.section-band-lg` (the marketing home's roomier band). Never a bare `py-N` on a
`<section>` of a public page.
Prominent money uses `text-title` / `h-display` with `tabular-nums`.

**Shape / depth**: cards = `.card` (rounded-card + border-line + bg-surface). Pills =
`rounded-pill`. Popovers = `shadow-pop`. Keep borders soft, shadows subtle.

**Capitalization**: never `.toUpperCase()` a translated string. ALL-CAPS labels only via
the `.kicker` utility (which uppercases visually, not in the data).

---

## 3. CANONICAL COMPONENTS — use these, never hand-roll

| Need | Use | Import |
|---|---|---|
| Page title + subtitle + primary action | `PageHeader` (desktop, in `hidden md:block`) + `MobileHeader` (in `md:hidden`) | `@/components/ui/PageHeader`, `@/components/mobile/primitives` |
| Search box | `SearchInput` | `@/components/ui/SearchInput` |
| Filter trigger (with count) | `FilterButton` inside `FilterBar` | `@/components/ui/FilterDrawer` |
| Filter panel | `FilterDrawer` + `RadioGroup`/`CheckGroup`/`DateRangeGroup`/`RangeGroup` | `@/components/ui/FilterDrawer` |
| Applied-filter chips | `ActiveFilters` (render right after `FilterBar`) | `@/components/ui/FilterDrawer` |
| Desktop list | `DataTable` (`onRowClick` → detail) | `@/components/ui/DataTable` |
| Mobile list | `RowCard` | `@/components/ui/RowCard` |
| Empty / no-result | `EmptyState` (distinct no-data vs no-result, §7) | `@/components/ui/EmptyState` |
| Centered dialog / form | `Modal` + `Field` + `inputCls`/`inputErrCls` | `@/components/ui/Modal` |
| Record detail panel | `DetailDrawer` + `DetailSection`/`DetailRow`/`DangerZone` | `@/components/ui/DetailDrawer` |
| Dropdown / picker | `Select` (custom combobox — **never native `<select>`**) | `@/components/ui/Select` |
| Money field | `MoneyInput` | `@/components/ui/FormFields` |
| Phone / email / percent / long text | `PhoneInput` / `EmailInput` / `PercentInput` / `Textarea` | `@/components/ui/FormFields` |
| Toggle | `Switch` | `@/components/ui/Switch` |
| Status label | `StatusBadge` / `Badge` | `@/components/ui/Badge` |
| Button | `Button` (variants: `primary` `outline` `ghost` `soft` `inverse` `danger`) | `@/components/ui/Button` |
| Row actions (2+ real actions only) | `RowActionMenu` (portaled) | `@/components/ui/RowActionMenu` |
| Loading | `SkeletonList` (lists) / `LoadingSplash` (boot) | `@/components/ui/Skeleton`, `LoadingSplash` |
| Error | `ErrorCard` | `@/components/ui/ErrorCard` |

**NEVER:**
- ❌ a native `<select>` — use `Select`.
- ❌ a raw `<input>` for money — use `MoneyInput`.
- ❌ a hand-rolled `<div className="card text-center">` empty block — use `EmptyState`.
- ❌ a custom filter button, a custom kebab, a second spinner.
- ❌ a 3-dot menu whose only action is "open detail" — make the row clickable + add a
  trailing `ChevronRight`. Reserve `RowActionMenu` for rows with **2+ real** actions, and
  even then prefer hosting edit/delete in the detail surface.

---

## 4. ICON REGISTRY — one icon per action (lucide-react)

Same action ⇒ same icon, everywhere. Do not pick a different glyph for the same meaning.

**Icon SIZE is part of the contract too.** The product had drifted to 18 distinct
`size={…}` values, with 14/15/16 all appearing on the same kind of toolbar button —
drift, not hierarchy. Control icons use exactly these six steps:

| `size` | Role |
|---|---|
| `12` | dense meta, inline chips |
| `14` | paired with body text |
| `16` | **default** — control and action icons |
| `18` | bottom-nav tabs |
| `20` | emphasis, section marks |
| `24` | feature marks, empty states |

Anything `>= 28` is an **illustration** dimension chosen per composition, not a control
step. Never introduce a seventh control size: pick the nearest role above.

| Action | Icon | Notes |
|---|---|---|
| Filter | `SlidersHorizontal` | only via `FilterButton` |
| Search | `Search` | only via `SearchInput` |
| Create / Add | `Plus` | `Button variant="primary"` |
| Open / drill-in | `ChevronRight` | trailing, `text-muted` |
| Edit | `Pencil` | in detail surface |
| Delete | `Trash2` | `Button variant="danger"` / destructive |
| Share | `Share2` | Telegram share (§9) |
| Copy | `Copy` → `CheckCircle2` on success | |
| Close | `X` | |
| Balance / money | `Wallet` | |
| Success | `CheckCircle2` · Warning `AlertTriangle` · Error `AlertCircle` · Info `Info` | |
| Back | **Telegram native BackButton** | never a custom web back chip inside TG |

---

## 5. LANGUAGE & i18n — zero hardcoded, zero mixed

The product ships **uz** + **ru**. Mixed-language UI (e.g. "Limitlar va overage", "Hard",
"Broadcast / oy") is a defect.

- ✅ Every user-visible string: `t('namespace.key', 'uz fallback')` with a matching entry
  in **both** `locales/uz.json` and `locales/ru.json`.
- ❌ No English words baked into Uzbek/Russian UI. Translate domain terms:
  `overage → "limitdan oshgani / qoʻshimcha haq"`, `hard → "qatʼiy (bloklash)"`,
  `soft → "yumshoq (haq olish)"`, `broadcast → "eʼlon"`. Loanwords that are genuinely
  standard in Uzbek business speech (`tarif`, `limit`, `balans`) are allowed — English
  technical jargon is not.
- ❌ Never concatenate translated fragments to build a sentence; use one key with
  interpolation (`t('k', { count })`).
- Labels that are proper nouns / payment brands (Payme, Click, Telegram) stay as-is.
- When you add a key, add it to **both** locale files. When you find a hardcoded or mixed
  string while working, fix it — do not walk past it.

---

## 6. FORMS & INPUTS — they must never crash or accept garbage

- **Money**: `MoneyInput` (groups thousands "1 000 000", caps 12 digits, clamps to a max).
  Never a raw number input — a 40-digit paste must be impossible at the input, not caught
  by an error screen afterward.
- **Phone**: `PhoneInput` (+998 locked). **Email**: `EmailInput` (typo-correct). **Percent**:
  `PercentInput`. **Long text**: `Textarea` (counter + hard cap).
- **Validation**: inline via `Field error=` + `focusFirstInvalid()` / `hasErrors()` from
  `@/lib/forms`. Block submit, keep entered values, show the error under its field.
- **Never** let a bad value reach the API and surface as the generic RouteErrorBoundary
  ("Nimadir notoʻgʻri ketdi"). That screen is for unexpected crashes only — validation and
  API errors are caught and shown as inline errors or toasts.
- **Submit states**: disabled while invalid/pending, spinner while pending, optimistic only
  when safe. Guard double-submit (`useSubmitGuard`).

---

## 7. LISTS, TABLES & EMPTY STATES

Every list page is composed the same way:
`PageHeader (title + primary action)` → `FilterBar (SearchInput + FilterButton)` →
`ActiveFilters (chips)` → loading `SkeletonList` / error `ErrorCard` / empty `EmptyState`
→ desktop `DataTable` (`onRowClick` → detail, trailing `ChevronRight`) + mobile `RowCard`.

**Empty states are two distinct things:**
- **No data yet** (first run): `tone="accent"`, encouraging copy, a primary CTA to create.
- **No result** (search/filter active): default tone, "nothing matches", an outline
  **Tozalash** button that clears filters. Never show the same block for both.

---

## 8. OVERLAYS

- `Modal` / `DetailDrawer` / `Select` dropdown / `RowActionMenu` all **portal to body**,
  trap focus, close on Esc + click-outside, and return focus to the trigger.
- Dropdowns must never be clipped by a scroll/overflow container (that is why `Select` and
  `RowActionMenu` are portaled — keep it that way).
- Sticky modal footers hold the primary/secondary actions; primary is rightmost.
- Destructive actions live in a `DangerZone` and ask for confirmation (`confirmDialog`).

---

## 9. TELEGRAM-NATIVE CONSTRAINTS

- Do **not** recreate or overlap Telegram's Close / Back / ⋯ controls. Use the native
  **BackButton** (hooked already); on desktop TG, `PageHeader` owns the title and
  `MiniAppHeader` is hidden (`md:hidden`) so titles are never duplicated.
- Share = Telegram-native share (`shareViaTelegram` / `openTelegramLink('https://t.me/share/url?...')`),
  with Web Share / clipboard fallback. Never an `<a target="_blank">` "open my own link".
- Lazy routes are wrapped in `lazyWithRetry` (stale-chunk recovery in the TG WebView). Keep
  it — never go back to bare `lazy()`.
- Respect `--tg-safe-*` insets for sticky headers/footers.

---

## 10. RESPONSIVE — check every size, every time

- Mobile-first phone layout (RowCards, MobileHeader, bottom nav) AND desktop (≥`md`:
  sidebar, PageHeader, DataTable). A change is not done until both are verified.
- Desktop content lives in the ~1280–1440 max-width container the layout already provides —
  do not add a second narrow max-width that re-creates the "prototype with huge margins" look.
- Test at ~360 / 768 / 1024 / 1440. Watch button positions, truncation, wrapping, sticky
  bars, and that overlays are not clipped.

---

## 11. DEFINITION OF DONE

1. Reuses shared components + the icon registry; no hand-rolled duplicates.
2. No hardcoded / mixed-language strings; uz + ru both updated.
3. Inputs are typed + validated; nothing reaches RouteErrorBoundary from user input.
4. Verified at mobile **and** desktop; overlays not clipped; Telegram-safe.
5. `typecheck` + `lint` + `test` + `build` green.
6. If any screen still feels like a prototype, keep refining — that is the bar.
