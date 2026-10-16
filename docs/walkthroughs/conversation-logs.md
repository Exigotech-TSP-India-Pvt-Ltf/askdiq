# Conversation Logs

> Every chat conversation as a searchable, filterable list, a quick preview drawer, and a full per-turn detail page with timings, tokens, cost, sources, feedback and a link to the Governance trace.  Route: `/admin/conversations` and `/admin/conversations/[id]` · Nav: **Conversation Logs** · Permission: `dashboard:read` (operator, admin, superadmin); **Promote to test** needs `feedback:triage` (admin, superadmin)

## Purpose and who uses it

A **conversation** is one chat session (`chat_sessions` row). A **turn** is one `turn_index` inside it: the user message plus the assistant answer.

- **Support** reads exactly what a user asked and what the assistant answered, and checks which documents were used and whether the user rated the answer.
- **Analysts** look at conversation length and the daily outcome mix, then filter to the conversations behind a bar.
- **Admins** turn a bad or important answer into a golden regression test (**Promote to test**). It is then re-run from the Feedback page and the benchmark suite.

For cost totals and CSV export use [Usage & Cost](usage-cost.md). For stage-level traces use [Governance](governance.md).

## How to get there

- **Sidebar**: **Conversation Logs** (speech-bubbles icon), shown with `dashboard:read` (`frontend/components/Sidebar.tsx`). The page header itself says **Conversations**.
- **Command palette** (Ctrl/Cmd+K): "Conversation Logs".
- **Links from other pages:**
  - conversation links anywhere in the app open `/admin/conversations/<id>`;
  - turn links open `/admin/conversations/<id>?turn=<turn_index>` (`frontend/lib/entity-links.ts`);
  - Visitors and Dashboard charts link to the list with filters (`conversationsHref`, `frontend/lib/entity-routes.ts`).

List URL parameters (`frontend/app/(app)/admin/conversations/page.tsx`):

| Param | Allowed values | Default | Effect |
|---|---|---|---|
| `convs_q` | text (max 200 on the stats call) | empty | Search title, session id or origin IP (debounced 300 ms) |
| `convs_page`, `convs_limit` | page 1..; `10`, `25`, `50`, `100` | `1`, `10` | Paging |
| `convs_sort` | `last_activity_desc`, `last_activity_asc` | `last_activity_desc` | Sort by last activity |
| `ip` | an IP | none | Origin IP (`chat_sessions.created_ip`), exact match |
| `min_turns`, `max_turns` | integers >= 1 (`min <= max`) | none | Turn-count range, inclusive (set by the histogram) |
| `outcome` | `answered`, `ungrounded`, `blocked`, `conversational`, `llm_failed` | none | Conversations with at least one turn of this outcome |
| `day` | `YYYY-MM-DD` | none | With `outcome`: only turns on that **UTC** day. The backend rejects `day` without `outcome` (422) |
| `preview` | a conversation id | none | Opens the preview drawer |
| `range` / `from` / `to` | global range | `7d` | Range for the stat cards and charts only (not the table) |
| `audience` | `overview`, `users`, `testing` | `overview` / last used | Audience scope |

Detail URL parameters (`frontend/app/(app)/admin/conversations/[id]/page.tsx`):

| Param | Allowed values | Effect |
|---|---|---|
| `turn` | `turn_index` (0-based integer) | Scrolls to, highlights and selects that turn. Echoed by the API as `focus_turn` |
| `inspect` | a request id | Opens the Single-Query Economic Inspector |

Turns are shown 1-based (**Turn 1**), but the `turn` param is 0-based.

## Layout at a glance

```text
List: /admin/conversations
+--------------------------------------------------------------------------+
| [#] Conversations         [Overview|Users|Testing]   [Last 7 days v]     |
|     Audience-scoped conversations, turn counts and outcomes              |
+------------------------+-------------------------+-----------------------+
| Conversations          | Avg turns per conv.     | Turns                 |
+------------------------+-------------------------+-----------------------+
| Conversation length (histogram)  | Outcomes by day (100% stacked bars)   |
| 1 | 2-3 | 4-6 | 7-9 | 10+        | Answered/Ungrounded/Blocked/...       |
+--------------------------------------------------------------------------+
| [Search title, ID, or origin IP...] [Origin IP v]                        |
| [Visitor 1.2.3.4 x] [2-3 turns x] [Ungrounded on 14 Oct x]               |
| Conversation | Origin IP | Turns | Messages | Created | Last Active | [o]|
| ...                                                 < 1 2 3 >  10/page   |
+--------------------------------------------------------------------------+
```

