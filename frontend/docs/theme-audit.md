# Light / dark theme parity audit

Owner report: after switching themes, parts of the dashboard stayed in the old theme.
This file is the inventory required by planchanges4 F0.3.5 (newplan §15.2a). Every PR that
adds or touches a page or shared component adds or updates its row here.

## How each row was checked

- **Light / Dark**: every surface, text, border, chart series, axis, grid, tooltip and legend
  uses a theme token (`bg-surface-*`, `text-fg*`, `border-line*`, `status-*`, `brand-*` backed by
  CSS variables) or a `light` + `dark:` class pair, or reads `useChartTheme()`. Contrast of
  every token pair is asserted in `tests/lib/theme-tokens.test.ts` (AA).
- **Toggle**: Light -> Dark -> Light with no reload. Charts re-render through `useChartTheme()`
  (observes the `dark` class); CSS-variable tokens switch with the class; nothing is computed
  once at mount.
- **Guard**: `npm run lint` (ESLint `no-restricted-syntax`) and `tests/lib/theme-guard.test.ts`
  fail on any hex colour, `bg-white` without `dark:bg-*`, or `text-gray-*` without `dark:text-*`
  outside `lib/theme-tokens.ts`.
- **Screenshot**: light and dark screenshots at 1366 px and 1920 px are attached to the PR by a
  person (no Playwright in this repo). `[ ]` = still to attach.

Legend: **Pass** = verified in code and tests; **Fixed** = was broken, fixed in PR-15 (cause in brackets).

## Root causes found (newplan §15.2a)

| # | Cause | Fix in PR-15 |
|---|---|---|
| T1 | 50 chart hex colours in Recharts props (plus white `rgba(255,255,255,.95)` tooltips) never changed with the theme | `chartTheme` in `lib/theme-tokens.ts` + `useChartTheme()` / `chartTooltipStyle()`; every chart migrated |
| T2 | Surfaces with no dark variant: `bg-white` / `text-gray-*` / `text-slate-900` in conversation detail, Monitoring, root page, OutcomePillBar, document modal, user not-found, table badges | Semantic tokens (`bg-surface-card`, `text-fg`, ...) that switch via CSS variables |
| T3 | `ThemeProvider` set the `dark` class in `useEffect`, after first paint (flash) | Inline pre-paint script in `app/layout.tsx` `<head>` + `suppressHydrationWarning`; provider no longer overrides it before reading storage |
| T4 | No `storage` listener (tabs drifted), no System mode | `storage` event sync, `light` / `dark` / `system` modes, live `matchMedia` change listener; `ThemeToggle` cycles Light -> Dark -> System |
| T5 | Single logo asset with `dark:brightness-110` | `BrandLogo` light/dark pair; new `exigotech-logo-dark.svg` and `exigotech-mark-dark.svg` |
| T6 | Amber `#B7791F` and red `#C0362C` too dark on dark surfaces | Separate lifted dark status tokens (`#F2B84B`, `#F87171`, ...) and `*-fg` text variants, all AA-tested |

## Pages

