# Entity pages: stats and per-entity endpoints (contract)

**Consumer:** PR-22 `feat/ui-entity-pages-deeplinks` (planchanges4 F3.2, F3.3, F3.4).
**Producer:** PR-14 `feat/dashboard-chart-aggregations` (planchanges4 B7.2, B7.3).
**Client types:** `frontend/lib/entity-stats-api.ts` (the TypeScript interfaces are the source of truth; this
page explains them).

Everything here is **additive**. Existing endpoints keep their current response fields; new fields are only
added. The frontend already renders the existing data and shows an empty state on charts whose endpoint answers
`404`, `405` or `501` (treated as "not deployed"). It never fabricates values.

## Conventions

| Topic | Rule |
|---|---|
| Timestamps | ISO-8601 with an explicit UTC offset (`2026-10-15T09:12:44Z`). |
| Days | `YYYY-MM-DD`, bucketed in UTC (the `tz` field echoes the bucketing zone, `"UTC"` for now). |
| Range params | `from` / `to` are inclusive `YYYY-MM-DD` days. Both optional; default is the last 7 days ending today. `to < from` is `422`. A span above `usage_max_range_days` is `422`. |
| Audience | `audience=overview|users|testing` (default `overview`), same semantics as `GET /admin/users`. Unknown values are `422`. |
| Money | `cost_usd` is a float in US dollars (not cents). |
| Durations | `*_ms` are float milliseconds. |
| `null` | "Not measured / not applicable". `0` means "measured and zero". Lists are never `null` (empty list instead). |
| Zero-filling | Day series contain every day of the range (zeros included). Heatmap and weekly-hit lists may omit zero cells. |
| Errors | `application/problem+json` (PR-03). `401` unauthenticated, `403` missing permission, `404` unknown id, `422` validation. |
| Guards | Reads: `require_permission(Permission.DASHBOARD_READ)`. Promote to test: `Permission.FEEDBACK_TRIAGE` (existing route). Add each new route to `tests/test_route_guards.py`. |
| Caching | Stats endpoints use `cached_response` (ETag) and `gather_scoped`; per-entity endpoints are not cached. |
| Search | Every `q` is trimmed, max 200 characters (`422` above), LIKE-escaped with `escape_ilike_pattern`. |

## B7.2 Visitors

### `GET /admin/visitors/stats`

Query: `audience`, `from`, `to`.

```json
{
  "from": "2026-10-09",
  "to": "2026-10-15",
  "tz": "UTC",
  "kpis": {
    "visitors": 120,
    "new_visitors": 30,
    "returning_visitors": 90,
    "active_now": 4,
    "sessions": 340,
    "queries": 1200,
    "avg_sessions_per_visitor": 2.83
  },
  "new_vs_returning": [
    { "day": "2026-10-09", "new": 4, "returning": 12 }
  ],
  "heatmap": [
    { "weekday": 0, "hour": 9, "sessions": 12 }
  ]
}
```

- A **visitor** is one client IP (same grouping as `GET /admin/users`). **New** on a day = the visitor's first
  ever session started that day; **returning** = active that day with an earlier first session. Each visitor
  counts once per day.
- `kpis.visitors` = distinct visitors active in the range; `new_visitors + returning_visitors = visitors`.
- `kpis.active_now` = visitors with activity in the last 30 minutes, independent of the range.
- `avg_sessions_per_visitor` is `null` when `visitors` is 0.
- `heatmap.weekday`: `0` = Monday ... `6` = Sunday. `hour`: `0..23`. `sessions` = sessions **started** in that
  slot within the range.

### `GET /admin/users` (existing) – additive filters

New optional query params used when a heatmap cell is clicked:

| Param | Type | Meaning |
|---|---|---|
| `weekday` | int `0..6` | Only visitors with a session started on this weekday ... |
| `hour` | int `0..23` | ... in this hour. Both or neither (`422` if only one). |
| `from`, `to` | day | Window for the `weekday`/`hour` filter (same defaults as above). |