> Screenshot placeholder: `img/conversation-logs-list.png` — List with an outcome-by-day chip active and the preview drawer open on the right.

```text
Detail: /admin/conversations/<id>
+--------------------------------------------------------------------------+
| Conversations > <title>                                                  |
+-----------------+-----------------+-----------------+--------------------+
| Visitor [View]  | Started         | Turns           | Cost               |
+-----------------+-----------------+-----------------+--------------------+
| Stage latency - turn N (waterfall) | Tokens per turn (stacked bars)      |
+--------------------------------------------------------------------------+
| Turn 1 [answered] 14/10 10:02 · 1.84 s · $0.0012 [Inspect][Trace][Promote]
|   USER: ...                                                              |
|   ASSISTANT: ...                                                         |
|   Feedback | Insight labels | Sources                                    |
+--------------------------------------------------------------------------+
| Turn 2 ...                                                               |
+--------------------------------------------------------------------------+
```

> Screenshot placeholder: `img/conversation-logs-detail.png` — Detail page with turn 2 highlighted from `?turn=1` and its feedback and sources footer visible.

## Layout tour

### List header

- Title **Conversations**, subtitle **Audience-scoped conversations, turn counts and outcomes**. Audience tabs and the global date-range picker are shown.
- The audience filter applies to the **session's created IP** (`backend/app/entities/conversations.py::session_conditions`):
  - `users`: not NULL and not a test IP;
  - `testing`: NULL or a test IP;
  - `overview`: no filter.

### Stat cards

From `GET /admin/conversations/stats` (`backend/app/entities/conversations.py::conversation_stats`). They use the **global range**, the audience, `ip` and `q`. They **ignore** the turn-count and outcome filters.

- **Conversations** = sessions **created** in the range (inclusive UTC days) that have at least one message. The subtitle is the range label (for example "Last 7 days"). If the stats endpoint is missing, the card falls back to the table total with the subtitle **Matching filters**.
- **Avg turns per conversation** = total turns / conversations for those same sessions. The server rounds to 2 decimals and the card shows 1. "—" when there are none.
- **Turns** = sum of distinct `turn_index` per session over those sessions. If stats are missing, the card is replaced by **Messages on this page**: the sum of `message_count` over the visible rows.
- Timezone: the range is converted to `YYYY-MM-DD` from your **local** calendar date (`toIsoDay`, `frontend/lib/range-context.tsx`). The backend reads those as **UTC** days, so near midnight the window can be off by up to one day.

### Conversation length (histogram)

- Title **Conversation length**, subtitle **Conversations by number of turns; select a bar to filter**.
- Bins (fixed, `TURN_BINS`): 1, 2-3, 4-6, 7-9, 10+. Same population as the **Conversations** card.
- Clicking a bar sets `min_turns` / `max_turns` and resets to page 1. A chip such as **2–3 turns** appears (en dash; the single-turn bin reads **1 turn**, the last bin **10+ turns**).
- Table view: **Turns | Conversations**.
- Empty: **Conversation stats are not available yet** (endpoint missing), otherwise the default **No data for this period**.

### Outcomes by day (stacked bars)

- Title **Outcomes by day**, subtitle **Share of turns per outcome; select a segment to filter**. Bars are 100% stacked per day.
- How it's computed (`outcomes_by_day`):
  - Assistant messages created in the range, grouped by **UTC day** and outcome.
  - Legacy outcomes are mapped to the current names. A NULL outcome counts as **Answered**.
  - Every day of the range is present (zero-filled).
  - Series labels: **Answered**, **Ungrounded**, **Blocked**, **Conversational**, **LLM failed**.
- Clicking a segment sets `outcome` + `day`. The table then lists conversations with at least one turn of that outcome on that UTC day. The chip reads, for example, **Ungrounded on 14 Oct**.
- Table view: **Day** plus one column per outcome (counts, not shares).

### Toolbar and filter chips

- Search **Search title, ID, or origin IP…**: ILIKE on session title, session id and created IP.
- **Origin IP** dropdown: **All Origin IPs** plus the distinct IPs **on the current page** only (and the active one).
- Active filter chips: **Visitor <ip>**, **1 turn** / **N–M turns** / **N+ turns**, and **<Outcome> on <day>**. Each has an X (**Clear filter: ...**). Changing any filter resets to page 1 and closes the preview. The change is pushed to browser history, so Back undoes it.

### Conversations table

From `GET /admin/conversations` (`backend/app/api/routes/admin.py::list_conversations`). It is **not** limited by the global range: it lists every matching conversation, all time.

