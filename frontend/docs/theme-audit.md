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
| `/admin/users` ("Visitors", PR-20) | Pass | Pass | Pass | Page bg token; header and nav label renamed to Visitors, no style change | [ ] |
| `/admin/users/[id]` | Pass | Fixed (T2) | Pass | "User not found" card tokenised | [ ] |
| `/admin/conversations` | Pass | Pass | Pass | Page bg token | [ ] |
| `/admin/conversations/[id]` | Pass | Fixed (T2) | Pass | All `text-gray-*`, `bg-white`, `border-gray-*` replaced; purple accents -> brand | [ ] |
| `/admin/feedback` | Pass | Pass | Pass | Page bg token | [ ] |
| `/admin/usage` (Overview, Users, Sessions, Queries tabs) | Pass | Fixed (T1, T2) | Fixed (T1) | Token rings, gauges, needles and legend dots from `chartTheme`; tab headings and chips tokenised. PR-20: Overview is now Live Telemetry (`InteractiveOverviewTab`, Sparkles + Live badge; active badge `bg-white/20`, inactive `bg-surface-muted text-fg-muted`) | [ ] |
| `/admin/evaluation` = `/evaluation` | n/a | n/a | n/a | PR-20: server `redirect()` (307) to `/admin/evaluation-live`; nothing rendered. Old report kept unmounted as `admin/evaluation/LegacyEvaluationReport` | n/a |
| `/admin/evaluation-live` = `/evaluation-live` | Pass | Fixed (T1) | Fixed (T1) | As above; white tooltips fixed; compare matrix radar migrated | [ ] |
| `/admin/governance` (tabs) | Pass | Pass | Pass | Page bg token | [ ] |
| `/admin/governance/monitoring` | Pass | Fixed (T1, T2) | Fixed (T1) | Whole view was light-only; now tokens + `chartTheme` | [ ] |
| `/admin/governance/logs` | Pass | Fixed (T2) | Pass | Default level badge tokenised | [ ] |
| `/admin/governance/traces` | Pass | Pass | Pass | No change needed | [ ] |
| `/_not-found` (Next default) | Pass | Pass | Pass | Branded 404 is F0.6.6 (PR-17) | [ ] |
| `/login` | n/a | n/a | n/a | Route not built yet (F1, PR-19); must use `BrandLogo` | n/a |

## Shared components

| Component | Light | Dark | Toggle | Notes |
|---|---|---|---|---|
| `Sidebar` | Pass | Fixed (T5) | Pass | `BrandLogo` in an `h-16` header (`h-10 w-auto max-w-[200px]`, ~160 px wide). PR-20: "Evaluation" removed, "Users" -> "Visitors"; collapsed rail shows the Sparkles icon for Evaluation (Live) with its label as the `title` tooltip (same tokens in both themes) |
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
| `admin/usage/QueryInspectorDrawer` | Pass | Pass | Pass | Existing `dark:` pairs (duplicate removed in PR-17) |
| `admin/EvaluationCompareMatrix` | Pass | Fixed (T1) | Fixed (T1) | Radar + tooltip from `chartTheme` |
| `admin/evaluation/LegacyEvaluationReport` (moved, unmounted) | Pass | Fixed (T1) | Fixed (T1) | Former `/evaluation` page body, unchanged; not routed (PR-20) |
| `admin/insights/StatCard` | Pass | Pass | Pass | `bg-surface-card` |
| `admin/insights/HighlightCard` | Pass | Pass | Pass | `bg-surface-card` |
| `admin/insights/AccuracyBadge` | Pass | Pass | Pass | Existing `dark:` pairs |
| `admin/insights/OutcomePillBar` | Pass | Fixed (T2) | Pass | Pills get dark tints; "All" pill tokenised |
| `admin/insights/TokenDistributionBar` | Pass | Fixed (T2) | Pass | Track and label tokenised |
| `admin/usage/InteractiveOverviewTab` | Pass | Fixed (T1) | Fixed (T1) | See `/admin/usage` |
| `admin/usage/UsersTab` | Pass | Fixed (T2) | Pass | Detail heading tokenised |
| `admin/usage/SessionsTab` | Pass | Fixed (T2) | Pass | Detail heading tokenised |
| `admin/usage/QueriesTab` | Pass | Fixed (T2) | Pass | Neutral chips tokenised |
| `admin/usage/TestIpManager` | Pass | Fixed (T2) | Pass | Cancel button tokenised |
| `documents/FormatBadge` | Pass | Fixed (T2) | Pass | Default badge tokenised |

## Known follow-ups (not theme-breaking)

- Coloured status chips (`bg-emerald-50 text-emerald-700` and similar) render as light chips in
  dark mode. They stay readable (AA) but should move to the `status-*-bg` / `status-*-fg` tokens
  when their components are next touched (F3 to F6).
- Decorative glows still use `rgba(...)` drop-shadows on the dark hero panels; they are
  theme-neutral.
- The CSP nonce for the pre-paint script is added by PR-18 (middleware); until then the script is
  a plain inline script.