The page URL mirrors them: `/admin/users?weekday=0&hour=9`.

### `GET /admin/visitors/{visitor_id}/timeline`

`visitor_id` is the id used by `GET /admin/users/{id}`: `client-<ip>` or a user UUID. Unknown id is `404`.
Query: `from`, `to` (apply to `queries`, `cost_series` and `feedback`; `sessions` are the sessions active in
the range).

```json
{
  "visitor_id": "client-203.0.113.7",
  "ip_address": "203.0.113.7",
  "sessions": [
    {
      "session_id": "6f1c...",
      "title": "Leave policy",
      "started_at": "2026-10-14T09:12:44Z",
      "ended_at": "2026-10-14T09:31:02Z",
      "turns": 4,
      "cost_usd": 0.0123
    }
  ],
  "queries": [
    {
      "request_id": "req_ab12",
      "session_id": "6f1c...",
      "turn_index": 0,
      "query": "How many leave days do I get?",
      "outcome": "answered",
      "grounded": true,
      "created_at": "2026-10-14T09:12:44Z",
      "latency_ms": 1840.5,
      "cost_usd": 0.0031
    }
  ],
  "cost_series": [{ "day": "2026-10-14", "cost_usd": 0.0123, "queries": 4 }],
  "feedback": [
    {
      "id": "fb_1",
      "message_id": "assistant-message-uuid",
      "session_id": "6f1c...",
      "turn_index": 0,
      "request_id": "req_ab12",
      "rating": 2,
      "comment": "Out of date",
      "reason_tags": ["wrong"],
      "created_at": "2026-10-14T09:13:10Z"
    }
  ],
  "truncated": { "sessions": false, "queries": false, "feedback": false }
}
```

- `sessions` ordered by `started_at` ascending, max 200. `ended_at` = last activity (`>= started_at`).
- `queries` most recent first, max 100. `outcome`, `latency_ms`, `cost_usd` may be `null` (not recorded).
- `feedback.message_id` is the **assistant** message id (the id the Feedback page and
  `POST /admin/feedback/{message_id}/promote-to-test` use). `rating` is `1..5`. `reason_tags` uses the
  `ReasonTag` allow-list from `app/schemas/feedback.py`.
- `truncated.<list>` is `true` when the list was cut at its maximum.

## B7.2 Conversations

### `GET /admin/conversations/stats`

Query: `audience`, `from`, `to`, optional `ip` and `q` (same meaning as the list, so the charts match the table).

```json
{
  "from": "2026-10-09",
  "to": "2026-10-15",
  "totals": { "conversations": 290, "turns": 1100, "avg_turns": 3.79 },
  "turns_histogram": [
    { "min_turns": 1, "max_turns": 1, "conversations": 80 },
    { "min_turns": 2, "max_turns": 3, "conversations": 120 },
    { "min_turns": 4, "max_turns": 6, "conversations": 60 },
    { "min_turns": 7, "max_turns": 9, "conversations": 20 },
    { "min_turns": 10, "max_turns": null, "conversations": 10 }
  ],
  "outcome_by_day": [
    { "day": "2026-10-09", "counts": { "answered": 30, "ungrounded": 2, "blocked": 1, "conversational": 5, "llm_failed": 0 } }
  ]
}
```

- A **turn** = one user message and its assistant answer (`chat_messages.turn_index`).
- Histogram bins are contiguous, inclusive, ascending; only the last bin may be open-ended (`max_turns: null`).
- `outcome_by_day.counts` counts assistant turns by `chat_messages.outcome`. Known keys: `answered`,
  `ungrounded`, `blocked`, `conversational`, `llm_failed`; all known keys are present (zeros included). A
  `null` outcome counts as `answered` (as `GET /admin/usage/queries/{id}` already does).
- `avg_turns` is `null` when there are no conversations.

### `GET /admin/conversations` (existing) – additive filters and fields

