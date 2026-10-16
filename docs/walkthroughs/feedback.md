# Feedback

> The human feedback loop: see how people rate answers, triage the ratings, promote failing turns to golden test cases and export preference pairs.  Route: `/admin/feedback` · Nav: **Feedback** · Permission: `dashboard:read` to view (operator, admin, superadmin); `feedback:triage` to act and `feedback:export` to export (admin, superadmin)

## Purpose and who uses it

Every assistant answer in Chat has a thumbs up and a thumbs down. Each click stores one rating row (`message_feedback`) tied to the assistant message. This page turns those rows into work:

- **Overview**: KPIs and charts about the ratings. Every chart mark filters the review queue.
- **Review Queue**: a keyboard-first triage list. Each rating is marked reviewed, dismissed or promoted.
- **Golden Cases**: answers promoted from feedback into a regression set, with "Run now".
- **Preference Data**: chosen and rejected answer pairs for a later DPO / SFT training run, downloadable as JSONL.
- **Knowledge Gaps**: questions that got low ratings *and* weak retrieval, with links into Documents.

Who uses it:

- **Support and admins** triage negative feedback daily: read the exchange, write the correct answer, then dismiss, review or promote it.
- **Analysts** watch the satisfaction trend, the reasons and the disagreement quadrant.
- **ML / evaluation owners** promote golden cases, run them, and export preference pairs.
- **Operators** (`dashboard:read` only) see every tab, but read-only. Triage buttons, checkboxes, the triage shortcuts and **Run now** are hidden. **Preference Data** shows a lock screen (see [Interactions and states](#interactions-and-states)).

How ratings arrive: the chat thumbs send `rating: 5` (thumbs up) or `rating: 1` (thumbs down) (`THUMBS_UP = 5`, `THUMBS_DOWN = 1` in `frontend/components/MessageBubble.tsx`). A thumbs down can also carry reason chips and a user-written "better answer". The backend stores one row per (message, user). Rating again updates that same row (`upsert_chat_feedback`, unique constraint `uq_message_feedback_message_actor`). The page supports 1 to 5 stars throughout, but the chat UI only produces 1 and 5. Ratings of 2, 3 and 4 come only from other clients or seeded data.

Source: `frontend/components/admin/feedback/FeedbackWorkspace.tsx`, `backend/app/api/routes/admin_feedback.py`, `backend/app/feedback/*`.

## How to get there

- **Sidebar**: **Feedback** (thumbs-up icon). Shown with `dashboard:read` (`frontend/components/Sidebar.tsx`).
- **URL**: `/admin/feedback`.
- **Command palette** (Ctrl/Cmd+K): type "Feedback". This nav item has no extra keywords.
- **From elsewhere**: the Dashboard satisfaction KPI and chart link here. Feedback entity links (for example in Conversation Logs and the activity feed) open `/admin/feedback?message_id=<uuid>` (`frontend/lib/entity-links.ts`).

URL parameters (all kept in the URL, so a filtered queue can be shared; `frontend/components/admin/feedback/use-feedback-url.ts`, `frontend/lib/feedback-filters.ts`):

| Param | Allowed values | Default | Effect |
|---|---|---|---|
| `tab` | `overview`, `queue`, `golden`, `pairs`, `gaps` | `overview`, or `queue` when `message_id` is present | Active tab (`overview` is removed from the URL) |
| `status` | `new`, `reviewed`, `promoted`, `dismissed`, `all` | `new` (or `all` when a valid `message_id` is present) | Queue triage status (server filter) |
| `rating` | `1`-`5` | none | Exact star rating (server filter) |
| `sentiment` | `positive` (4-5), `negative` (1-2) | none | Client-side filter on the loaded page only |
| `reason` | `wrong`, `incomplete`, `off_topic`, `unsafe`, `too_slow`, `unclear` | none | Client-side filter on the loaded page only |
| `outcome` | `answered`, `ungrounded`, `blocked`, `conversational`, `llm_failed` | none | Pipeline outcome of the rated answer (server) |
| `grounded` | `true`, `false` | none | Groundedness of the rated answer (server) |
| `has_comment` | `true`, `false` | none | Rating has a non-empty comment (server) |
| `day` | `YYYY-MM-DD` | none | Ratings created that UTC day (`T00:00:00Z` to `T23:59:59Z`) |
| `message_id` | UUID | none | One assistant message. Its drawer opens automatically |
| `q` | free text, up to 200 chars | none | Search (server) |
| `sort` | `created_at_desc`, `created_at_asc`, `rating_desc`, `rating_asc` | `created_at_desc` | Queue order |
| `audience` | `overview`, `users`, `testing` | global setting | Audience scope (shared by all admin pages) |

Invalid values are ignored. Switching tabs drops `message_id`, so the **Overview** tab stays reachable. A chart click writes the filter, switches to the queue, and pushes a browser history entry, so Back returns to the chart. Editing filters in the queue replaces the URL instead. The global date range picker is **not** shown on this page and has no effect here (see [Known limitations](#known-limitations-and-in-flight-changes)).

## Layout at a glance

```text
+---------------------------------------------------------------------------+
| [thumb] Feedback          [Overview | Users | Testing]                    |
| Human feedback loop: triage ratings, promote golden cases, export ...     |
+---------------------------------------------------------------------------+
| [Overview] [Review Queue (n)] [Golden Cases (n)] [Preference Data]        |
| [Knowledge Gaps (n)]                                                      |
| (filter) Status: New (x)  Rating: 1 (x)   Clear all                       |
+---------------------------------------------------------------------------+
| ! Negative feedback spike: 31% of ratings are 1-2 stars  [Review ...]     |
| +--------------+ +--------------+ +--------------+ +--------------+       |
| |Total feedback| |Positive (4-5)| |Needs improv. | |Coverage      |       |
| |  1,204  ~~~  | |  71.2%  ~~~  | |  22.4%  ~~~  | |  8.3%   ~~~  |       |
| +--------------+ +--------------+ +--------------+ +--------------+       |
| +-------------------------------------------+ +-------------------+       |
| | Satisfaction trend (stacked area)         | | Rating            |       |
| |                                           | | distribution      |       |
| +-------------------------------------------+ +-------------------+       |
| +-----------+ +-----------+ +-------------+ +-----------------+           |
| |Feedback by| |Top reasons| |Triage funnel| |Human vs         |           |
| |outcome    | |           | |             | |groundedness     |           |
| +-----------+ +-----------+ +-------------+ +-----------------+           |
+---------------------------------------------------------------------------+
```

> Screenshot placeholder: `img/feedback-overview.png`: Overview tab with the spike banner, four KPI cards, the satisfaction trend and the four breakdown charts.

```text
+---------------------------------------------------------------------------+
| Review queue (312)                                                        |
| [Search question, comment, user or IDs...] [Status v][Rating v]           |
| [Outcome v][Grounded v][Comment v][Sort v]                                |
| (kbd) j / k move  Enter open  x select  r reviewed  d dismiss  p promote  |
|                          3 selected [Mark reviewed] [Dismiss] [Promote]   |
| [ ] Rating Question and comment  Reasons  Outcome  Status Submitted  Act. |
| [x] *1     How do I reset ...    Wrong    Answered New    12/10 9:14 (v)(x)(f)|
| ...                                                  < Prev   Next >      |
+---------------------------------------------------- drawer -------------+
                                                     | *1 Feedback on "..."|
                                                     | Question / Answer   |
                                                     | Labels and flags    |
                                                     | Sources (n)         |
                                                     | User feedback       |
                                                     | Correct answer      |
                                                     | Reviewer note       |
                                                     | [Mark reviewed r]   |
                                                     | [Dismiss d][Promote]|
                                                     +---------------------+
```

> Screenshot placeholder: `img/feedback-queue.png`: Review Queue with three rows selected, the bulk bar visible and the detail drawer open on a 1-star rating.

## Layout tour

### Header and audience

- Title **Feedback**, subtitle **Human feedback loop: triage ratings, promote golden cases, export preference pairs**.
- The audience tabs (**Overview** / **Users** / **Testing**, `frontend/components/admin/AudienceScopeTabs.tsx`) scope the Overview, Review Queue and Knowledge Gaps data. **Golden Cases** and **Preference Data** are **not** audience-scoped: they always show every case and every pair.
- How the audience is decided for a rating: the client IP is `COALESCE(chat_sessions.created_ip, chat_messages.ip_address, 'Unknown')` (`CLIENT_IP` in `backend/app/feedback/repository.py`). **Testing** = IP in the configured test-IP list. **Users** = every other IP, including `Unknown`. The general rule in `build_audience_filter` (`backend/app/governance/audience.py`) also counts NULL IPs as testing, but this coalesced IP is never NULL, so for feedback that case never applies.

### Tabs

Labels: **Overview**, **Review Queue**, **Golden Cases**, **Preference Data**, **Knowledge Gaps** (ARIA label "Feedback sections").

Tab badges come from the analytics response, which is audience-scoped and ignores queue filters:

- **Review Queue** badge = `triage_funnel.new`, the number of ratings with status `new`.
- **Golden Cases** badge = `triage_funnel.promoted`, the number of *feedback rows* with status `promoted`. This is **not** the number of golden cases. One case can absorb several ratings, and the Golden Cases table itself is not audience-scoped, so the badge and the table total can differ.
- **Knowledge Gaps** badge = number of gap rows returned (at most `FEEDBACK_GAP_LIMIT`, default 10).

### Filter chips

These show on the **Overview** and **Review Queue** tabs only (`FilterChips.tsx`). Each active filter is a removable chip:

- `Status: New`. The default status shows as a chip so you can widen it. Removing it sets `status=all`.
- `Rating: n`
- `Positive (4-5)` / `Negative (1-2)`
- `Outcome: <label>`
- `Grounded` / `Ungrounded`
- `Day: YYYY-MM-DD`
- `Reason: <label>`
- `Has comment` / `No comment`
- `Message: <first 8 chars>`
- `Search: <text>`

**Clear all** removes every filter key and sets `status=all`. When a reason or sentiment chip is active, the note **Reason and sentiment narrow the loaded page.** appears, because those two filters run in the browser only.

### Overview tab

All Overview numbers come from one call, `GET /admin/feedback/analytics?audience=…` (`backend/app/feedback/analytics.py::compute_analytics`). It runs ten SQL aggregates in parallel (`gather_scoped`).

Common rules for every Overview number:

- **Time window**: **all time**, except the trend. Only `daily_trend` (and the KPI sparklines drawn from it) is limited to the last `FEEDBACK_TREND_DAYS` days (default **30**, rolling from "now", so the first day can be partial).
- **Queue filters**: ignored. The KPIs show the unfiltered audience total (the "F3" rule).
- **Timezone**: trend days are `CAST(message_feedback.created_at AS date)` on a naive-UTC column, so they are **UTC** days.
- **Positive / negative bands**: positive = rating >= 4, negative = rating <= 2, neutral = 3 (`POSITIVE_MIN`, `NEGATIVE_MAX`, `NEUTRAL`).
- **Caching**: the response carries a weak ETag and `Cache-Control: private, max-age=15` (`HTTP_CACHE_MAX_AGE_SECONDS`, `backend/app/core/http_cache.py`). In the browser, `useApiQuery` treats data as fresh for 30 s (`DEFAULT_STALE_TIME_MS`) and revalidates on window focus or when the tab becomes visible. There is **no polling** and no refresh button. Any triage action invalidates the analytics key, so the KPIs and badges update after you act.

#### Negative feedback spike banner

- Shown when `negative_ratio > 25` **and** `total >= 5` (`SPIKE_NEGATIVE_RATIO`, `SPIKE_MIN_TOTAL` in `FeedbackOverview.tsx`).
- Text: **Negative feedback spike: {x}% of ratings are 1-2 stars**, and below it **Above the 25% threshold. Review the low-rated answers and promote failing turns to golden cases.**
- The button **Review negative feedback** opens the queue with `sentiment=negative`.

#### KPI cards

Each card has a sparkline of the last 30 UTC days. Clicking a sparkline point opens the queue filtered to that `day`.

| Card label | Value | Hint under the value | Sparkline |
|---|---|---|---|
| **Total feedback** | `total` | **All ratings for this audience (not the queue filters)** | ratings per day |
| **Positive (4-5 stars)** | `positive_ratio` % | `{positive_count} ratings` | positive ratings per day |
| **Needs improvement (1-2 stars)** | `negative_ratio` % | `{negative_count} ratings` | negative ratings per day ("good direction" is down) |
| **Coverage** | `feedback_rate` % | **Share of answers that received a rating** | ratings per day (same series as Total) |

How it's computed (`backend/app/feedback/analytics.py`):

- `total` = `COUNT(message_feedback.id)` after the exchange joins and the audience filter (`_summary`). `total_feedback` is the same number.
- `positive_ratio` = `round(positive_count / total * 100, 1)`, where `positive_count` = ratings >= 4.
- `negative_ratio` = `round(negative_count / total * 100, 1)`, where `negative_count` = ratings <= 2. Neutral (3) ratings stay in the denominator, so positive + negative can add up to less than 100%.
- `feedback_rate` (Coverage) = `round(total / assistant_messages * 100, 1)` (`_coverage`). `assistant_messages` = `COUNT(chat_messages)` where `role = 'assistant'`, joined to its session and audience-filtered on `chat_messages.ip_address`. This counts ratings, not distinct rated messages. Two users rating the same answer count twice.
- Every ratio is `0.0` when its denominator is 0. Percentages show with one decimal.
- **Known issue**: on this branch the Coverage denominator filters on the assistant message's own `ip_address`, which is empty for assistant rows. For **Users**, Coverage therefore reads **0%** or wrong. For **Testing**, every assistant message counts in the denominator. **Overview** is correct (no filter). This is fixed in `feat/realistic-demo-data`, see [Known limitations](#known-limitations-and-in-flight-changes).

#### Satisfaction trend

- Title **Satisfaction trend**, subtitle **Positive and negative ratings per day. Click a day to filter the queue.**
- A stacked area of `positive` and `negative` per day. The table view (**View as table**) adds **Avg rating**.
- How it's computed (`_trend`): rows with `created_at >= now - FEEDBACK_TREND_DAYS`, grouped by UTC date. Each day carries `total`, `positive` (>=4), `negative` (<=2) and `avg_rating` = `round(AVG(rating), 2)`. Days with no ratings are **missing**, not zero.
- Click: sets `day` and opens the queue.
- Empty: the default chart empty message **No data for this period**.

#### Rating distribution

- Title **Rating distribution**, subtitle **Click a rating to filter.**
- Bars **5 stars** through **1 star**: green for 4-5, amber for 3, red for 1-2.
- How it's computed (`_distribution`): `COUNT` grouped by `rating`, all time, audience-scoped. Missing ratings are filled with 0.
- Click: sets `rating=n` and opens the queue.

#### Feedback by outcome

- Title **Feedback by outcome**, subtitle **What the pipeline did on rated answers.**
- One bar per pipeline outcome of the rated assistant message: **Answered**, **Ungrounded**, **Blocked**, **Conversational**, **LLM failed**. Sorted by count.
- How it's computed (`_by_outcome`): ratings grouped by `chat_messages.outcome`. Legacy rows with no outcome count as `answered`. The UI keeps only the five known outcomes and plots `total` per outcome. The API also returns per-outcome positive and negative counts, which the UI does not draw.
- Click: sets `outcome` and opens the queue.

#### Top reasons

- Title **Top reasons**, subtitle **Thumbs-down reason chips.**, empty text **No reasons given yet**.
- Labels: **Wrong**, **Incomplete**, **Off topic**, **Unsafe**, **Too slow**, **Unclear**.
- How it's computed (`_top_reasons`): `jsonb_array_elements_text(reason_tags)`, counted per tag. The top 10 are returned by count, then name. Unknown tags are dropped in the UI. A rating with two reasons counts once for each reason.
- Click: sets `reason` (client-side filter) and opens the queue.

#### Triage funnel

- Title **Triage funnel**, subtitle **New, reviewed, promoted to golden cases.** Stages **New**, **Reviewed**, **Promoted**. The table column is **Reached**.
- How it's computed: the API returns plain counts per `triage_status` (`_funnel`). The UI makes them cumulative ("everything that reached this stage"):
  - **New** = `new + reviewed + promoted`
  - **Reviewed** = `reviewed + promoted`
  - **Promoted** = `promoted`
  - Dismissed rows are left out of the funnel. A rating promoted straight from `new` still counts under **Reviewed**.
- Click: sets `status` to that stage and opens the queue. The queue filter is the *exact* status, so a **New** bar showing 300 can open a queue of only the 120 rows that are still `new`.

#### Human vs groundedness

- Title **Human vs groundedness**, subtitle **Disagreement quadrant. Click a quadrant to filter.** Axes **Human rating** (negative/positive) and **Grounded** (no/yes). The top-left and bottom-right quadrants are labelled **Disagree**.
- Quadrants: **Liked and grounded**, **Liked but ungrounded**, **Disliked but grounded**, **Disliked and ungrounded**.
- How it's computed (`_disagreement`): `COUNT` of ratings with `rating >= 4` or `<= 2`, crossed with `chat_messages.grounded IS TRUE` or `IS FALSE`. Neutral ratings and answers with unknown groundedness are excluded. Bubble size is the count.
- Click: sets `sentiment` (client-side) and `grounded` (server) and opens the queue. "Disliked but grounded" usually points to retrieval returning the wrong passage. "Liked but ungrounded" points to answers that pleased the user without document support.

Source: `frontend/components/admin/feedback/FeedbackOverview.tsx`, `backend/app/feedback/analytics.py`.

### Review Queue tab

A table card titled **Review queue** with the server total as a badge. The description reads **Triage with the keyboard: j / k to move, r / d / p to act, Enter to open.**, or for operators **Read-only: your role can view feedback but not triage it.**

How it's loaded:

- `GET /admin/feedback?audience=&limit=25&…filters…&cursor=` (`list_feedback`). The page size is **25** (`QUEUE_PAGE_SIZE`). Paging is keyset-based (`next_cursor`, **Next** / **Prev**). The next page is prefetched while idle. A filter or sort change resets to page 1.
- One SQL statement joins each rating to its assistant message, the user question (same `turn_index`), the latest online-evaluation record (retrieved chunks and confidence) and the request latency (`backend/app/feedback/repository.py::list_feedback`).
- The badge is the server `total` for the **server-side** filters. Reason and sentiment filters only hide rows on the loaded page, so the visible row count can be lower than the badge.
- Default view: `status=new`, newest first.

Toolbar (`TableToolbar`):

- Search box, placeholder **Search question, comment, user or IDs…**, debounced 300 ms. It searches (ILIKE, server side) the comment, the rater's email (`actor`), the client IP, the user question text, the session ID and the message ID.
- **Status**: **All statuses**, **New**, **Reviewed**, **Promoted**, **Dismissed**.
- **Rating**: **All ratings**, **5 stars** … **1 star**.
- **Outcome**: **All outcomes**, **Answered**, **Ungrounded**, **Blocked**, **Conversational**, **LLM failed**. A rating whose message has no outcome is matched as `answered`.
- **Grounded**: **Any**, **Grounded**, **Ungrounded**.
- **Comment**: **Any**, **Has comment**, **No comment**.
- **Sort**: **Newest first**, **Oldest first**, **Lowest rating**, **Highest rating**. Rating sorts break ties newest first.

Shortcut hint line: **j / k** move, **Enter** open, **x** select, **r** reviewed, **d** dismiss, **p** promote. Operators see only the first two.

Columns: checkbox (triage roles only) · **Rating** (star chip: green 4-5, amber 3, red 1-2) · **Question and comment** (question, then the comment or **No comment**) · **Reasons** · **Outcome** (plus `· ungrounded` when grounded is false) · **Status** · **Submitted** (browser-local date and time) · **Actions** (triage roles only).

Row actions (icon buttons with tooltips **Mark reviewed (r)**, **Dismiss (d)**, **Promote to test (p)**). A button is disabled when the transition is not allowed:

| From | Allowed to |
|---|---|
| `new` | `reviewed`, `promoted`, `dismissed` |
| `reviewed` | `promoted`, `dismissed` |
| `dismissed` | `new` (restore) |
| `promoted` | nothing (final) |

The same table is enforced on the server (`backend/app/feedback/service.py::TRANSITIONS`). An illegal move returns 409 `Invalid transition: a -> b`.

Bulk actions: when rows are selected, the bar shows **{n} selected** with **Mark reviewed**, **Dismiss** and **Promote**. Only selected rows that can make the transition are sent, at most 200 (`MAX_BULK_IDS`). The server runs the whole batch in one transaction. If any ID is missing (404) or cannot transition (409), **nothing** changes. On success the toast reads **Marked reviewed: N items** (or **Dismissed: …**, **Promoted to golden cases: …**). The selection clears and every queue page is refetched.

Source: `frontend/components/admin/feedback/ReviewQueue.tsx`, `triage.ts`, `backend/app/api/routes/admin_feedback.py`.

### Feedback detail drawer

Opens on a row click, on **Enter**, or automatically for a `message_id` deep link (`FeedbackDrawer.tsx`).

- Header: the star chip, **Feedback on "<first 60 chars of the question>"**, the submitted time and the rater, and the status badge. The drawer has **Previous record** and **Next record** arrows and **Open full view**, which goes to `/admin/conversations/<conversation_id>`.
- **Question** and **Answer**: the full texts.
- **Labels and flags**: **Outcome**, **Grounded** (Yes/No/—), **Confidence** (2 decimals, from the latest online-eval record), **Latency** (ms; the request latency record, falling back to the eval record's latency), **Cost** (`$0.0000`), **Status**, plus guardrail flag chips.
- **Sources (n)**: the cited chunks with source name, section heading and retrieval score (3 decimals), or **No sources were cited.**
- **User feedback**: the comment (or *No comment*), the reason chips, and **Reviewed by {email} on {time}** once triaged.
- **Correct answer**: a text area labelled **Write the correct answer** (max 4000 characters), placeholder **Becomes the preferred (chosen) answer in the preference export.**, and the button **Save answer**. It is saved through `PATCH /admin/feedback/{id}` with `corrected_answer`, PII-masked on the server before storage. Success toast: **Correct answer saved**, "It is now the chosen side of this preference pair." The same field holds the user's own suggested answer from the chat thumbs-down form. The reviewer's text overwrites it.
- **Reviewer note**: a text area labelled **Note** (max 2000 characters), placeholder **Why this was reviewed, dismissed or promoted.**, button **Save note**, toast **Note saved**.
- Footer (triage roles only): **Mark reviewed** `r`, **Dismiss** `d`, **Promote to test** `p`, and for dismissed rows **Restore to queue**.

Every PATCH sends the row `version` you loaded (optimistic locking). If someone else changed the row first, the server returns 409. The page rolls back and shows **Changed by someone else**: "This record was updated elsewhere. The latest version has been loaded; review it and try again."

### Golden Cases tab

A table card titled **Golden cases**, described as **Answers promoted from feedback into the regression suite, with their last evaluation.**, with a header link to **Evaluation (Live)** (`/admin/evaluation-live`).

- Data: `GET /admin/feedback/golden?limit=100`. It lists the `eval_promoted_cases` table, newest promotion first. It is **not** audience-scoped. The UI loads only the first 100 cases and has no paging.
- Columns:
  - **Case**: the case ID, `promoted_<message uuid hex>`.
  - **Question**: plus the reason labels.
  - **Expectation**: **Grounded answer** or **Not grounded**, then `· <expected_outcome>`, plus **Has reference answer** when a corrected answer was captured.
  - **Promoted**: date and promoter.
  - **Last evaluation**: **Passed** / **Failed**, run time, `· confidence x.xx`, `· <error>`, or **Not run yet**.
  - **Run**: the **Run now** button, triage roles only.

What promotion stores (`service._promote_message`):

- `query`: the user question of that turn (or "Unknown user query").
- `expect_grounded`: whether the *promoted answer* was grounded.
- `case_type`: `robustness` if it was grounded, otherwise `negative`.
- `expected_outcome`: the answer's outcome, defaulting to `answered`.
- `reference_answer` and `reason_tags`: taken from the best rating on that message. "Best" means a rating with a corrected answer first, then the most recently updated.

Promotion also flips **every** open (`new` or `reviewed`) rating of that message to `promoted` and stamps them with the case ID. Promoting the same message again returns the existing case (`created: false`).

How golden cases are used by evaluation:

- **Run now** calls `POST /admin/feedback/golden/{case_id}/run`, which queues the job `eval.golden_run`. The job is deduplicated per case: a second click while one is queued returns `already_queued` (toast **Already queued**). The worker runs the real query pipeline (`evaluate_case` in `backend/app/evaluation/evaluator.py`). It then stores `last_run_at`, `last_run_passed` and `last_run_detail` (grounded, confidence, guardrail flags, latency, error).
- **Pass rule**: promoted cases carry no expected documents, keywords or flags, so `passed` reduces to "the new answer's groundedness equals `expect_grounded`, and there was no error". The stored `reference_answer` is **not** compared with the new answer.
- The offline evaluation CLI (`backend/scripts/run_evaluation.py`) merges every promoted case into its dataset through `load_promoted_cases`. That function reads the DB and falls back to `data/promoted_test_cases.json`.
- Job chip next to **Run now**: **Queued** → **Running…** → **Passed** / **Failed**. If nothing changes after 40 polls of 3 s (about 2 minutes), the chip reads **Still running**. A failed enqueue shows **Could not queue**. The UI polls the golden list because there is no job-status endpoint.

### Preference Data tab

A table card titled **Preference data**, described as **Chosen vs rejected answers for a later DPO / SFT run. Preview of the first 50 pairs; text is PII-masked.**

- Requires `feedback:export` (admin, superadmin). Without it the tab shows the empty state **Export needs the feedback:export permission**, "Admins and superadmins can preview and download preference pairs.", and no request is sent.
- Controls: **Complete pairs only** checkbox, **Download JSONL** button (**Downloading…** while it runs).
- Badge: **{complete} complete · {incomplete} incomplete**, plus ` (preview)` when the stream had more than 50 lines. These counts describe the 50-pair preview only, not the whole export.
- Columns: **Prompt**, **Chosen**, **Rejected**, **Rating** (with reasons), **Quality** (**Complete** or **Incomplete: chosen, …**). Missing cells show a **Missing** chip, and incomplete rows are tinted.
- Preview mechanics: the page calls the same export endpoint, reads the first 50 lines from the stream, then cancels it (`previewPairs` in `frontend/lib/feedback-api.ts`).

Export format (`GET /admin/feedback/export?format=jsonl`, `backend/app/feedback/service.py::build_pair`):

- The response is `application/x-ndjson`, streamed from a server-side cursor in batches of `FEEDBACK_EXPORT_BATCH_SIZE` (default 500). It is saved as `feedback_pairs_YYYYMMDD_HHMMSS.jsonl` (UTC stamp), with `Cache-Control: no-store`.
- One JSON object per rating, oldest first, with these keys: `feedback_id`, `message_id`, `prompt`, `chosen`, `rejected`, `rating`, `reason_tags`, `source_ids`, `triage_status`, `complete`, `missing`.
- Pairing rule:

| Situation | `chosen` | `rejected` |
|---|---|---|
| A corrected answer exists (user or reviewer) | corrected answer | original answer |
| No correction, rating 4-5 | original answer | `null` |
| No correction, rating 1-3 | `null` | original answer |

- `complete` is true only when prompt, chosen and rejected are all present, so **in practice only ratings with a corrected answer are complete**. `missing` lists the empty sides.
- `prompt` and the original answer are PII-masked at export time. Corrected answers were masked when stored. `source_ids` are the chunk IDs from the latest eval record.
- Server filters: `triage_status=<one status>`, `include_dismissed` (default false, so dismissed ratings are excluded), and `complete_only`. The UI only sends `complete_only`. **The export is not audience-scoped**: it always covers every audience.
- Every call, **including each preview load**, writes an audit event `feedback_exported` (actor, format, filters).

### Knowledge Gaps tab

A ranked list of questions that drew low ratings while retrieval looked weak.

- Each row shows the question (PII-masked) and the line `{n} low rating(s) · avg {x.x} stars · retrieval confidence {0.00|unknown} · last seen {date}`. The buttons are **Show in queue**, **Open document**, and **Add document** (the last only with `documents:write`).
- Empty state: **No knowledge gaps detected**, "Gaps appear when low-rated answers also had low retrieval confidence."
- How it's computed (`analytics._knowledge_gaps`):
  - Starts from ratings <= 2 whose user question is known.
  - Keeps those where *any* of these hold: the latest online-eval `confidence < FEEDBACK_GAP_CONFIDENCE_THRESHOLD` (default **0.5**), the answer was `grounded = false`, or the outcome is `ungrounded` or `llm_failed`.
  - Groups by `lower(trim(question))` and returns the top `FEEDBACK_GAP_LIMIT` (default **10**) by count.
  - `avg_rating` and `avg_confidence` are averaged over the group. `last_seen` is the newest rating.
  - The UI re-sorts by count (desc), then average rating (asc), then confidence (asc).
  - All time, audience-scoped.
- **Show in queue** filters the queue to `message_id = <one representative message>` with `status=all`. That is one message from the group (the highest UUID string), not every rating in the gap.
- **Open document** opens `/documents?q=<first 120 chars of the question>`.
- **Add document** links to `/documents?action=upload`. The Documents page does not read `action`, so it only opens the list and you must click upload yourself.

## Common tasks

### 1. Triage negative feedback (daily routine)

1. Open **Feedback**. If the red spike banner shows, click **Review negative feedback**. Otherwise click the 1-2 star bars in **Rating distribution**, or open **Review Queue** and set **Rating** to **1 star**.
2. Keep **Status: New** so you only see untriaged work. Set **Sort** to **Lowest rating** if you mix ratings.
3. Press **j** / **k** to move and **Enter** to open the drawer. Read **Question**, **Answer**, **Sources** and **Labels and flags**.
4. Decide:
   - The answer was fine, or the complaint is noise: press **d** (Dismiss). The toast offers **Undo**, which restores the rating to `new`.
   - It was a real problem and you have handled it (for example, a document was fixed): add a **Note**, **Save note**, then press **r** (Mark reviewed).
   - The answer was wrong and you want a regression test: see recipe 2.
5. With the drawer open, the next row opens when you press **j**. Shortcuts act on the drawer's row.
6. For a batch of obvious noise, tick rows with **x** (or the checkboxes) and click **Dismiss** in the bulk bar. Up to 200 rows go in one all-or-nothing transaction.

### 2. Promote a failing answer to a golden case

1. In the queue, open the rating's drawer.
2. Optional but recommended: write the right answer in **Write the correct answer** and click **Save answer**. This makes the preference pair complete and becomes the case's `reference_answer`.
3. Click **Promote to test** (or press **p**). The toast reads **Promoted to golden cases**, "Case promoted_… added to the golden set." If the message was promoted before, it reads "Already in the golden set as …".
4. Open **Golden Cases**, find the case, and click **Run now**. Wait for the chip to turn **Passed** or **Failed**.
5. Remember what the case asserts: the new answer's groundedness must match the promoted answer's groundedness. A wrong but grounded answer promoted as a case expects "grounded" again, so the case cannot tell you whether the *content* is fixed. Read the answer itself in Evaluation or Conversation Logs.

### 3. Export preference data for training

1. Make sure you have `feedback:export` (admin or superadmin).
2. Open **Preference Data**. Check the preview: rows tinted amber are incomplete (**Incomplete: chosen** usually means a low rating with no corrected answer).
3. To raise the complete count, go back to the queue, filter **Rating: 1 star**, and write corrected answers in the drawer.
4. Tick **Complete pairs only** to export only usable DPO pairs, then click **Download JSONL**. The file is `feedback_pairs_<UTC stamp>.jsonl`.
5. The download covers all audiences and excludes dismissed ratings. For a status-specific export, call the API directly, for example `GET /admin/feedback/export?format=jsonl&triage_status=reviewed&complete_only=true`.

### 4. Find content gaps and fix documents

1. Open **Knowledge Gaps**. Rows at the top have the most low ratings.
2. Click **Open document** to search Documents for the question and check whether the knowledge base covers it.
3. If not, upload or fix a document (needs `documents:write`). **Add document** opens the Documents list, so click upload there.
4. Back on the gap row, click **Show in queue**, mark the representative rating reviewed, and add a note naming the fixed document.

### 5. Share a filtered queue with a colleague

1. Apply filters, for example by clicking **Disliked but grounded** in the quadrant.
2. Copy the browser URL (for example `/admin/feedback?tab=queue&sentiment=negative&grounded=true`) and send it. The audience is not in this URL unless the global audience param is already there, so tell them which audience to pick.

## Interactions and states

- **Chart drill-down**: every chart mark and KPI sparkline point calls `onFilter`. It writes the filter to the URL, switches to **Review Queue** and pushes a history entry. Each chart card has **View as table** / **View as chart**.
- **Keyboard shortcuts** (Review Queue; ignored while typing in an input or with Ctrl, Cmd or Alt held):
  - **j** / **k**: move focus down / up (and move the open drawer).
  - **Enter**: open the drawer.
  - **x**: toggle selection.
  - **r**: mark reviewed.
  - **d**: dismiss.
  - **p**: promote.
  - **x**, **r**, **d** and **p** need `feedback:triage`. The drawer closes with Escape (`DrawerShell`).
- **Optimistic updates**: status changes show at once and roll back on failure. A 409 shows the **Changed by someone else** toast. Any other failure shows **Action failed** with the server message.
- **Undo**: only dismissal can be undone (the toast's Undo restores the rating to `new`, toast **Restored to the queue**). Reviewed and promoted cannot be reversed from the UI.
- **Loading states**:
  - Skeleton cards: **Loading feedback analytics**.
  - Skeleton rows: **Loading feedback**, **Loading golden cases**, **Loading preference pairs**, **Loading knowledge gaps**.
  - The whole page in its Suspense fallback: **Loading feedback**.
- **Error states** (each has a retry button):
  - **Could not load feedback analytics**
  - **Could not load the review queue**
  - **Could not load golden cases**
  - **Could not load the preview**
  - **Could not load knowledge gaps**
- **Empty states**:
  - Queue with the default status: **Nothing left to triage**.
  - Queue with other filters: **No feedback matches these filters**, with "Remove a filter chip to widen the queue." or, when a reason or sentiment filter is on, "Reason and sentiment filters apply to the loaded page; try the next page."
  - Golden: **No golden cases yet**, "Promote a rated answer from the Review Queue (p) to add it to the regression set."
  - Pairs: **No preference pairs yet**, "Ratings with an answer become pairs; a corrected answer completes them."
  - Gaps: **No knowledge gaps detected**.
  - Top reasons: **No reasons given yet**.
- **Exports**: JSONL only, `feedback:export`. See [Preference Data tab](#preference-data-tab).
- **When data looks missing**:
  - If Coverage reads 0% under **Users**, that is the known denominator bug.
  - If the queue shows fewer rows than its badge, a reason or sentiment chip is filtering the page in the browser.
  - If a rating you expect is absent, check the **Status** filter (default **New**) and the audience.
  - A user who re-rates a dismissed or reviewed answer updates the same row **without** resetting its status, so a changed rating does not reappear in the **New** queue.

## Light and dark theme

Chart colours come from the shared chart tokens (`useChartTheme`): ratings use status ok/warn/danger, and Top reasons uses the warn colour. Status badges use `status-*` tokens and keep their contrast in both themes. Dismissed rows are shown with strikethrough text rather than colour alone.

## API endpoints used

| Method | Path | Permission | Used for |
|---|---|---|---|
| GET | `/admin/feedback/analytics?audience=` | `dashboard:read` | Overview KPIs and charts, tab badges, Knowledge Gaps |
| GET | `/admin/feedback?audience=&status filters…&limit=&cursor=` | `dashboard:read` | Review Queue rows and drawer data |
| PATCH | `/admin/feedback/{feedback_id}` | `feedback:triage` | Single transition, review note, corrected answer (needs `version`) |
| POST | `/admin/feedback/bulk` | `feedback:triage` | Bulk reviewed / dismissed / promoted (1-200 IDs, all-or-nothing) |
| POST | `/admin/feedback/{message_id}/promote-to-test` | `feedback:triage` | Promote a message to a golden case (row action **p**) |
| GET | `/admin/feedback/golden?limit=&offset=` | `dashboard:read` | Golden Cases table |
| POST | `/admin/feedback/golden/{case_id}/run` | `feedback:triage` | **Run now** (202, deduplicated job `eval.golden_run`) |
| GET | `/admin/feedback/export?format=jsonl&complete_only=&triage_status=&include_dismissed=` | `feedback:export` | Preference preview and **Download JSONL** |
| POST | `/sessions/{sid}/messages/{mid}/feedback` | signed-in chat user (rate limit 30/min) | Where ratings come from (Chat, not this page) |

Every write is audited: `feedback_triaged`, `feedback_promoted_to_eval_case`, `golden_case_run_requested` and `feedback_exported`.

## Related pages

- [README.md](README.md): index of walkthroughs
- [shell.md](shell.md): sidebar, command palette, audience tabs
- [dashboard.md](dashboard.md): the satisfaction KPI that links here
- [chat.md](chat.md): where users give thumbs up/down, reasons and a better answer
- [conversation-logs.md](conversation-logs.md): **Open full view** target
- [evaluation.md](evaluation.md): Evaluation (Live) and offline runs that consume golden cases
- [documents.md](documents.md): fixing knowledge gaps
- [analytics.md](analytics.md): intent and quality labels per turn
- [governance.md](governance.md): audit log entries for triage and export
- [user-management.md](user-management.md): granting admin (triage, export)
- [visitors.md](visitors.md), [usage-cost.md](usage-cost.md), [settings.md](settings.md), [login.md](login.md)

## Known limitations and in-flight changes

- **No date range**: the global date range picker is not shown here. Overview figures are all-time, except the 30-day trend (UTC days). The queue can only be narrowed to one `day` via a chart click.
- **Coverage bug (Users / Testing)**: the denominator filters assistant messages by their own `ip_address`, which is empty, so Coverage is 0% or wrong for Users and Testing (`_coverage`, `admin_feedback.get_feedback_analytics`).
- **Client-side filters**: `reason` and `sentiment` have no API parameter. They filter only the loaded 25-row page, and the badge total ignores them.
- **Funnel click mismatch**: funnel bars are cumulative, but a click filters the exact status.
- **Coverage sparkline** shows ratings per day (the same series as Total feedback), not a coverage rate.
- **Golden Cases** shows only the first 100 cases (`GOLDEN_PAGE_SIZE`) with no paging, and it is not audience-scoped. Its tab badge counts promoted ratings, not cases.
- **Golden pass/fail** checks groundedness only. The reference answer is stored but not graded.
- **Export** is not audience-scoped. The UI cannot choose `triage_status` or `include_dismissed`. Each preview load writes a `feedback_exported` audit row.
- **Knowledge Gaps**: **Show in queue** opens one representative message only. **Add document** does not open the upload dialog (`/documents?action=upload` is not read).
- **`by_intent`**: the analytics API returns feedback counts per intent once `turn_insights` exists, but no chart draws it.
- **Re-rating** by a user keeps the old triage status (the upsert does not touch `triage_status`).
- **Ratings 2-4**: the chat UI only sends 1 or 5. Middle ratings and the neutral band appear only with seeded or API-submitted data.
- Coming in branch `feat/realistic-demo-data`:
  - The Coverage denominator filters on the session IP (`ChatSession.created_ip`), which fixes "feedback rate was 0 for audience=users".
  - A demo seeder (`backend/scripts/seed_realistic_activity.py`) creates ratings 1-5 with comments, reason tags and some reviewed/dismissed triage states, so every chart has data. It creates no golden cases.
- Coming in branch `fix/shell-nav-logout-theme-dates`:
  - **Submitted**, **Promoted**, **Last evaluation**, **last seen** and **Reviewed by … on** become relative, timezone-aware times (`RelativeTime`).
  - Each exported pair gains `created_at`, and the Preference Data preview gains a **Rated** column.
  - The flat sidebar removes the collapsible **Admin** group header. The **Feedback** item itself is unchanged.
- Coming in branch `fix/chat-page-fetch-timeouts`:
  - `GET /admin/feedback/analytics` releases its pooled DB connection before the parallel aggregates run (`release_connection`).
  - Parallel aggregates share a process-wide cap (`fanout_slots`).
  - The browser cache backs off after a failed request (1 s, 2 s … up to 60 s) before focus or mount refetches retry. **Retry** buttons still refetch immediately.
- `perf/dashboard-load-speed` does not change this page.