| Route | Light | Dark | Toggle | Notes | Screenshot L/D |
|---|---|---|---|---|---|
| `/` (redirect spinner) | Pass | Fixed (T2) | Pass | `bg-white` -> `bg-surface-page` | [ ] |
| `/chat` | Pass | Pass | Pass | Page bg token; sample questions and product name from brand | [ ] |
| `/documents` | Pass | Fixed (T2) | Pass | Doc name `text-slate-900`, result banner, re-ingest modal tokenised | [ ] |
| `/documents/[id]` | Pass | Fixed (T2) | Pass | Neutral type/strategy badges tokenised; coloured badges are light chips in both themes (readable) | [ ] |
| `/admin` (Dashboard) | Pass | Fixed (T1) | Fixed (T1) | Query volume, engagement donut, pipeline latency, RAG stage dots and share bar all from `chartTheme`; custom tooltip uses `bg-surface-elevated` | [ ] |
| `/admin/users` | Pass | Pass | Pass | Page bg token | [ ] |
| `/admin/users/[id]` | Pass | Fixed (T2) | Pass | "User not found" card tokenised | [ ] |
| `/admin/conversations` | Pass | Pass | Pass | Page bg token | [ ] |
| `/admin/conversations/[id]` | Pass | Fixed (T2) | Pass | All `text-gray-*`, `bg-white`, `border-gray-*` replaced; purple accents -> brand | [ ] |
| `/admin/feedback` | Pass | Pass | Pass | Page bg token | [ ] |
| `/admin/usage` (Overview, Users, Sessions, Queries tabs) | Pass | Fixed (T1, T2) | Fixed (T1) | Token rings, gauges, needles and legend dots from `chartTheme`; tab headings and chips tokenised | [ ] |
| `/admin/evaluation` = `/evaluation` | Pass | Fixed (T1) | Fixed (T1) | Radar, outcome pie, pass-rate and latency bars, tooltips from `chartTheme` | [ ] |
| `/admin/evaluation-live` = `/evaluation-live` | Pass | Fixed (T1) | Fixed (T1) | As above; white tooltips fixed; compare matrix radar migrated | [ ] |
| `/admin/governance` (tabs) | Pass | Pass | Pass | Page bg token | [ ] |
| `/admin/governance/monitoring` | Pass | Fixed (T1, T2) | Fixed (T1) | Whole view was light-only; now tokens + `chartTheme` | [ ] |
| `/admin/governance/logs` | Pass | Fixed (T2) | Pass | Default level badge tokenised | [ ] |
| `/admin/governance/traces` | Pass | Pass | Pass | No change needed | [ ] |
| `/_not-found` (`app/not-found.tsx`) | Pass | Pass | Pass | Branded 404 (PR-17): `BrandLogo` pair, `bg-surface-*` / `text-fg*` tokens | [ ] |
| Route error boundary (`app/error.tsx`) | Pass | Pass | Pass | Branded (PR-17): reference digest + copy, retry; no error text shown | [ ] |
| Any `(app)` route at < `md` (390 px) | Pass | Pass | Pass | PR-17: `MobileTopBar` + off-canvas sidebar; desktop aside `hidden md:flex` | [ ] |
| `/admin/settings` (Branding, Insights Taxonomy tabs) (PR-26) | Pass | Pass | Pass | Cards `bg-surface-card` + `border-line`; inputs, badges and table on tokens; superadmin only | [ ] |
| `/login` | n/a | n/a | n/a | Route not built yet (F1, PR-19); must use `BrandLogo` | n/a |

## Shared components