| Column | Content |
|---|---|
| **Conversation** | Title (**Untitled Session** when empty) plus the copyable conversation id link |
| **Origin IP** | `created_ip`. Links to the visitor page `/admin/users/client-<ip>`. "—" when unknown |
| **Turns** | Distinct `turn_index` values (`turn_count`) |
| **Messages** | All messages, user and assistant (`message_count`) |
| **Created** | `chat_sessions.created_at` |
| **Last Active** | `last_activity_at` (sortable) |
| (eye icon) | **Preview** |

- Row click or the eye icon opens the preview drawer (`?preview=<id>`).
- Empty: **No conversations found matching current audience and filters.** Error: **Failed to load conversations.**
- The server also returns `outcome_counts` and `expires_at` per row. They are not displayed.

### Preview drawer

`frontend/components/admin/entity/ConversationPreviewDrawer.tsx`, data from `GET /admin/conversations/{id}`.

- Title = conversation title (or **Conversation**), with the id underneath.
- Buttons: **Previous record** / **Next record** (within the current page), **Open full view** (detail page) and **Close preview** (or Esc).
- **Turn timeline**: a **Started … · last activity …** line, then per turn:
  - a **Turn N** link to the detail page at that turn;
  - the outcome chip and a **not grounded** chip;
  - the time;
  - the question and answer, cut at 280 characters.
- States: **Loading conversation**, **Couldn't load this conversation.** (retry), **No turns in this conversation**.

### Detail page header and stat cards

- Breadcrumb **Conversations > <title>**. The title falls back to **Untitled Conversation**. There are no audience tabs: a conversation is shown whatever audience you have selected.
- **Visitor**: `created_ip`, with a **View visitor** link.
- **Started**: `created_at`, with the subtitle **Last activity <time>**.
- **Turns**: number of turns, subtitle **N messages**.
- **Cost** = sum of each turn's stored `cost_usd` (USD, up to 4 decimals). The subtitle **N tokens** is the sum of per-turn totals, where each turn = `total_tokens` (prompt + completion) + query embedding + guardrail embedding tokens. If per-turn detail is missing: **Per-turn cost not available yet**. Ingestion cost is never part of a conversation. See [usage-cost.md](usage-cost.md#how-cost-is-computed) for how a turn's cost is computed and stored.

### Stage latency chart

- Title **Stage latency · turn N** for the selected turn (the `?turn=` turn, otherwise the first turn with detail). Subtitle **Select a stage to open the query inspector**.
- Waterfall of `latency_records` columns for that request: **Guardrail (in)**, **Embedding**, **Retrieval**, **Reranker**, **LLM**, **Guardrail (out)** (`STAGE_FIELDS`, `backend/app/governance/monitoring.py`). Stages that did not run are omitted. Values under 1 s show as ms, above as seconds.
- Clicking any stage opens the inspector for that turn (`?inspect=<request_id>`).
- Table view: **Stage | Duration**. Empty: **No stage timings for this turn** or **Per-turn stage timings are not available yet**.

### Tokens per turn chart

- Title **Tokens per turn**, subtitle **Select a turn to jump to it**. Stacked bars per turn (`#1`, `#2`, ...):
  - **Prompt** = prompt - cached;
  - **Cached prompt**;
  - **Completion** = completion - reasoning, i.e. the visible output;
  - **Reasoning**.
  Embedding tokens are not shown here.
- Clicking a bar selects that turn (`?turn=`).
- Table view: **Turn | Prompt | Cached | Completion | Reasoning**. Empty: **No token counts** / **Per-turn tokens are not available yet**. Turns with no LLM call (blocked, canned answers) have no token row.

### Turn cards

One card per turn, in order (`turn_details` in `backend/app/entities/conversations.py`):

- **Header**:
  - **Turn N** button: selects the turn and updates the charts;
  - outcome chip (`answered` green, `ungrounded` amber, `blocked` / `llm_failed` red, others neutral);
  - **not grounded** when `grounded = false`;
  - one red chip per guardrail flag (for example `possible_hallucination`);
  - the turn time;
  - **· <total latency> · <cost>**.
- **Buttons**:
  - **Inspect**: opens the Single-Query Economic Inspector (token and cost breakdown at the rate card);
  - **Trace**: opens Governance Traces at `/admin/governance?tab=Traces&request_id=<id>`;
  - **Promote to test** / **Promoted**: only for `feedback:triage`. Disabled once promoted.
