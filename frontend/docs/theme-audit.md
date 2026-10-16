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
| T4 | No `storage` listener (tabs drifted) | `storage` event sync. Two themes only, `light` / `dark` (System mode removed): a missing or legacy `system` value is resolved once to the OS preference and persisted as an explicit `light` / `dark` (pre-paint script and provider), so the theme never changes on its own; `ThemeToggle` switches Light <-> Dark |
| T5 | Single logo asset with `dark:brightness-110` | `BrandLogo` light/dark pair; new `exigotech-logo-dark.svg` and `exigotech-mark-dark.svg` |
| T6 | Amber `#B7791F` and red `#C0362C` too dark on dark surfaces | Separate lifted dark status tokens (`#F2B84B`, `#F87171`, ...) and `*-fg` text variants, all AA-tested |
| T7 | Owner (Oct 2026): only the Usage "Live Telemetry" cards looked right; other cards were flat near-black in dark | Studio look extracted into tokens (`surface-studio`, `bg-studio`, `shadow-studio` / `shadow-studio-hover`, per theme in `globals.css`) and `components/ui/StudioCard`; every shared card / chart / KPI / table container now uses it (see "Studio rollout") |
| T8 | Owner: tables "not good" in black: slate `dark:bg-dark-surface` headers and footers (#0F1720) inside near-black cards, slate borders | Dark neutrals (`surface-card/muted/elevated`, `line`, `line-strong`, `chart-grid`) and the legacy `dark-*` palette moved to brand-tinted near-black (asserted: green/blue > red); table header `bg-surface-studio`, zebra + brand hover rows, footers on tokens |
| T9 | `zinc` greys and `bg-slate-900` blocks in light mode (Usage stage breakdown) | Replaced with `fg*` / `chart-track` / studio tile tokens |

## Pages

| Route | Light | Dark | Toggle | Notes | Screenshot L/D |
|---|---|---|---|---|---|
| `/` (redirect spinner) | Pass | Fixed (T2) | Pass | `bg-white` -> `bg-surface-page` | [ ] |
| `/chat` | Pass | Pass | Pass | Page bg token; sample questions and product name from brand. PR-21: `chat/ChatHeader` (BrandLogo mark pair, `bg-surface-card`, `border-line`, `text-fg*`), user chip and "View trace" link on tokens | [ ] |
| `/documents` | Pass | Fixed (T2) | Pass | PR-22: top-cited bar (`StackedBar` horizontal), age vs citations `QuadrantScatter`, coverage `RadialGauge` from `chartTheme`; delete via `ConfirmDialog` (danger tokens); upload / delete hidden without `documents:write` | [ ] |
| `/documents/[id]` | Pass | Pass | Pass | PR-22: `DashboardHeader` + breadcrumb; chunk cards on `surface-card` / `line`, strategy chips on `status-*` tokens; hit heatmap on the sequential ramp; highlighted chunk `ring-brand-500/40` | [ ] |
| `/documents` inventory (owner fix) | Pass | Pass | Pass | Inventory `TableCard` (format / size / chunks / hash + copy / ingested / status chips on `status-*` tokens), KPI cards on `surface-card` / `line` with brand hover, rows fade up (`fadeUp`, reduced motion = opacity only) | [ ] |
| `/documents/[id]` tabs (owner fix) | Pass | Pass | Pass | Metadata card (`surface-card`, warnings on `status-warn-*`), `TabGroup` (Chunks / Canonical Markdown / Citations / History), Markdown rendered with `MarkdownView` (tokens only, no HTML injection), source view on `surface-muted` | [ ] |
| `/admin` (Dashboard) | Pass | Pass | Pass | PR-21 rebuild: KpiTrendCard grid, StackedArea, PercentileBand, BulletChart, RadialGauge, Treemap from `useChartTheme()`; activity feed and health pill on `status-*` / `surface-*` tokens; old teal/slate literals and the custom tooltip removed | [ ] |
| `/admin/conversations` | Pass | Pass | Pass | PR-22: turns `Histogram`, outcome 100 % `StackedBar`; preview `DrawerShell` (surface-elevated) | [ ] |
| `/admin/conversations/[id]` | Pass | Pass | Pass | PR-22: `DashboardHeader` + breadcrumb; turn cards on `surface-card` / `surface-muted` / `brand-50`+`dark:brand-950/40` (no `text-gray-*`); stage `Waterfall`, token `StackedBar`; highlighted turn ring `brand-500/40` | [ ] |
| `/admin/feedback` (PR-24 triage workspace: Overview, Review Queue, Golden Cases, Preference Data, Knowledge Gaps) | Pass | Pass | Pass | Page bg token; KPI / chart cards on `surface-card`; queue rows `border-line`, focused row `brand-50` / `dark:brand-950/40`; status and rating badges on `status-*-bg` / `status-*-fg`; spike alert `status-danger-bg` | [ ] |
| `/admin/usage` (Overview, Users, Sessions, Queries tabs) | Pass | Fixed (T1, T2) | Fixed (T1) | Token rings, gauges, needles and legend dots from `chartTheme`; tab headings and chips tokenised. PR-20: Overview is now Live Telemetry (`InteractiveOverviewTab`, Sparkles + Live badge; active badge `bg-white/20`, inactive `bg-surface-muted text-fg-muted`) | [ ] |
| `/admin/evaluation` = `/evaluation` | n/a | n/a | n/a | PR-20: server `redirect()` (307) to `/admin/evaluation-live`; nothing rendered. Old report kept unmounted as `admin/evaluation/LegacyEvaluationReport` | n/a |
| `/admin/evaluation-live` = `/evaluation-live` | Pass | Fixed (T1) | Fixed (T1) | As above; white tooltips fixed; compare matrix radar migrated. PR-21: quality Radar (current vs previous) and pass-rate LineTrend from `chartTheme` | [ ] |
| `/admin/governance` (tabs) | Pass | Pass | Pass | PR-23: tab in `?tab=`; header `bg-surface-page` / `text-fg`; DateRangePicker on Traces and Logs only | [ ] |
| `/admin/governance` Overall (PR-23) | Pass | Pass | Pass | Health banner animates between `status-*-bg` / `status-*` token values read per theme (re-renders on toggle); KPI cards, status timeline cells (`status-*` with alpha), incident rows (`status-warn-bg` highlight) | [ ] |
| `/admin/governance/monitoring` -> `?tab=Monitoring` | Pass | Pass | Pass | PR-23: route redirects to the tab. Stat tiles `surface-card` / `line`; Recharts bar from `useChartTheme()`; PercentileBand, StackedArea (errors `status.danger`), RadialGauges and share bars from `chartTheme` | [ ] |
| `/admin/governance/logs` -> `?tab=Logs` | Pass | Pass | Pass | PR-23: level badges and chips on `status-*-bg` / `status-*-fg` (replaces the emerald / rose / sky chips); JSON viewer `surface-muted` | [ ] |
| `/admin/governance/traces` -> `?tab=Traces` | Pass | Pass | Pass | PR-23: status and latency badges on `status-*` tokens; trace drawer on `DrawerShell`; waterfall from `chartTheme` | [ ] |
| `/admin/users` (Visitors) | Pass | Pass | Pass | PR-22: `StatCard` KPI strip, new vs returning `StackedBar`, weekday x hour `CalendarHeatmap`; filter chip `brand-50` / `dark:brand-950/60`; row actions `iconButton` | [ ] |
| `/admin/users/[id]` (Visitor detail) | Pass | Pass | Pass | PR-22: `DashboardHeader` + breadcrumb; activity timeline `Waterfall`, cost `KpiTrendCard` sparkline; feedback chips on `status-*` tokens | [ ] |
| `/_not-found` (`app/not-found.tsx`) | Pass | Pass | Pass | Branded 404 (PR-17): `BrandLogo` pair, `bg-surface-*` / `text-fg*` tokens | [ ] |
| Route error boundary (`app/error.tsx`) | Pass | Pass | Pass | Branded (PR-17): reference digest + copy, retry; no error text shown | [ ] |
| Any `(app)` route at < `md` (390 px) | Pass | Pass | Pass | PR-17: `MobileTopBar` + off-canvas sidebar; desktop aside `hidden md:flex` | [ ] |
| `/login` (PR-18) | Pass | Pass | Pass | Split screen (§7.7): left panel `from-brand-600 to-brand-800` gradient with white text in both themes (white on `brand-600` 4.8:1), white `BrandLogo tone="onDark"`, hexagons/rings `text-white/*` (currentColor); right panel `bg-surface-page` + `text-fg*`, button `bg-surface-card border-line-strong`, error banner `status-danger-*`. Below `md`: teal header strip + single column. Microsoft logo colours are fixed brand colours in `theme-tokens.ts` | [ ] |
| Access Required (any dashboard route, pending / rejected) (PR-18) | Pass | Pass | Pass | Card on `bg-surface-card`; stepper dots `brand-600` / `surface-card` / `status-danger-*`; rail `bg-line`; form fields `border-line bg-surface-card text-fg` | [ ] |
| `/suspended` and suspended gate (PR-18) | Pass | Pass | Pass | Same layout as `BrandedStatusPage`; icon on `status-danger-bg` | [ ] |
| `/admin/user-management` (Users, Access Requests, Roles & Permissions, Audit Trail tabs) (PR-19) | Pass | Pass | Pass | KPI cards and charts on `bg-surface-card` with `chartTheme` marks (bar, funnel, donut, histogram recolour on toggle, journey-tested); pending KPI `status-warn-bg` highlight; role / status / event chips on `status-*-bg` + `status-*-fg` and brand tints with `dark:` pairs | [ ] |
| `/admin/settings` (Branding, Insights Taxonomy tabs) (PR-26) | Pass | Pass | Pass | Cards `bg-surface-card` + `border-line`; inputs, badges and table on tokens; superadmin only | [ ] |
| `/admin/analytics` (new, PR-25: Guide, Overview, Trend, Flow, Topics, Drop-off, Conversations tabs) | Pass | Pass | Pass | Native port of the Chatbot Insights HTML. Filter bar, chips, KPI cards, tour and pending state on `surface-*` / `line` / `brand-*` / `status-info-*` tokens; outcome pills on `status-*-bg` / `*-fg` | [ ] |

## Shared components

| Component | Light | Dark | Toggle | Notes |
|---|---|---|---|---|
| `Sidebar` | Pass | Fixed (T5) | Pass | `BrandLogo` in an `h-16` header (`h-10 w-auto max-w-[200px]`, ~160 px wide). PR-20: "Evaluation" removed, "Users" -> "Visitors"; collapsed rail shows the Sparkles icon for Evaluation (Live) with its label as the `title` tooltip (same tokens in both themes) |
| `brand/BrandLogo` (new) | Pass | Pass | Pass | CSS-only swap (`dark:hidden` / `hidden dark:block`), no flash |
| `ThemeToggle` | Pass | Pass | Pass | Light <-> Dark; icon crossfade via Motion preset |
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
| `admin/evaluation/LegacyEvaluationReport` (moved, unmounted) | Pass | Fixed (T1) | Fixed (T1) | Former `/evaluation` page body, unchanged; not routed (PR-20) |
| `admin/dashboard/*` (DashboardKpis, DashboardCharts, ActivityFeed, HealthPill) (PR-21) | Pass | Pass | Pass | Cards `bg-surface-card` / `border-line`; event and health tones `status-*-bg` / `status-*-fg`; charts via `useChartTheme()` (toggle covered in tests/journeys/dashboard.test.tsx) |
| `charts/LineTrend` (PR-21) | Pass | Pass | Pass | Series, grid, axis, budget line from `chartTheme`; dashed forecast series |
| `charts/KpiTrendCard` window label (PR-21) | Pass | Pass | Pass | `text-fg-subtle` |
| `ui/CountUp` (PR-21) | n/a | n/a | n/a | Inherits colour; `countUp` preset, instant under reduced motion |
| `admin/evaluation/QualityCharts` (PR-21) | Pass | Pass | Pass | Catalogue Radar + LineTrend in `ChartCard` |
| `chat/ChatHeader`, `chat/TraceLink` (PR-21) | Pass | Pass | Pass | Tokens only; initials chip `brand-50` / `dark:brand-950/60` |
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
| `documents/FormatBadge` | Pass | Fixed (T2) | Pass | `.docx` chip on `status-info-*`, `.md` chip on `surface-muted` / `line`, "Standardized to Markdown" caption `brand-700` / `dark:brand-300` |
| `documents/UploadDialog` (new) | Pass | Pass | Pass | `Modal` (`pop` + focus trap + Esc), drop zone `line` / `brand-50` (`dark:brand-950/40`), step bars `progressFill` on `brand-600`, result on `status-ok-bg` / `status-warn-bg`, errors on `status-danger-*` |
| `documents/MarkdownView` (new) | Pass | Pass | Pass | Headings `text-fg`, code / tables on `surface-muted` / `line`, links `brand-700` / `dark:brand-300` |
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
| `shell/ProfileMenu`, `shell/NotificationBell`, `shell/HeaderUtilities` (PR-17) | Pass | Pass | Pass | Popovers on `surface-elevated`; theme radio (light / dark) applies instantly |
| `shell/MobileTopBar`, `Sidebar` (mobile drawer) (PR-17) | Pass | Pass | Pass | `bg-surface-card`, `border-line`; nav items reuse the desktop classes |
| `shell/BrandedStatusPage` (PR-17) | Pass | Pass | Pass | `BrandLogo` pair on `bg-surface-card` |
| `admin/CursorPagination` (PR-17) | Pass | Pass | Pass | Mirrors `Pagination` (existing `dark:` pairs) |
| `admin/TableToolbar` (columns, density) (PR-17) | Pass | Pass | Pass | Column menu on `surface-elevated`; buttons reuse the existing `dark:` pairs |
| `auth/LoginView`, `auth/BrandPanelDecor`, `auth/MicrosoftSignInButton` (PR-18) | Pass | Pass | Pass | Sheen `via-brand-100/60` (light) / `dark:via-white/10`; hover border `brand-400` / `dark:brand-500`; focus ring token |
| `auth/AccessRequiredView`, `auth/SuspendedView` (PR-18) | Pass | Pass | Pass | Tokens only; reduced motion renders the stepper static |
| `shell/RoleBadge` (PR-18) | Pass | Pass | Pass | Superadmin `brand-50` / `dark:brand-950/60`; admin `status-info-*`; operator `surface-muted` |
| `Sidebar` role filtering + pending badge (PR-18) | Pass | Pass | Pass | Badge `bg-status-warn` with `text-white dark:text-fg-inverse` (existing pair); role badge under the email |
| `admin/user-management/*` (PR-19: `UserOverview`, `charts` DailyBars / Donut, `UserDrawer`, `AccessRequestsTab`, `RolesMatrixTab`, `AuthAuditTab`, `InviteUserDialog`) | Pass | Pass | Pass | Tokens only (`surface-*`, `fg*`, `line`, `status-*`, `brand-*` with `dark:`); safeguard tooltip `bg-surface-inverse` + `text-fg-inverse`; row exit via `AnimatePresence` + `fadeUp` preset |
| `admin/governance/HealthBanner` (PR-23) | Pass | Pass | Pass | Motion colour transition between `semanticTokens[theme]` status values; text `status-*-fg`; asserted in light and dark in tests |
| `admin/governance/StatusTimeline`, `IncidentList` (PR-23) | Pass | Pass | Pass | Cells `status-ok/70`, `status-warn/80`, `status-danger/80`, empty `surface-muted`; markers `status-danger` + `text-fg-inverse` |
| `admin/governance/TraceDrawer`, `JsonViewer`, `badges` (PR-23) | Pass | Pass | Pass | `DrawerShell` chrome; fields `surface-card` / `line`; badges and live indicator on `status-*` tokens |
| `charts/BarList` (PR-24) | Pass | Pass | Pass | Bars from `chartTheme.categorical` / `status`, track `chartTheme.track`, labels `chartTheme.axis`; switches with the theme class |
| `admin/feedback/FilterChips` (PR-24) | Pass | Pass | Pass | Chips `brand-50` / `dark:brand-950/60`, border `brand-300` / `dark:brand-700/60`; `pop` preset |
| `admin/feedback/FeedbackDrawer` (PR-24) | Pass | Pass | Pass | `DrawerShell` tokens; meta tiles `surface-muted`; flags `status-warn-bg` |
| `admin/feedback` Golden job chip, Preference pair flags (PR-24) | Pass | Pass | Pass | Chips on `status-info` / `status-ok` / `status-danger` / `status-warn` bg+fg tokens |
| `chat/FeedbackReasonPanel` (PR-24) | Pass | Pass | Pass | Panel `surface-card` + `border-line`; selected chip `brand-50` / `dark:brand-950/60`; `pop` preset |
| `settings/BrandPreview` (PR-26) | Pass | Pass | Pass | Light and dark panes side by side, each pinned to its theme with inline semantic + draft `--brand-*` variables (independent of the page theme); `BrandLogo forceTheme` |
| `settings/ColorField`, `ContrastBadge` (PR-26) | Pass | Pass | Pass | Badge `status-ok-bg`/`status-ok-fg` or `status-danger-*`; swatches render the chosen colour (data, not tokens) |
| `settings/AssetDropzone` (PR-26) | Pass | Pass | Pass | Dashed `border-line-strong`, hover lift + brand border; dark artwork previewed on `bg-surface-inverse` |
| `settings/TaxonomyTab` table and add form (PR-26) | Pass | Pass | Pass | `surface-muted` header, archived rows at 60 % opacity, status pills on `status-*` tokens |
| `brand/BrandLogo` live update (PR-26) | Pass | Pass | Pass | Saving branding re-renders logos and rewrites every `--brand-*` variable without a reload |
| `analytics/TrendChart`, `HourOfDayChart`, `FlowSankey`, `QualityDonut`, `DropoffChart`, `PeopleBubble` (new, PR-25) | Pass | Pass | Pass | Hand-drawn SVG on the chart-kit primitives; colours from `chartTheme.<theme>.insights` (the HTML's outcome, quality, handled and hour colours, lifted for dark; light Partly / Greeting moved to `status-warn` / slate for 3:1) via `useInsightsPalette()`; grid / axis / tooltip from `chartTheme`; theme flip covered by `tests/components/analytics` |
| `analytics/TopicsTreemap` (new, PR-25) | Pass | Pass | Pass | `TreemapChart` (next/dynamic) with per-tile `color` from the handled-% scale (`mixHex(handled.low, handled.high)`) and `readableOn()` labels |
| `analytics/ConversationsTable` (new, PR-25) | Pass | Pass | Pass | `TableCard` + `Pagination`; outcome pills on `status-*` tokens; expand panel on `surface-muted` |
| `charts/StackedBar` (new, PR-22) | Pass | Pass | Pass | Segments from `chartTheme.categorical`, grid / axis tokens, legend hover dims to `dimOpacity`; one keyboard mark per segment |
| `admin/DashboardHeader` breadcrumb (PR-22) | Pass | Pass | Pass | `text-fg-subtle` trail, current page `text-fg-muted`, hover `text-accent-fg` |
| `admin/entity/ConversationPreviewDrawer` (PR-22) | Pass | Pass | Pass | `DrawerShell`; turn rail `border-brand-200` / `dark:border-brand-800`; outcome chips on `status-*` |
| `admin/entity/shared` (`FilterChip`, `SectionCard`, `ToneChip`) (PR-22) | Pass | Pass | Pass | Chips on `brand-50`/`dark:brand-950/60` and `status-*-bg`/`-fg`; cards `surface-card` + `line`, hover brand border + `shadow-md` |

## Studio rollout (owner theme pass: fix/owner-theme-tables-motion)

Reference: Usage > Overview "Economic Waterfall / Thinking Pipeline / Performance Cockpit" in dark. All rows
below: tokens only, light and dark designed (dark is the reference), toggle switches via CSS variables;
motion from `lib/motion.ts` presets only, reduced to opacity (or instant) under reduced motion.

| Component | Light | Dark | Toggle | Notes | Screenshot L/D |
|---|---|---|---|---|---|
| `ui/StudioCard` (new: StudioCard, StudioCardHeader, StudioIconBadge, StudioPill, StudioGrid, StudioMetricTile, StudioSectionHeader, StudioSegmented, `studioCardClass`) | Pass | Pass | Pass | `bg-studio` gradient (brand-tinted top + corner glow), `border-line-brand` / `dark:border-brand-500/25`, `shadow-studio` inner highlight; hover lift + `shadow-studio-hover`; active ring; tone badges on `brand-*` / `status-*`; grid stagger (none above 50 items); metric count-up; segmented layoutId pill | [ ] |
| `ui/Collapse` (new) | n/a | n/a | n/a | `expand` preset (height + opacity, 200 ms); instant under reduced motion | n/a |
| `ui/styles` studio / table / segment tokens | Pass | Pass | Pass | `studioSurface`, `studioTile`, `studioToneClasses`, `tableHead` (`bg-surface-studio`), `tableRow` (zebra + brand hover), `segmentTrack`; overlay / popover panels get the brand edge | [ ] |
| `charts/ChartCard` (plain + aurora) | Pass | Pass | Pass | Studio surface; optional icon badge + pill; body crossfades by state (loading / error / empty / chart / table); "View as table" header sticky `bg-surface-studio` | [ ] |
| `charts/primitives` ChartEmpty, `charts/Sparkline` empty | Pass | Pass | Pass | Empty panel is a studio tile; sparkline empty is a dashed baseline + "No trend data" (no boxed placeholder) | [ ] |
| `charts/KpiTrendCard`, `insights/StatCard`, `ui/Skeleton` SkeletonCard | Pass | Pass | Pass | Studio surface; StatCard tone pills on `status-*` tokens | [ ] |
| `admin/TableCard`, `admin/DataTable` | Pass | Fixed (T8) | Pass | Studio surface (edge glow on hover, no lift); sticky `bg-surface-studio` header, zebra + brand hover rows, token empty / error / skeleton | [ ] |
| `admin/Pagination`, `CursorPagination`, `TableToolbar` | Pass | Fixed (T8) | Pass | Slate pairs -> `fg*` / `line` / `surface-*`; filter + column popovers animate with `popover` | [ ] |
| `admin/TabGroup`, `AudienceScopeTabs`, `DashboardHeader` | Pass | Fixed (T8) | Pass | `segmentTrack` (dark `bg-surface-studio`); header icon badge = studio brand badge | [ ] |
| `ui/DrawerShell`, `Modal`, `ConfirmDialog`, `ToastProvider` | Pass | Pass | Pass | Brand edge + studio-tinted drawer header / footer | [ ] |
| `ui/EmptyState`, `ErrorState` | Pass | Pass | Pass | Icon in a studio / danger ring badge | [ ] |
| `usage/InteractiveOverviewTab` | Pass | Fixed (T9) | Pass | Stage breakdown: studio tile + `Collapse` (was `bg-slate-900` in light); skeleton / error on shared primitives; token bars | [ ] |
| Ad-hoc tables (feedback Golden / Preference / Review queue, settings Taxonomy, user-management Access requests / Roles, usage inspector, evaluation-live) | Pass | Fixed (T8) | Pass | Header `bg-surface-studio` + brand divider; zebra and hover from the table tokens | [ ] |

## Known follow-ups (not theme-breaking)

- Coloured status chips (`bg-emerald-50 text-emerald-700` and similar) render as light chips in
  dark mode. They stay readable (AA) but should move to the `status-*-bg` / `status-*-fg` tokens
  when their components are next touched (F3 to F6).
- Decorative glows still use `rgba(...)` drop-shadows on the dark hero panels; they are
  theme-neutral.
- The pre-paint theme script carries the per-request CSP nonce from `middleware.ts` (PR-18).