| Component | Light | Dark | Toggle | Notes |
|---|---|---|---|---|
| `Sidebar` | Pass | Fixed (T5) | Pass | `BrandLogo` in an `h-16` header (`h-10 w-auto max-w-[200px]`, ~160 px wide) |
| `brand/BrandLogo` (new) | Pass | Pass | Pass | CSS-only swap (`dark:hidden` / `hidden dark:block`), no flash |
| `ThemeToggle` | Pass | Pass | Pass | Light -> Dark -> System; icon crossfade via Motion preset |
| `providers/MotionProvider` (new) | n/a | n/a | n/a | Same motion in both themes; `reducedMotion="user"` |
| `ChatSessionList` | Pass | Pass | Pass | Existing `dark:` pairs |
| `MessageBubble` | Pass | Pass | Pass | Existing `dark:` pairs |
| `admin/DashboardHeader` | Pass | Pass | Pass | Existing `dark:` pairs |
| `admin/AudienceScopeTabs` | Pass | Pass | Pass | Existing `dark:` pairs |
| `admin/TabGroup` | Pass | Fixed (T2) | Pass | Inactive count badge tokenised |
| `admin/DataTable` | Pass | Pass | Pass | Existing `dark:` pairs |
| `admin/TableCard` | Pass | Pass | Pass | `bg-surface-card` |
| `admin/TableToolbar` | Pass | Pass | Pass | Existing `dark:` pairs |
| `admin/TableFormatters` | Pass | Pass | Pass | Existing `dark:` pairs |
| `admin/Pagination` | Pass | Pass | Pass | Existing `dark:` pairs |
| `admin/QueryInspectorDrawer` | Pass | Pass | Pass | `bg-surface-card` |
| `admin/usage/QueryInspectorDrawer` | Pass | Pass | Pass | PR-17: chrome now from `ui/DrawerShell` (shared with the evaluation inspector); body keeps its `dark:` pairs; errors use `ErrorState` |
| `admin/EvaluationCompareMatrix` | Pass | Fixed (T1) | Fixed (T1) | Radar + tooltip from `chartTheme` |
| `admin/insights/StatCard` | Pass | Pass | Pass | `bg-surface-card` |
| `admin/insights/HighlightCard` | Pass | Pass | Pass | `bg-surface-card` |
| `admin/insights/AccuracyBadge` | Pass | Pass | Pass | Existing `dark:` pairs |
| `admin/insights/OutcomePillBar` | Pass | Fixed (T2) | Pass | Pills get dark tints; "All" pill tokenised |
| `admin/insights/TokenDistributionBar` | Pass | Fixed (T2) | Pass | Track and label tokenised |
| `admin/usage/InteractiveOverviewTab` | Pass | Fixed (T1) | Fixed (T1) | See `/admin/usage` |
| `admin/usage/OverviewTab` | Pass | Pass | Pass | Existing `dark:` pairs |
| `admin/usage/UsersTab` | Pass | Fixed (T2) | Pass | Detail heading tokenised |
| `admin/usage/SessionsTab` | Pass | Fixed (T2) | Pass | Detail heading tokenised |
| `admin/usage/QueriesTab` | Pass | Fixed (T2) | Pass | Neutral chips tokenised |
| `admin/usage/TestIpManager` | Pass | Fixed (T2) | Pass | Cancel button tokenised |
| `documents/FormatBadge` | Pass | Fixed (T2) | Pass | Default badge tokenised |
| `charts/ChartCard` (new, PR-16) | Pass | Pass | Pass | Replaces the local cards in Dashboard (`AuroraChartCard`), Monitoring, Evaluation and Evaluation Live; `plain` / `aurora` variants on `surface-*`, `line`, `brand-*` tokens; skeleton, error, empty and "View as table" states use tokens |
| `charts/Sparkline`, `charts/KpiTrendCard` (new) | Pass | Pass | Pass | SVG colours from `useChartTheme()`; delta badge on `status-*-bg` / `status-*-fg` |
| `charts/StackedArea`, `PercentileBand`, `Histogram` (new) | Pass | Pass | Pass | Series `chartTheme.categorical`, grid `chartTheme.grid`, axis `chartTheme.axis`; legend hover dims to `dimOpacity` |
| `charts/Waterfall`, `BulletChart`, `Funnel` (new) | Pass | Pass | Pass | Bars from `categorical` / `status` / `sequential`; ranges on `chartTheme.track` |
| `charts/CalendarHeatmap` (new) | Pass | Pass | Pass | Cells on the sequential ramp (separate light and dark ramps) |
| `charts/RadialGauge` (new) | Pass | Pass | Pass | Track `chartTheme.track`; value tone from `chartTheme.status` (lifted dark values, T6) |
| `charts/QuadrantScatter`, `charts/Radar` (new) | Pass | Pass | Pass | Quadrant dots from `status`; radar series from `categorical` |
| `charts/TreemapChart`, `charts/SankeyChart` (new, `next/dynamic`) | Pass | Pass | Pass | Recharts wrappers fed from `useChartTheme()`; skeleton while the chunk loads |
| Chart tooltip (`charts/primitives`) | Pass | Pass | Pass | Card on `bg-surface-elevated` + `border-line` + `shadow-md`; focus ring `.chart-mark:focus-visible` uses `--brand-500` |
| `ui/DrawerShell` (PR-17) | Pass | Pass | Pass | Portal drawer: `bg-surface-elevated`, `border-line`, backdrop `bg-ink/40` / `dark:bg-black/60` |
| `ui/Modal`, `ui/ConfirmDialog` (PR-17) | Pass | Pass | Pass | Same tokens as the drawer; danger tone uses `status-danger` / `status-danger-bg` (dark text on the lifted dark red) |
| `ui/ToastProvider` (PR-17) | Pass | Pass | Pass | `bg-surface-elevated` cards, tone icons from `status-*` |
| `ui/EntityLink`, `ui/CopyButton` (PR-17) | Pass | Pass | Pass | Links `brand-700` / `dark:brand-300`; tooltip `bg-surface-inverse` + `text-fg-inverse` (flips per theme) |
| `ui/EmptyState`, `ui/ErrorState`, `ui/Skeleton` (PR-17) | Pass | Pass | Pass | `surface-muted` placeholders; pulse off under reduced motion |
| `ui/DateRangePicker` (PR-17) | Pass | Pass | Pass | Popover + native date inputs on `bg-surface-card`; active preset brand tint |
| `ui/CommandPalette`, `ui/ShortcutsSheet` (PR-17) | Pass | Pass | Pass | Selected row `brand-50` / `dark:brand-950/60`; `kbd` chips `surface-muted` |
| `ui/ActivePill` (in `TabGroup`, `AudienceScopeTabs`) (PR-17) | Pass | Pass | Pass | Sliding `bg-brand-gradient` pill (layoutId); same in both themes |
| `shell/ProfileMenu`, `shell/NotificationBell`, `shell/HeaderUtilities` (PR-17) | Pass | Pass | Pass | Popovers on `surface-elevated`; theme radio (light / dark / system) applies instantly |
| `shell/MobileTopBar`, `Sidebar` (mobile drawer) (PR-17) | Pass | Pass | Pass | `bg-surface-card`, `border-line`; nav items reuse the desktop classes |
| `shell/BrandedStatusPage` (PR-17) | Pass | Pass | Pass | `BrandLogo` pair on `bg-surface-card` |
| `admin/CursorPagination` (PR-17) | Pass | Pass | Pass | Mirrors `Pagination` (existing `dark:` pairs) |
| `admin/TableToolbar` (columns, density) (PR-17) | Pass | Pass | Pass | Column menu on `surface-elevated`; buttons reuse the existing `dark:` pairs |
| `settings/BrandPreview` (PR-26) | Pass | Pass | Pass | Light and dark panes side by side, each pinned to its theme with inline semantic + draft `--brand-*` variables (independent of the page theme); `BrandLogo forceTheme` |
| `settings/ColorField`, `ContrastBadge` (PR-26) | Pass | Pass | Pass | Badge `status-ok-bg`/`status-ok-fg` or `status-danger-*`; swatches render the chosen colour (data, not tokens) |
| `settings/AssetDropzone` (PR-26) | Pass | Pass | Pass | Dashed `border-line-strong`, hover lift + brand border; dark artwork previewed on `bg-surface-inverse` |
| `settings/TaxonomyTab` table and add form (PR-26) | Pass | Pass | Pass | `surface-muted` header, archived rows at 60 % opacity, status pills on `status-*` tokens |
| `brand/BrandLogo` live update (PR-26) | Pass | Pass | Pass | Saving branding re-renders logos and rewrites every `--brand-*` variable without a reload |

## Known follow-ups (not theme-breaking)

- Coloured status chips (`bg-emerald-50 text-emerald-700` and similar) render as light chips in
  dark mode. They stay readable (AA) but should move to the `status-*-bg` / `status-*-fg` tokens
  when their components are next touched (F3 to F6).
- Decorative glows still use `rgba(...)` drop-shadows on the dark hero panels; they are
  theme-neutral.
- The CSP nonce for the pre-paint script is added by PR-18 (middleware); until then the script is
  a plain inline script.
