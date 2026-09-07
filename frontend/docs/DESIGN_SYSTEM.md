# Muddato — UI/UX Design System

The single standard for every page. Goal: one unified, enterprise-grade, read-first
admin product across desktop and mobile. When in doubt, **show data first, actions second.**

> Status: living document. Foundation (tokens, type scale, primitives) is in place;
> page-by-page enforcement is tracked in [UI_AUDIT.md](./UI_AUDIT.md).

---

## 1. Typography

One scale. **Never** use ad-hoc `text-[Npx]`. Use the tokens (inline text) or the
`.h-*` classes (headings, which also bundle weight + tracking).

| Purpose | Token / class | Size (px) | Weight |
|---|---|---|---|
| KPI / hero number | `.h-display` | 26 → 32 → 40 (responsive) | 600 |
| Page title (`<h1>`) | `.h-page` | 28 → 34 | 600 |
| Section / modal title (`<h2>`) | `.h-section` / `text-section` | 20 | 600 |
| Sub-header (`<h3>`) | `.h-sub` / `text-subsection` | 16 | 600 |
| Body (default) | `text-body` | 14 | 400/500 |
| Secondary / dense | `text-small` | 13 | 400 |
| Hint / caption | `text-caption` | 12 | 400 |
| Micro-label / kicker | `text-micro` / `.kicker` | 11 | 500 |

Fonts: **Inter Tight** (sans), **JetBrains Mono** (numerals in data cells via `tabular-nums`).
Capitalization for ALL-CAPS micro-labels comes **only** from the `.kicker` class — never
bake uppercase into a stored string or `.toUpperCase()` a translated value.

## 2. Color (already tokenized — do not hardcode hex)

Semantic CSS vars only: `ink` / `ink-soft` / `muted` (text), `cream` (page bg),
`surface` (cards/modals), `line` (hairlines), `accent` (brand orange) + `accent-strong`
(AA text) / `accent-fill` (AA button), and `success` / `warn` / `danger` / `info` each
with `-soft` (tint) + `-strong` (AA text). All meet WCAG AA for their documented use.

## 3. Buttons (`<Button>`)

| Variant | Use | Notes |
|---|---|---|
| `primary` (orange) | **The** create/save/send action | One per page/section. This is the standard Add button. |
| `outline` | Secondary (Filter, Cancel, Export, secondary CTA) | |
| `ghost` / `soft` | Low-priority / inline | |
| `danger` | Destructive (Delete/Remove) only | Use the variant, not a `className="bg-danger"` override. |
| `inverse` (dark) | Marketing CTAs only | Not for admin pages. |

Sizes `sm`/`md`/`lg` = 36/40/48px. Icon-only buttons need `aria-label` + `.tap-target`
(44px on touch). Use `loading` for async — don't hand-roll spinners.

## 4. Page structure — READ-FIRST (the core rule)

Every list/data page, top to bottom:

```
PageHeader:  [Title (sentence case)]                         [+ Primary action]
Toolbar:     [Search .............................] [Filtr]   (+ optional sort/view)
(optional)   [ quick-filter chips — segmentation, distinct from the Filter button ]
Main:        [ Table (desktop) / Cards (mobile) — the focus ]
States:      empty (helpful + CTA) · loading (skeleton) · error (retry)
Row actions: open detail (DetailModal) / kebab — Edit, Delete(confirm) inside
```

**Action placement** (desktop): Add in the header right; Search + Filter together below.
Add is never between Search and Filter, never inside the filter, never below the list.
Mobile: title + compact Add in the header; full-width search; Filter opens a sheet.

**Create flow:** never a compose form above the list. Header "+ New" → **modal/drawer**
(medium forms) or a dedicated `/new` page (complex forms). See `tenant/BroadcastsPage`
and `platform/BroadcastsPage` as the reference.

## 5. Shared component kit (use these — don't re-roll)

- **Header:** `PageHeader` (desktop, in `hidden md:block`) + `MobileHeader` (`md:hidden`) — see PLATFORM_STANDARD.md §3, the authoritative reference.
- **Toolbar / filters:** `FilterBar` + `FilterButton` + `FilterDrawer` (+ `RadioGroup`,
  `CheckGroup`, `DateRangeGroup`, `RangeGroup`). Active-count badge + Clear.
- **Lists:** `DataTable` (desktop, dense, sortable) → `RowCard` (mobile). One row =
  primary id · secondary metadata · status badge · date · actions.
- **Status:** `StatusBadge` (maps `status.*` → tone). Never a local one-off pill.
- **Modal:** `Modal` (focus-trap, Esc, scroll-lock, mobile bottom-sheet) + `Field` +
  `inputCls`/`inputErrCls`. Confirm/destructive: `confirmDialog({ danger: true })`.
- **States:** `Skeleton`/`SkeletonList` (loading), `ErrorCard` (error + retry),
  `EmptyState` (empty). KPIs: `KpiCard`.

## 6. Modals & destructive actions

Edit modal: title "Edit …" · fields · footer `[Cancel] [Save changes]`.
Delete: `confirmDialog({ danger: true })` — names the record, says "cannot be undone",
`[Cancel]` left / destructive action right. Prefer **Archive** over Delete when recoverable.

## 7. Responsive

Breakpoints: `sm 640 · md 768 · lg 1024 · xl 1280`. Page max-width `1480px`.
Test 360 → 1440. No horizontal overflow except intentional data tables (which must
fall back to cards on mobile — no naked `<table overflow-x-auto>`). Touch targets ≥44px.

## 8. Accessibility (baseline already enforced globally)

Keyboard focus rings (`:focus-visible`, WCAG 2.2), 44px touch targets on coarse pointers,
iOS-zoom-safe inputs (≥16px), reduced-motion honored. Per component: `aria-label` on
icon-only buttons, modals trap focus, status never communicated by color alone (badge text).

## 9. Naming / capitalization

Store every nav item, title, section header, and button label in i18n in **sentence case**
(first word capitalized; proper nouns/acronyms as-is — SMS, SKU, IMEI, LTV, UZS, PDF…).
Never `.toUpperCase()` a translated string. Reuse the canonical `common.*` action labels
(add/save/cancel/delete/edit/filter/search) instead of per-page duplicates.
