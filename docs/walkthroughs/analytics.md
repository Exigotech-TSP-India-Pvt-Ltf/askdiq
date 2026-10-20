# Analytics

> What people ask, how each question was answered and where conversations drop. Every turn is labelled with a topic (intent), an answer type (bot quality) and an issue.  Route: `/admin/analytics` · Nav: **Analytics** · Permission: `dashboard:read` (operator, admin, superadmin)

## Purpose and who uses it

The Analytics page (internally "Chatbot Insights") looks at chat traffic one conversation and one question at a time. It does not show tokens or latency. It answers:

- What topics do people ask about, and how often is each one handled well?
- Which conversations were fully served, partly served or not served, and by which kind of user?
- Where did conversations fail (issue of the last unhandled question)?
- When do people ask (day and hour, in your own timezone)?

Who uses it:

- **Analysts and product owners** use topics, flow and drop-off to decide what content to add.
- **Support** uses the **Conversations** tab to read failing conversations question by question.
- **New team members** can take the page tour from the command palette (**Tour this page**).

Every number here comes from **turn labels** (`turn_insights`) written by a background classifier (see [How turns are classified](#how-turns-are-classified)). A conversation with no labelled turn yet does not appear at all, and a banner reports the backlog.

All read endpoints need `dashboard:read` (`read_guard = require_permission(Permission.DASHBOARD_READ)` in `backend/app/api/routes/insights.py`). Editing the taxonomy (intent, quality and issue labels) needs `taxonomy:write` (superadmin) and lives in **Settings → Insights Taxonomy**, see [settings.md](settings.md).

Source: `frontend/app/(app)/admin/analytics/page.tsx`, `frontend/components/admin/analytics/*`, `frontend/lib/insights-api.ts`, `backend/app/api/routes/insights.py`, `backend/app/insights/*`.

## How to get there

- **Sidebar**: **Analytics** (column-chart icon). Shown with `dashboard:read` (`frontend/components/Sidebar.tsx`).
- **URL**: `/admin/analytics`.
- **Command palette** (Ctrl/Cmd+K): type "Analytics". This nav item has no extra keywords.
- **From elsewhere**: topic and quality entity links (for example from the Dashboard) open `/admin/analytics?intent=<CODE>&quality=<CODE>` (`frontend/lib/entity-links.ts`).

URL parameters (`frontend/components/admin/analytics/url-state.ts`). Values are validated against the backend allow-lists, and invalid values fall back to the default. Changes use `history.replaceState`, so filter changes do **not** add Back-button entries. Any filter change resets the table to page 1. Parameters this page does not own (such as `audience`) are kept.

| Param | Allowed values | Default | Effect |
|---|---|---|---|
| `tab` | `guide`, `overview`, `trend`, `flow`, `topics`, `dropoff`, `conversations` | `overview` | Active tab |
| `period` | `all`, `7d`, `30d`, `custom` | `all` | Date window (see Filter bar) |
| `from`, `to` | `YYYY-MM-DD` | none | Only with `period=custom`. Both are required and `from <= to`, otherwise the period falls back to `all` |
| `user_type` | text, max 64 | none | Conversation user type |
| `outcome` | `Served`, `Partly served`, `Not served`, `Greeting only` | none | Conversation outcome |
| `rating` | `Great`, `Good`, `Served with friction`, `Partly served`, `Dropped`, `No question asked` | none | Conversation experience rating |
| `issue` | taxonomy code `^[A-Z][A-Z0-9_]{0,31}$` | none | Issue of the failing question |
| `intent` | taxonomy code | none | Topic. Keeps conversations with **any** turn of that intent, and narrows question counts to it |
| `quality` | taxonomy code | none | Answer type. Same "any turn" rule as `intent` |
| `person` | 16 hex chars (pseudonym) | none | One probable person |
| `q` | text, max 200, control characters stripped | none | Search |
| `sort` | `conv_id`, `person`, `start`, `user_type`, `outcome`, `experience_rating`, `issue`, `exchanges` | `start` | Conversations table sort |
| `dir` | `asc`, `desc` | `desc` | Sort direction |
| `page` | 1-10000 | 1 | Table page (1-based) |
| `limit` | 1-100 | 25 | Table page size |
| `audience` | `overview`, `users`, `testing` | global setting | Audience scope (shared by all admin pages) |

This page has its **own** period filter. The global date range picker is not shown here, and the global `range` param is ignored.

## Layout at a glance

```text
+---------------------------------------------------------------------------+
| [chart] Analytics                 [Overview | Users | Testing]            |
| What people ask, how it was answered and where conversations drop         |
+---------------------------------------------------------------------------+
| [Overview][Trend][Flow][Topics][Drop-off][Conversations]                  |
| PERIOD      USER TYPE  OUTCOME  RATING   SEARCH                           |
| [Full per.v][All v]    [All v]  [All v]  [Question, issue or person ]     |
|                                                       [Clear filters]     |
| (x) 30 May - 30 May   (x) Platform and services                           |
| (hourglass) 42 turns pending classification. ...    [=====---] 90%        |
| +------+ +------+ +------+ +------+ +------+ +------+                     |
| |Conv. | |Subst.| |Quest.| |Conv. | |Prob. | |Avg Qs|                     |
| | 812  | |1,930 | |handl.| |served| |people| |/conv.|                     |
| +------+ +------+ +------+ +------+ +------+ +------+                     |
| +-------------------------------------------------------------------+     |
| | Daily volume  (area = questions, bars = conversations)            |     |
| +-------------------------------------------------------------------+     |
| +-------------------------------+ +---------------------------------+     |
| | Questions by hour of day      | | User type -> outcome (flow)     |     |
| +-------------------------------+ +---------------------------------+     |
| +-------------------------------+ +---------------------------------+     |
| | Question topics (treemap)     | | How questions were answered     |     |
| +-------------------------------+ +---------------------------------+     |
| +-------------------------------+ +---------------------------------+     |
| | Where conversations dropped   | | Conversations per person        |     |
| +-------------------------------+ +---------------------------------+     |
| Conversations (812 conversations)                                         |
| [View] Conv  Person Started Type Outcome Rating Issue  Qs                 |
+---------------------------------------------------------------------------+
```

> Screenshot placeholder: `img/analytics-overview.png`: Overview tab with the filter bar, the KPI strip, all six charts and the top of the Conversations table.

> Screenshot placeholder: `img/analytics-conversations.png`: Conversations tab with one row expanded to show its per-question labels.

## Layout tour

### Common rules for every number

- **Unit of analysis**:
  - An **exchange** (turn) is one user message plus the assistant reply with the same `turn_index`, and only once that reply has a row in `turn_insights`.
  - A **conversation** is one chat session (`chat_sessions`). A chat session expires after `SESSION_TIMEOUT_MINUTES` (default 60) without activity (`backend/app/core/sessions.py`).
  - Conversation fields are computed per session from its **labelled** turns only.
- **Substantive**: an exchange whose intent is not `GRE` (Greeting) or `ACK` (Closing). The SQL hard-codes this (`ti.intent NOT IN ('GRE','ACK')`, `analytics_repository.py`).
- **Handled**: stored per turn by the classifier. It is true when the bot-quality code counts as handled in the taxonomy (default: `OK` Answered correctly, `CORR` Correct refusal, `SCOPE` Out of scope, `ACK` Closing), and for `OK` only when the answer was also grounded (`rules.derive_handled`).
- **Date window**:
  - A conversation is in range when its **start** (first labelled turn) falls inside the range.
  - Exchange counts (questions, topics, quality, hours) are further limited to exchanges whose own date is in range.
  - **Full period** (`all`) is unbounded.
  - **Last 7 days** = today minus 6 days through today, and **Last 30 days** likewise. "Today" is the database's current date in the request timezone.
- **Timezone**: the browser sends its IANA zone as `tz` (`viewerTimeZone()` in `use-insights.ts`). Days and hours are bucketed in **your** timezone. Without `tz` the server uses `INSIGHTS_TIMEZONE` (default `Australia/Sydney`).
- **Audience**:
  - **Testing** = sessions whose `created_ip` is NULL or in the test-IP list.
  - **Users** = sessions with a non-NULL IP not in that list.
  - **Overview** = everything (`_AUDIENCE_SQL`).
- **Conversation filters vs turn filters**:
  - `user_type`, `outcome`, `rating`, `issue`, `person` and `q` keep or drop whole conversations.
  - `intent` and `quality` keep a conversation if **any** of its turns matches, *and* they also narrow exchange-level counts to matching turns.
- **Rollup vs live** (`analytics_service.py::cube`):
  - The exchange counts come from the hourly table `insight_daily_rollup` for closed days, but only when all of these hold: period is **Full period**, audience is **Overview**, no conversation filter is set, `tz` equals `INSIGHTS_TIMEZONE`, and the rollup is fresh.
  - Otherwise, and always for today, they are computed live. Viewers outside `INSIGHTS_TIMEZONE` therefore always get live numbers.
  - The response field `source` is `rollup` or `live`. It is not shown in the UI.
- **Caching and refresh**:
  - Only `/summary` sends an ETag with `Cache-Control: private, max-age=15`.
  - In the browser every query is fresh for 30 s and revalidates on window focus or tab visible (`frontend/lib/query-cache.ts`).
  - There is no manual refresh button and no periodic chart polling. The only poll is the pending-classification check (see [Pending classification banner](#pending-classification-banner)). When that poll sees the labelled count rise, it invalidates every `admin/insights` query, so the charts refresh as labels arrive.
- **Privacy**: question texts returned by the API (failing question, turn messages) are PII-masked (`redact_pii`). People are salted-hash pseudonyms.

### Tabs

**Overview**, **Trend**, **Flow**, **Topics**, **Drop-off**, **Conversations** (ARIA label "Analytics sections"). What each tab shows:

- **Overview** (default): KPI strip, **Daily volume**, then **Questions by hour of day** + **User type → outcome**, then **Question topics** + **How questions were answered**, then **Where conversations dropped** + **Conversations per person**, then the **Conversations** table.
- **Trend**: **Daily volume**, **Questions by hour of day**, **How questions were answered**.
- **Flow**: **User type → outcome**, **How questions were answered**.
- **Topics**: **Question topics**, **How questions were answered**.
- **Drop-off**: **Where conversations dropped**, **Conversations per person**.
- **Conversations**: the table only.
The filter bar, the pending banner and the KPI strip sit above every tab. Every tab reads the same bundle (one request per filter set), so switching tabs renders from memory with no request; `?tab=guide` (the removed Guide tab) opens **Overview**.

While you are on any tab, the page warms in idle time the bundles of the other date-range presets and audiences, and the records behind every KPI card's **View records**, so those open instantly too.

### Filter bar

Fields (all labels uppercase in the UI):

- **Period**: **Full period**, **Last 7 days**, **Last 30 days**, **Custom**. Choosing **Custom** shows **From** and **To** date inputs, pre-filled with today. A range is applied only when both dates are set and **From** <= **To**.
- **User type**: **All**, **Customer / prospect**, **Off-scope request**, **Mixed**, **Greeting only**, plus any other user type seen in the current flow data.
- **Outcome**: **All**, **Served**, **Partly served**, **Not served**, **Greeting only**.
- **Rating**: **All**, **Great**, **Good**, **Served with friction**, **Partly served**, **Dropped**, **No question asked**.
- **Search**: placeholder **Question, issue or person**, debounced 300 ms. On the server it matches (ILIKE) a concatenation of the session ID, user type, issue label, failing question and **all** question texts of the conversation.
- **Clear filters**: resets period, dates and every filter. It does not change tab, sort or page size.

Active filters show as removable chips below the bar:

- The date range, for example `30 May – 30 May`.
- The period name.
- The user type, outcome and rating values.
- The topic, quality and issue labels.
- The person label, or **Selected person**.
- The search text in quotes.

A topic, quality, issue or person chip shows its readable name only after you click that chart mark in this visit. A deep link shows the raw code (for example `SVC`) or **Selected person**.

### Pending classification banner

- Shown while `pending > 0`. Text: **{n} turns pending classification.** followed by either **Topics and quality include labelled turns only.** or, when nothing in the current selection is labelled yet, **The classifier labels each turn in the background; charts fill in as turns are labelled.** A progress bar reads **{labelled} of {total} labelled ({pct}%)**.
- How it's computed (`GET /admin/insights/pending`, `analytics_service.pending`):
  - `pending` = assistant messages with no `turn_insights` row.
  - `labelled` = `COUNT(turn_insights)`.
  - `total` = `pending + labelled`.
  - `progress_pct` = `labelled / total * 100`.
  - The count is **global**: it ignores the audience, period and filters.
- Refresh: re-polled every **30 s** (`PENDING_POLL_MS`) while the backlog is above 0 and the browser tab is visible (`useVisibilityPolling`).

### KPI strip

Six cards, all from `GET /admin/insights/summary` (`analytics_service.summary` + `analytics_repository._KPIS`). "Scoped conversations" means conversations that pass every filter. "Active" means scoped conversations whose outcome is not **Greeting only**.

| Card label | Value | Hint | Formula |
|---|---|---|---|
| **Conversations** | `conversations` | **{active} with a question** | `COUNT(*)` of scoped conversations |
| **Substantive questions** | `substantive_questions` | **of {exchanges} exchanges ({handled_exchanges} handled)** | sum of substantive exchanges in scope. `exchanges` counts all exchanges in scope, greetings included |
| **Questions handled** | `handled_pct` % | **{handled_questions} of {substantive_questions}** | `handled substantive / substantive * 100` |
| **Conversations served** | `served_pct` % | **{served} of {active}** | `Served conversations / active conversations * 100` |
| **Probable people** | `people` | **Pseudonymous, no PII** | `COUNT(DISTINCT person_key)` over scoped conversations |
| **Avg. questions / conv.** | `avg_questions_per_conversation` | **Per conversation with a question** | `SUM(substantive)` over active conversations / active conversations, 2 decimals (the card shows 1) |

- Percentages are rounded to 2 decimals by the API and shown as whole numbers. A zero denominator gives `null`, shown as **n/a**.
- **Probable person**: `person_key` = the first 16 hex chars of `sha256(salt || lower(user_email))`. Without an email it hashes `'ip:' || created_ip`, then `'sid:' || session id`. The salt is derived from the server's session secret (`person_salt`).
- If `/summary` fails, the strip is replaced by a red alert with the error text.

### Daily volume

- Title **Daily volume**, subtitle **Substantive questions and conversations per day. Click a day to focus on it; click again to clear.** A tag shows the first and last day, for example `1 May – 30 May`.
- Drawing: an area and line for questions, and bars for conversations. The tooltip shows **Questions** and **Conversations**. The table view has **Day**, **Questions**, **Conversations**.
- How it's computed (`GET /admin/insights/trend`, `analytics_service.trend`):
  - `questions[day]` = substantive exchanges whose own date (in `tz`) is that day.
  - `conversations[day]` = scoped conversations that **started** that day.
  - Every day between the bounds is listed, with zeros filled in, capped at 400 days (`MAX_TREND_DAYS`).
  - For **Full period** the bounds are the first and last labelled exchange day in the audience (`data_bounds`). Otherwise they are the period bounds.
- Click: sets `period=custom&from=day&to=day`. Clicking the same day again restores **Full period**.
- Empty: **Choose a valid date range.**

### Questions by hour of day

- Title **Questions by hour of day**, subtitle **Your time zone ({tz})**. A radial chart of 24 hours. Tooltip `{h}:00 – {h+1}:00`, **Questions**.
- How it's computed (`GET /admin/insights/hourly`, `_HOURLY`): substantive exchanges in scope, grouped by `extract(hour)` of the user message time in `tz`. `peak_hour` is the busiest hour.
- Not clickable. Empty: **No questions in this selection.**

### User type → outcome

- Title **User type → outcome**, subtitle **{total} conversations with a question**. A Sankey diagram from user type to **Served**, **Partly served** or **Not served**. **Greeting only** conversations are excluded.
- How it's computed (`GET /admin/insights/flow`, `_FLOW`): scoped conversations with outcome other than **Greeting only**, counted per (user type, outcome).
  - **Outcome**:
    - **Greeting only**: no substantive turn.
    - **Served**: every substantive turn handled.
    - **Not served**: none handled.
    - **Partly served**: otherwise.
  - **User type** = the taxonomy *group* of the intent with the most substantive turns in the conversation (ties broken by group name), if that group holds at least **60%** of the conversation's substantive turns (`USER_TYPE_DOMINANCE_PCT`). Otherwise it is **Mixed**. Conversations with no substantive turn are **Greeting only**. Intents without a group fall into `Other`.
  - With the default taxonomy the groups are **Customer / prospect** (SVC, SEC, RAI, CO, CON, BIZ, SUP) and **Off-scope request** (OOS, INJ, AMB, OTHER).
- Click a band: sets (or toggles off) both `user_type` and `outcome`.
- Empty: **No conversations in this selection.**

### Question topics

- Title **Question topics**, subtitle **Size = volume, shade = handled %. Click a topic to filter.** The tag shows **{total} questions**. Once a topic is selected, it shows a **View conversations** button instead, which switches to the Conversations tab.
- A treemap. Tile area is the number of substantive questions. Colour runs from red (0% handled) to teal (100% handled). Each tile reads `{n} · {pct}% handled`. The table view has **Topic**, **Questions**, **Handled**, **Handled %**.
- How it's computed (`GET /admin/insights/topics`, `analytics_service.topics`): substantive exchanges in scope grouped by intent code. Per intent: `questions` = count, `handled` = handled count, `handled_pct` = `handled / questions * 100`. Labels and groups come from the taxonomy, archived entries included. Sorted by volume.
- Click: sets or clears `intent`.
- Empty: **No questions in this selection.**

### How questions were answered

- Title **How questions were answered**. A donut with the total in the centre (**questions**) and a counted legend. The table view has **Answer type**, **Questions**, **Share**.
- Slice order: **Answered correctly** (OK), **Correct refusal** (CORR), **Out of scope** (SCOPE), **Partly answered** (PART), **No-info reply** (FB), **Clarifying question** (CLAR), **Off-target** (OFF), **False action claim** (FALSE). These are the default labels. A superadmin can rename them in Settings.
- How it's computed (`GET /admin/insights/quality`, `analytics_service.quality`): substantive exchanges in scope grouped by `bot_quality`. `pct` = share of all substantive exchanges in scope. Only the eight codes above are returned. A substantive turn labelled `ACK` (Closing) is counted in `total` but has no slice.
- Click a slice: sets or clears `quality`.
- Empty: **No questions in this selection.**
- Slice colours come from the theme tokens (`theme-tokens.ts`, `insights.quality`). A colour edited in the taxonomy is **not** used by this chart.

### Where conversations dropped

- Title **Where conversations dropped**, subtitle **Issue of the last unhandled question, Partly and Not served conversations**. A lollipop chart, red. Once an issue is selected it shows a **View conversations** button.
- How it's computed (`GET /admin/insights/dropoff`, `_DROPOFF`):
  - Each conversation's `issue` is the issue code of its **last** (highest `turn_index`) substantive, unhandled turn.
  - The chart counts scoped conversations with outcome **Partly served** or **Not served** and a non-null issue, grouped by issue, sorted by count.
  - Conversations whose last unhandled turn has no issue (for example a rule-labelled **Clarifying question**) are not counted. The total can therefore be lower than Partly + Not served.
- Default issue labels:
  - **Info not in knowledge base**
  - **Answer too narrow**
  - **Wrong answer**
  - **Generic answer**
  - **Support request, no route**
  - **Claimed an action it cannot do**
  - **Clarifying question, user left**
  - **Pricing or quote needs follow-up**
  - **Model error**
  - **Other**
- Click: sets or clears `issue`.
- Empty: **No dropped conversations in this selection.**

### Conversations per person

- Title **Conversations per person**, subtitle **{n} probable people**. Packed bubbles. Size is the number of conversations, and colour is the person's most common outcome. The legend shows **Served**, **Partly served**, **Not served**. The tooltip shows **Conversations** and **Mostly**.
- How it's computed (`GET /admin/insights/people`, `_PEOPLE`):
  - Scoped conversations are counted per pseudonym and outcome.
  - The dominant outcome is the most frequent one. Ties go to the outcome seen first.
  - Labels **P01**, **P02**, … are numbered per result set by each person's first conversation start. The same person can get a different P-number under different filters. The stable `person_id` is what the filter stores.
- Click: sets or clears `person`.
- Empty: **No people in this selection.**

### Conversations table

- The console's standard table: title **Conversations** with **{n} conversations**, a search box (**Search id, person, issue or question…**), **Filters** (**Type**, **Outcome**, **Rating**, **Issue**), **Columns** and **Compact**. Search and filters are the page's own URL filters, shared with the filter bar and every tab; search runs on the server (conversation id, user type, issue, failing question and question text).
- Columns (click a header to sort):
  - **Conv**: the first block of the session UUID, linking to `/admin/conversations/<id>`.
  - **Person**: P-number.
  - **Started**: the start date in your timezone.
  - **Type**: user type.
  - **Outcome**: a coloured pill.
  - **Rating**: the experience rating.
  - **Issue**: the issue label.
  - **Qs**: all labelled exchanges, greetings included.
- Sorting:
  - The default is **Started**, newest first.
  - A new column starts descending; clicking again toggles.
  - Ties are broken by session ID.
  - Sorting is server-side.
- Paging: offset-based, **10** rows by default (10 / 25 / 50 / 100), with **Showing x–y of N entries**. The next page (with its questions) is prefetched while idle, and the previous page and each sort toggle too.
- **View** / **Hide** expands the row to list every question with `time · topic · Handled | Not handled | greeting/closing · answer type · issue · friction: …`. Each row of the bundle's page carries its labelled questions (`questions`: text masked and cut to 160 characters), so expanding never waits for a request. Rows without them (an older stored snapshot) fall back to `GET /admin/insights/conversations/{id}` (only `audience` and `tz` apply), which also lists unlabelled turns as **Pending classification** and shows **Loading questions…** while loading. **No questions recorded.** when there are none.
- **Experience rating** (`conv` CTE in `analytics_repository.py`), first match wins:
  1. Any rating of 1-2 stars in the session's feedback → **Dropped**.
  2. **Served** and some rating of 5 → **Great**.
  3. **Served** and any turn with friction (`hostile` or `abandoned`) → **Served with friction**.
  4. **Served** → **Good**.
  5. **Partly served** → **Partly served**.
  6. **Not served** → **Dropped**.
  7. Otherwise **No question asked**.

  Feedback is taken from **all** ratings on the session, whatever the date filter.
- States: skeleton rows while loading, an error with **Try Again**, and an empty state (**No conversations match this search or these filters** / **No conversations in this range**).

### How turns are classified

A two-stage hybrid classifier writes one `turn_insights` row per assistant message (`backend/app/insights/*`).

**When it runs**

- **On write**:
  - When an assistant message is saved, the job `insights.classify` is queued in the same transaction (`triggers.enqueue_classification_in_transaction`), deduplicated per message. This requires `INSIGHTS_CLASSIFY_ON_WRITE=true` (the default).
  - It runs in the job worker, never in the chat request path. If queuing fails, the chat still succeeds and the turn waits for a backfill.
- **Backfill**:
  - `backend/scripts/backfill_turn_insights.py` (`--limit`, `--since`, `--chunk-size`, `--dry-run`) queues `insights.backfill` jobs in chunks of message IDs (default 100, max 500).
  - The worker labels them under the same budget.
- **Rollup**:
  - `insights.rollup` runs hourly. Every classify or backfill job also makes sure the next hourly slot is queued.
  - It recomputes `insight_daily_rollup` for today, yesterday and any day that received late labels, with days in `INSIGHTS_TIMEZONE`.

**Stage 1: rules** (`rules.classify_by_rule`, `classified_by = rule`, confidence 1.0, no LLM cost)

| Condition (first match wins) | Intent | Bot quality | Handled | Issue |
|---|---|---|---|---|
| Greeting or capability question (conversational detector), or outcome `conversational` | `GRE` | `ACK` | yes | none |
| Thanks or goodbye | `ACK` | `ACK` | yes | none |
| Prompt injection (unsafe-input flags or the pre-check fails) | `INJ` | `CORR` | yes | none |
| `spam_detected` flag | `OTHER` | `SCOPE` | yes | none |
| `unclear_input`, `empty_query` or `query_too_long` flag | `AMB` | `CLAR` | no | none |
| Outcome `llm_failed` | `OTHER` | `OFF` | no | `LLM_FAIL` |
| Outcome `blocked` with a policy flag (secret or PII leak, unsafe) | `OTHER` | `CORR` | yes | none |
| Outcome `blocked`, other | `OOS` | `SCOPE` | yes | none |

Everything else (normal answered or ungrounded questions) goes to the LLM.

**Stage 2: LLM** (`classifier.classify_turns`, `llm.py`, `classified_by = llm`)

- Turns are sent in batches of `INSIGHTS_BATCH_SIZE` (default 10). A job for one message also picks up other unlabelled turns to fill its batch.
- The model is `INSIGHTS_MODEL`, or the main Azure deployment when that is empty. The reasoning effort is `INSIGHTS_REASONING_EFFORT` (default `minimal`).
- Before sending, user and assistant text is PII-masked and truncated: user text to `INSIGHTS_USER_MAX_CHARS` (1000), the answer to `INSIGHTS_ANSWER_MAX_CHARS` (600).
- The prompt lists the **active** taxonomy codes for intent, bot quality and issue, plus friction (`hostile` or `abandoned`) and a confidence score. Its output is constrained by a strict JSON schema.
- The output is validated again:
  - An unknown intent becomes `OTHER`.
  - An unknown quality falls back to the rule table's outcome mapping: grounded answer → `OK`, weak evidence → `FB`, else `PART`.
  - Invalid answers get confidence <= 0.1.
  - `handled` is **not** taken from the model. It is derived from the quality's `handled` flag in the taxonomy, plus groundedness for `OK`.
  - The issue is dropped when the turn is handled.
- **Budget**: `INSIGHTS_DAILY_TOKEN_BUDGET` (default 200,000 tokens per local day in `INSIGHTS_TIMEZONE`). Usage is recorded in `insight_llm_usage`. A batch whose estimate exceeds what is left is deferred to the next local midnight. While deferred, those turns stay **pending**.
- **Concurrency**: one Postgres advisory lock means only one worker calls the LLM at a time. A busy lock defers the job by 15 s. An open LLM circuit breaker defers it too.
- The `manual` value of `classified_by` exists in the code, but nothing in this UI sets it.

**Taxonomy**:

- Codes are stable and stored on each label. Labels, descriptions, colours, groups and order are editable, and entries can be archived but never deleted, in **Settings → Insights Taxonomy** (`/admin/settings?tab=taxonomy`, `taxonomy:write`, superadmin). See [settings.md](settings.md).
- Renaming a label changes the charts at once, because they look up labels at read time.
- Archived entries stop being offered to the classifier, but old labels keep their name.
- If the taxonomy table has no active intents or qualities, the built-in default (`taxonomy.DEFAULT_ENTRIES`) is used.
- The user-type grouping depends on each intent's **group**, so editing groups changes **User type** and the flow chart for all history.

## Common tasks

### 1. Find the topics the assistant handles worst

1. Open **Analytics** and pick the **Topics** tab. Set **Period** to **Last 30 days**.
2. In **Question topics**, look for large tiles shaded red: high volume, low handled %. Switch to **View as table** to sort mentally by **Handled %**.
3. Click the tile. The chip with the topic name appears, and every chart and the KPIs narrow to conversations containing that topic.
4. Click **View conversations**. Expand a few rows with **View** to read the failing questions, then click the ID to open the full log.

### 2. Investigate why conversations drop

1. Open the **Drop-off** tab.
2. In **Where conversations dropped**, click the biggest issue (for example **Info not in knowledge base**).
3. Open **Conversations**. Rows now show that issue. Sort by **Started** to see the newest.
4. For **Info not in knowledge base**, collect the failing questions and hand them to the documents owner (see [documents.md](documents.md)). For **Support request, no route** or **Pricing or quote needs follow-up**, the fix is a routing or contact change, not content.

### 3. Look at one day or one hour of traffic

1. In **Daily volume**, click a day. The period becomes **Custom** with **From** = **To** = that day.
2. **Questions by hour of day** now shows that day's hours in your timezone.
3. Click the same day again to return to **Full period**.

### 4. Review real visitors only

1. Select **Users** in the header audience tabs. Test-IP and IP-less sessions are excluded.
2. Set **Outcome** to **Not served** and open **Conversations**.
3. Share the URL. It carries the tab, period and filters, but the audience is a global setting, so tell the recipient to pick **Users**.

### 5. Check whether classification has caught up

1. If the blue banner **{n} turns pending classification** shows, the charts are partial.
2. Wait. The banner re-checks every 30 s while the tab is visible, and the charts refresh as labels land.
3. If the count does not move for hours, the daily token budget is probably exhausted (jobs wait until local midnight in `INSIGHTS_TIMEZONE`), or the job worker is down. Ask an engineer to check the worker logs (`insights_budget_exhausted`) and, for old history, to run `scripts/backfill_turn_insights.py`.

## Interactions and states

- **Cross-filtering**: clicking a day, a flow band, a topic tile, a donut slice, a drop-off issue or a person filters the whole page. Clicking the same mark again clears that filter. Hover or keyboard focus shows exact numbers in a tooltip.
- **View as table** / **View as chart** on every chart card gives an accessible table of the same data.
- **Drill-through**: the table ID opens `/admin/conversations/<id>` (Conversation Logs). **View conversations** buttons switch to the Conversations tab with filters kept.
- **Exports**: none on this page. There is no CSV or JSON download.
- **Keyboard**: chart marks are focusable buttons (Enter selects). The tour closes with Escape. There are no page-specific shortcuts.
- **Loading**: each chart card shows its own skeleton. KPI values show pulsing placeholders.
- **Errors**: each chart card shows the error text and a retry button. The KPI strip shows a red alert line.
- **Empty states**:
  - **Choose a valid date range.** (Daily volume)
  - **No questions in this selection.** (hours, topics, quality)
  - **No conversations in this selection.** (flow)
  - **No dropped conversations in this selection.**
  - **No people in this selection.**
  - **No conversations match these filters.** (table)
- **422 errors**: an invalid filter (for example a hand-edited `from` without `period=custom`) is normally caught by the URL parser first. The server rejects anything left over with a 422 problem response, and the card shows the message.
- **503**: an aggregate that times out returns 503, "The query took too long." Narrow the period or filters.
- **When data is missing**:
  - A brand-new conversation appears only after its first turn is labelled.
  - Sessions with no labelled turns are invisible here but still appear in Conversation Logs.
  - Under **Testing**, sessions without an IP are included.

## Light and dark theme

Analytics uses its own palette from `frontend/lib/theme-tokens.ts` (`insights`): outcome colours (Served teal, Partly served amber, Not served red, Greeting only slate), the topic red-to-teal handled scale and the hour ramp. Each has separate light and dark values, and charts re-render when the theme flips. Treemap tile labels pick black or white for contrast (`readableOn`).

## API endpoints used

All paths are under `/admin/insights` (router prefix). They are reached from the browser through `/api/proxy`.

| Method | Path | Permission | Used for |
|---|---|---|---|
| GET | `/admin/insights/summary` | `dashboard:read` | KPI strip (ETag, `private, max-age=15`) |
| GET | `/admin/insights/trend` | `dashboard:read` | Daily volume |
| GET | `/admin/insights/hourly` | `dashboard:read` | Questions by hour of day |
| GET | `/admin/insights/flow` | `dashboard:read` | User type → outcome; extra user-type options in the filter |
| GET | `/admin/insights/topics` | `dashboard:read` | Question topics |
| GET | `/admin/insights/quality` | `dashboard:read` | How questions were answered |
| GET | `/admin/insights/dropoff` | `dashboard:read` | Where conversations dropped |
| GET | `/admin/insights/people` | `dashboard:read` | Conversations per person |
| GET | `/admin/insights/conversations?sort=&dir=&limit=&offset=` | `dashboard:read` | Conversations table |
| GET | `/admin/insights/conversations/{conv_id}?audience=&tz=` | `dashboard:read` | Expanded per-question detail |
| GET | `/admin/insights/pending` | `dashboard:read` | Pending classification banner |
| GET | `/admin/insights/taxonomy` | `dashboard:read` | (Settings page) list taxonomy |
| POST | `/admin/insights/taxonomy` | `taxonomy:write` | (Settings page) create entry |
| PATCH | `/admin/insights/taxonomy/{kind}/{code}` | `taxonomy:write` | (Settings page) edit entry |
| POST | `/admin/insights/taxonomy/{kind}/{code}/archive` | `taxonomy:write` | (Settings page) archive entry |

Shared query params on the aggregate routes are `period`, `from`, `to`, `user_type`, `outcome`, `rating`, `issue`, `intent`, `quality`, `person`, `q`, `audience` and `tz` (`insights_filters`). The aggregate routes run on a read-only DB session, with their sub-queries in parallel.

## Related pages

- [README.md](README.md): index of walkthroughs
- [shell.md](shell.md): sidebar, command palette, audience tabs
- [settings.md](settings.md): **Insights Taxonomy** editing (`taxonomy:write`)
- [conversation-logs.md](conversation-logs.md): the full log opened from the Conv ID
- [dashboard.md](dashboard.md): topic links into this page
- [feedback.md](feedback.md): star ratings that feed the experience **Rating**
- [documents.md](documents.md): fixing **Info not in knowledge base** gaps
- [visitors.md](visitors.md): IP-based audiences and test IPs
- [usage-cost.md](usage-cost.md): token cost (the classifier's own LLM spend is not broken out there)
- [chat.md](chat.md), [evaluation.md](evaluation.md), [governance.md](governance.md), [user-management.md](user-management.md), [login.md](login.md)

## Known limitations and in-flight changes

- **Labelled turns only**: sessions and turns without a `turn_insights` row are excluded from every chart and KPI. The pending count is global, not filtered.
- **Conversation start defines the period**: a conversation that started before the range but continued into it is excluded from the conversation counts.
- **Experience rating** uses all feedback on the session, whatever the date filter. One 1-2 star rating marks the whole conversation **Dropped**.
- **Drop-off** omits conversations whose last unhandled question has no issue code.
- **P-numbers** are per result set and change with filters.
- **Custom period default**: choosing **Custom** pre-fills today's date using the UTC calendar day (`new Date().toISOString()`), which can be yesterday's date in eastern-hemisphere mornings.
- **Chip labels** for deep-linked topic, quality, issue or person filters show codes or **Selected person** until you click the matching chart mark.
- **Quality donut colours** ignore the taxonomy colour field.
- **Substantive is hard-coded** in the dashboard SQL as "intent not GRE/ACK". The taxonomy's per-intent `substantive` flag is used by the classifier when it writes labels, not by these charts.
- **No export** from this page. Filter changes do not create browser history entries (`replaceState`).
- The rollup is used only for **Full period** + **Overview** audience + the server timezone + no conversation filters. Everything else is computed live, so large ranges in other timezones are slower.
- The `nav.ts` comment about the item being `enabled: false` is outdated. The item is enabled in `Sidebar.tsx`.
- Coming in branch `feat/realistic-demo-data`: `backend/scripts/seed_realistic_activity.py` seeds multi-turn sessions with synthetic `turn_insights` labels (model `seed-synthetic`), session feedback and test-IP rules, then recomputes the daily rollup with the real job. Every chart then has data and the pending banner stays empty. `--purge --apply` removes the seeded rows.
- Coming in branch `fix/chat-page-fetch-timeouts`: the shared browser cache backs off after a failed request (1 s, 2 s … up to 60 s) before focus or mount refetches retry. The parallel aggregate runner (`gather_scoped`, used by `/summary`) gets a process-wide connection cap (`fanout_slots`).
- `perf/dashboard-load-speed` and `fix/shell-nav-logout-theme-dates` do not change this page's components or endpoints. The shell branch only flattens the sidebar, and the **Analytics** item is unchanged.