| Param | Type | Meaning |
|---|---|---|
| `min_turns`, `max_turns` | int `>= 1` | Inclusive turn-count filter (histogram bin click). `max_turns` omitted = open-ended. `min_turns > max_turns` is `422`. |
| `outcome` | enum above | Conversations with at least one turn of this outcome ... |
| `day` | `YYYY-MM-DD` | ... on this day (outcome-bar segment click). `day` without `outcome` is `422`. |

New row fields: `turn_count` (int) and `outcome_counts` (`{outcome: count}`); the UI falls back to
`ceil(message_count / 2)` when `turn_count` is absent. Supported `sort` stays
`last_activity_desc | last_activity_asc`.

Page URL: `/admin/conversations?ip=&min_turns=&max_turns=&outcome=&day=&preview=<conversation id>`
(`preview` opens the side drawer and is UI-only).

### `GET /admin/conversations/{id}?turn=<n>` (existing) – additive

`turn` is an optional `turn_index` (int `>= 0`). The response keeps `conversation`, `messages` and
`message_count` unchanged and adds:

```json
{
  "focus_turn": 1,
  "turns": [
    {
      "turn_index": 1,
      "request_id": "req_ab12",
      "user_message_id": "uuid",
      "assistant_message_id": "uuid",
      "created_at": "2026-10-14T09:14:02Z",
      "outcome": "answered",
      "grounded": true,
      "stages_ms": {
        "guardrail_pre": 12.4,
        "embedding": 41.0,
        "retrieval": 80.2,
        "reranker": null,
        "llm": 1650.7,
        "guardrail_post": 6.1
      },
      "total_ms": 1790.4,
      "tokens": { "prompt": 2400, "cached_prompt": 1024, "completion": 380, "reasoning": 128, "embedding": 12, "guardrail": 8, "total": 2800 },
      "cost_usd": 0.0031,
      "sources": [
        { "document_id": "uuid", "chunk_id": "uuid", "chunk_index": 3, "source_name": "Leave policy.md", "section_heading": "Annual leave", "score": 0.82 }
      ],
      "feedback": [
        { "id": "fb_1", "rating": 2, "comment": "Out of date", "reason_tags": ["wrong"], "triage_status": "new", "created_at": "2026-10-14T09:15:00Z" }
      ],
      "labels": { "intent": "leave", "quality": "partial", "issue": "outdated_content" },
      "promoted_case_id": null
    }
  ]
}
```

- One `turns` item per `turn_index`, ascending. Timings come from `latency_records` (by `request_id`);
  `stages_ms` keys are exactly the six above, `null` when the stage did not run. `total_ms` is
  `latency_records.total_ms`.
- `tokens.prompt` **includes** `cached_prompt`; `tokens.completion` **includes** `reasoning` (as stored in
  `chat_messages`). `tokens` is `null` for turns without usage data.
- `labels` comes from the insights classifier (B6); `null` until classified, individual keys `null` when unknown.
- `focus_turn` echoes `turn` when that turn exists, else `null` (never an error; `turn` < 0 or non-integer is `422`).
- `promoted_case_id` is set once the turn was promoted (`POST /admin/feedback/{assistant_message_id}/promote-to-test`).

## B7.3 Documents

### `GET /documents/stats/citations`

Query: `from`, `to`, `limit` (top-cited size, `1..50`, default 10). A **citation** = one assistant turn that
listed the document among its sources (counted once per turn per document), from the
`document_citation_daily` rollup.

```json
{
  "from": "2026-09-16",
  "to": "2026-10-15",
  "top_cited": [
    { "document_id": "uuid", "source_name": "Leave policy.md", "citations": 120, "last_cited_at": "2026-10-15T08:00:00Z" }
  ],
  "age_vs_citations": [
    { "document_id": "uuid", "source_name": "Leave policy.md", "age_days": 200, "citations": 3 }
  ],
  "coverage": { "answered_queries": 1000, "cited_queries": 870, "coverage_pct": 87.0 }
}
```