- **USER** and **ASSISTANT** blocks: the full stored texts.
- **Footer** (only when the turn has feedback, labels or sources):
  - **Feedback**: each rating as **N / 5** (green 4-5, amber 3, red 1-2), reason tags and the quoted comment, then **Open in Feedback** (`/admin/feedback?message_id=<assistant message id>`). Shows **None** if there are no ratings.
  - **Insight labels**: intent and quality from the turn classifier (`turn_insights`). Both link to Analytics filtered by that value. An issue label shows as an amber chip. **Not classified yet** when absent.
  - **Sources**: the chunks retrieved for the answer. Each shows the document name and **· chunk N** and links to the chunk on the document page. **No cited sources** when empty. They come from the online evaluation record (`evaluation_records.retrieved_chunks`) if one exists, else from the query audit row (`audit_log.detail.sources`). They are the passages given to the model, not citations parsed out of the answer text.

### Single-Query Economic Inspector

The same drawer as on Usage & Cost (`frontend/components/admin/usage/QueryInspectorDrawer.tsx`, `GET /admin/usage/queries/{request_id}`). It shows Client IP, Session, **Turn Cost**, Groundedness, the prompt and answer with copy buttons, the token allocation bar and the **Stage-by-Stage Mathematical Pricing (Rate Card)** table. **Open full view** goes to the Governance trace. Details and known issues are in [usage-cost.md](usage-cost.md#single-query-economic-inspector-drawer).

## Common tasks

1. **Investigate a bad answer** (a user complained, or a thumbs-down came in).
   1. Find the conversation:
      - from Feedback, open the rating and use **Open full view** (it opens this conversation);
      - or search the list by title, session id or the user's IP;
      - or, if you have a request id, paste it into **Usage & Cost > All Queries** search and follow the **Session** link.
   2. Open it with **Open full view** (or click the title link) and scroll to the turn. Its card is highlighted when you arrive with `?turn=`.
   3. Read the chips. **not grounded** or `possible_hallucination` means the groundedness check (answer-to-source embedding similarity below `GROUNDEDNESS_THRESHOLD`, default 0.45) failed. `blocked` means a guardrail stopped it before generation.
   4. Check **Sources**:
      - no sources, or sources from the wrong document, points to a retrieval or knowledge-base gap (see [documents.md](documents.md));
      - right sources but a wrong answer points to generation.
   5. Click **Trace** to see the Governance trace: retrieval scores per chunk, stage timings and guardrail decisions.
   6. Click **Inspect** if cost or token use looks abnormal (for example very high reasoning tokens).
   7. Read **Feedback** for the user's rating, reason tags and comment. Use **Open in Feedback** to triage it.
   8. If this question must never regress, click **Promote to test** (admin). The toast **Turn N promoted to a test case** shows the new case id. The case appears on the Feedback page's golden cases tab. Run it there, or include it in the next benchmark run (`backend/scripts/run_evaluation.py` loads promoted cases).

2. **See how long conversations usually are.**
   1. Pick a range in the header (for example **Last 30 days**) and the **Users** audience.
   2. Read **Avg turns per conversation** and the **Conversation length** histogram.
   3. Click the **10+** bar to list the longest conversations and preview them.

3. **Find the conversations behind a spike of ungrounded answers.**
   1. In **Outcomes by day**, click the **Ungrounded** segment of the spike day.
   2. The table lists conversations with an ungrounded turn on that UTC day.
   3. Use **Previous record** / **Next record** in the preview drawer to scan them quickly.

4. **Review one visitor's history.**
   1. Choose the IP in the **Origin IP** dropdown, or arrive from Visitors (`?ip=`).
   2. Open each conversation, or use **View visitor** on the detail page for the visitor timeline.

## Interactions and states

- **Deep links:** every filter, the preview, the selected turn and the open inspector are in the URL. Copy the address bar to share exactly what you see. Back closes the drawer or undoes the last filter.
- **Promote to test** (`POST /admin/feedback/{assistant_message_id}/promote-to-test`, `feedback:triage`):
  - the button flips to **Promoted** immediately (optimistic);
  - on error it reverts and shows **Couldn't promote this turn**;
  - repeat calls return the same case.
  The `eval:run` permission is **not** what gates this button.
- **Exports:** none on this page. Use **Usage & Cost > Export CSV** (turns) or the Feedback export.
- **Loading:** the list uses table and chart skeletons. The detail page shows **Loading conversation**. The route fallbacks read **Loading conversations…** / **Loading conversation…**.
- **Errors and empty states:**
  - list: **Failed to load conversations.**;
  - stats: **Couldn't load conversation stats.** (retry on each chart);
  - detail, unknown or expired id: **Conversation not found** / **It may have expired or been deleted.** with **Back to Conversations**;
  - detail, other errors: **Failed to load conversation.** with retry;
  - detail, no turns: **No messages found**.
- **Refresh and caching:** list, stats and detail use the shared query cache (`frontend/lib/query-cache.ts`).
  - Data is fresh for 30 s; after that it revalidates in the background on focus or remount.
  - The detail page does **not** revalidate on window focus.
  - The stats response carries an ETag (`304` when unchanged).
  - There is no polling and no manual refresh button. Reload the page to force fresh data.
- **Keyboard:** Esc closes the preview drawer or the inspector. Ctrl/Cmd+K opens the palette. The Turn buttons and chart bars are focusable.

## Light and dark theme

Outcome colours are consistent across the list chart, the chips and the detail page. Answered is green, Ungrounded amber, Blocked and LLM failed red, Conversational neutral. Every chart has **View as table** for colour-independent reading.

## API endpoints used

| Method | Path | Permission | Used for |
|---|---|---|---|
| GET | `/admin/conversations` | `dashboard:read` | Conversations table (filters, paging, sort) |
| GET | `/admin/conversations/stats` | `dashboard:read` | Stat cards, Conversation length, Outcomes by day |
| GET | `/admin/conversations/{id}?turn=` | `dashboard:read` | Preview drawer and detail page (messages + per-turn detail) |
| GET | `/admin/usage/queries/{request_id}` | `dashboard:read` | Single-Query Economic Inspector |
| POST | `/admin/feedback/{message_id}/promote-to-test` | `feedback:triage` | Promote to test |
| GET | `/admin/usage/audience-counts` | `dashboard:read` | Audience tab badges (shared header) |

## Related pages

- [README.md](README.md), [shell.md](shell.md) (audience, range picker, palette), [dashboard.md](dashboard.md)
- [usage-cost.md](usage-cost.md) (turn-level cost and CSV export), [visitors.md](visitors.md) (per-IP history), [feedback.md](feedback.md) (ratings, triage, golden cases)
- [governance.md](governance.md) (traces behind **Trace**), [evaluation.md](evaluation.md) (where promoted cases are measured), [analytics.md](analytics.md) (intent and quality labels), [documents.md](documents.md) (sources and chunks)
- [chat.md](chat.md), [login.md](login.md), [settings.md](settings.md), [user-management.md](user-management.md)

## Known limitations and in-flight changes

- **Cards and table cover different time spans.** The stat cards and charts follow the global range. The table is all time. **Conversations** (card) and the table total therefore usually differ.
- **Times are shown about one UTC offset off on the current build.**
  - Affected: the **Created** / **Last Active** columns, the detail **Started** card, the preview drawer and the turn card times.
  - Cause: these values come from the API without a timezone (naive UTC, e.g. `2026-10-14T10:02:00`), and the browser parses them as **local** time.
  - Correct: the per-turn `created_at` inside `turns[]` (sent with `Z`). It is only used for sorting, not display.
- **Origin IP dropdown** only offers IPs that are on the current page.
- **Sort** is only by last activity (ascending or descending) on the current build.
- **No server cap on `limit`** for `GET /admin/conversations` (the UI only sends 10-100).
- A hand-edited URL with `day` but no `outcome` fails the list request (422) and shows **Failed to load conversations.**
- **Sources** are the retrieved context passages, not citations parsed from the answer, despite the **No cited sources** empty text.
- Conversations can expire (`expires_at`). An expired conversation shows **Conversation not found**.
- The detail endpoint is not audience-scoped: any `dashboard:read` user can open any conversation by id.
- The inspector's **Turn Latency Stages** block never renders, and its uncached-prompt row double-counts cached tokens. See [usage-cost.md](usage-cost.md#known-limitations-and-in-flight-changes).
- Coming in branch `fix/shell-nav-logout-theme-dates`:
  - all list, preview and turn times render through `RelativeTime` ("3h ago", full local time with timezone in the tooltip, naive server times read as UTC), which fixes the offset above;
  - the **Created** column becomes sortable (`created_at_desc` / `created_at_asc`, with a stable tiebreak on id).
- Coming in branch `fix/chat-page-fetch-timeouts`:
  - `GET /admin/conversations/stats` releases its database connection before running its parallel queries;
  - the Next proxy gets a 60 s read timeout with up to 2 retries on 502/503/504;
  - the query cache backs off after failures (1 s doubling up to 60 s).
  These change error and retry behaviour, not the numbers.