- `top_cited` sorted by `citations` desc, then `source_name`. `age_vs_citations` lists **every** document
  (including zero citations), `age_days` = whole days since `updated_at` (or `created_at`).
- `coverage_pct` = `100 * cited_queries / answered_queries`, rounded to 1 decimal; `null` when
  `answered_queries` is 0.

### `GET /documents/{id}/chunks` (existing) – additive params and fields

| Param | Type | Meaning |
|---|---|---|
| `q` | string `<= 200` | Case-insensitive match on chunk `content` or `section_heading` (LIKE-escaped). |
| `sort` | `index` (default) \| `hits` | `hits` = hits desc, then `chunk_index` asc. Unknown is `422`. |

The response stays a JSON **array**; each item adds:

```json
{
  "id": "uuid", "chunk_index": 3, "content": "...", "strategy": "structure_aware",
  "section_heading": "Annual leave", "char_count": 812,
  "hits": 42,
  "low_rated_hits": 3,
  "last_hit_at": "2026-10-15T08:00:00Z",
  "weekly_hits": [{ "week_start": "2026-10-13", "hits": 9 }]
}
```

- `hits` = citations of this chunk in the last 8 ISO weeks; `low_rated_hits` = of those, turns rated `<= 2`.
- `weekly_hits` covers the last 8 ISO weeks (`week_start` = Monday); zero weeks may be omitted.
- The UI also filters and sorts locally, so an older backend that ignores `q` / `sort` still behaves; the
  chunk search is debounced 300 ms. Page URL: `/documents/{id}?q=&sort=hits&chunk=<chunk id>`.

### `GET /documents/{id}/low-rated-citations`

Query: `limit` (`1..100`, default 20). Unknown document is `404`.

```json
{
  "document_id": "uuid",
  "total": 7,
  "items": [
    {
      "message_id": "assistant-message-uuid",
      "session_id": "uuid",
      "turn_index": 1,
      "request_id": "req_ab12",
      "chunk_id": "uuid",
      "chunk_index": 3,
      "query": "How many leave days do I get?",
      "rating": 2,
      "reason_tags": ["wrong"],
      "comment": "Out of date",
      "created_at": "2026-10-14T09:15:00Z"
    }
  ]
}
```

- Rows = feedback with `rating <= 2` on turns that cited a chunk of this document, newest first; one row per
  (message, chunk). `message_id` deep-links to `/admin/feedback?message_id=`.

## Deep links that read these params (newplan §1)

| Element | href | Target parses into |
|---|---|---|
| Visitors heatmap cell | `/admin/users?weekday=0&hour=9` | Visitors list filter chip + `GET /admin/users?weekday=0&hour=9` |
| Visitor row "View sessions" | `/admin/usage?tab=sessions&ip=<ip>` | Usage page Sessions tab, IP filter |
| Visitor row / IP | `/admin/users/client-<ip>` | Visitor detail |
| Visitor "Conversations" | `/admin/conversations?ip=<ip>` | Conversations list IP filter |
| Conversations histogram bin | `/admin/conversations?min_turns=2&max_turns=3` | List filter + request params |
| Outcome bar segment | `/admin/conversations?outcome=blocked&day=2026-10-09` | List filter + request params |
| Conversation row | `/admin/conversations?preview=<id>` | Preview drawer (table state kept) |
| Turn link | `/admin/conversations/<id>?turn=<n>` | Scroll to + highlight turn `n` |
| Trace link | `/admin/governance?tab=Traces&request_id=<id>` | Governance traces |
| Feedback link | `/admin/feedback?message_id=<assistant message id>` | Feedback queue |
| Insight label | `/admin/analytics?intent=<x>` / `?quality=<x>` | Analytics filters |
| Top-cited bar / scatter dot | `/documents/<id>` | Document detail |
| Source / heatmap cell / low-rated item | `/documents/<id>?chunk=<chunk id>` | Scroll to + highlight chunk |
