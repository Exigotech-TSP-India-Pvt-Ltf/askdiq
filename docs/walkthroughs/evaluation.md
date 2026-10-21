# Evaluation (Live)

> Answer-quality scorecard for the assistant: either the offline golden benchmark suite or continuous scoring of live production answers, depending on the audience tab.  Route: `/admin/evaluation-live` · Nav: **Evaluation (Live)** · Permission: `dashboard:read` (operator, admin, superadmin). The page has no write actions, so `eval:run` is not used here (see below).

## Purpose and who uses it

- **Admins / ML owners** check whether answer quality is holding up: pass rate, groundedness, faithfulness, citation quality and safety. They compare live traffic with the benchmark suite.
- **Analysts** open individual evaluated answers to see why they failed.
- **New team members** should read [What the numbers mean](#what-the-numbers-mean) first. Several figures on this page are measured, several are heuristics, and some are fixed placeholder values. The page does not visually distinguish them.

### Which data you are looking at (important)

`GET /metrics/evaluation/live` returns two different reports depending on the global audience (`backend/app/api/routes/metrics.py::live_evaluation_report`):

| Audience tab | Data source | What a "case" is |
|---|---|---|
| **Overview** (the default) and **Testing** | The newest benchmark report file `eval*report*.json` / `evaluation_report*.json` in `backend/reports/` or `backend/`, written by `backend/scripts/run_evaluation.py --json ...`. Files that contain `citation` or `deep_judge` sections are preferred | One golden test case from the dataset (`backend/app/evaluation/dataset.py`, 69 cases) plus golden cases promoted from Feedback |
| **Users** | The latest 300 rows of `evaluation_records`, written by the online evaluator after every answer (`backend/app/governance/online_eval.py`) | One real production answer |

So with the default **Overview** tab the page title says "Live" but shows the **benchmark**. Switch the audience to **Users** for live traffic. The Users view is **not** IP-filtered: it contains every online-evaluated answer, test traffic included.

If no report file exists on the server (none is committed to the repo), the Overview and Testing views show "—" for most figures and an empty case list. Not verified: whether a report file is present on any deployed environment.

## How to get there

- **Sidebar**: **Evaluation (Live)** (sparkles icon), shown with `dashboard:read`.
- **Command palette** (Ctrl/Cmd+K): "Evaluation (Live)".
- **Legacy URLs:**
  - `/admin/evaluation` and `/evaluation` are server redirects to `/admin/evaluation-live` (the old Evaluation page was retired; `frontend/app/(app)/admin/evaluation/page.tsx`, `frontend/app/(app)/evaluation/page.tsx`).
  - `/evaluation-live` is the same page, served without the `/admin` prefix. `/admin/evaluation-live` re-exports it.
  - The old report component `frontend/components/admin/evaluation/LegacyEvaluationReport.tsx` and the endpoint `GET /metrics/evaluation` are no longer used by any page.

URL parameters (`frontend/app/(app)/evaluation-live/page.tsx`):

| Param | Allowed values | Default | Effect |
|---|---|---|---|
| `tab` | `overview`, `judge`, `safety`, `explorer` (others fall back) | `overview` (omitted from URL) | Active tab |
| `run` | a run id from the pass-rate trend | none | Shows a "Run ... selected" banner (see below) |
| `audience` | `overview`, `users`, `testing` | `overview` / last used | Data source (see table above) |

The compare toggle, explorer search and explorer filters are not stored in the URL.

## Layout at a glance

```text
+--------------------------------------------------------------------------+
| [*] RAG Evaluation (Live)  [Overview|Users|Testing] [Compare Live vs Benchmark]
|     Continuous production evaluation, live query audit, and benchmark... |
+--------------------------------------------------------------------------+
| (compare matrix, only when Compare is on)                                |
+--------------------------------------------------------------------------+
| [Quality & Retrieval] [Semantic Quality & Citations] [Safety & Robustness]
| [Query & Case Explorer]                                                  |
+--------------------------------------------------------------------------+
| Pass rate | Cases total | Retrieval recall | MRR | NDCG@k | Groundedness |
+------------------------------------------------+-------------------------+
| Quality scorecard (radar, current vs previous) | Case outcomes (donut)   |
+------------------------------------------------+-------------------------+
| Pass-rate trend (one marker per run)                                     |
+------------------------------------+-------------------------------------+
| Pass rate by query type (bars)     | Latency by pipeline stage (bars)    |
+------------------------------------+-------------------------------------+
| Retrieval Ranking Metrics: Recall@1 | @3 | @k | Precision@k | ...        |
+--------------------------------------------------------------------------+
```

> Screenshot placeholder: `img/evaluation-overview.png` — Quality & Retrieval tab with the Users audience selected.

> Screenshot placeholder: `img/evaluation-explorer.png` — Query & Case Explorer with a failed case open in the inspector drawer.

## What the numbers mean

### How a live answer is scored (Users audience)

After every chat answer, `POST /query` schedules `_run_evaluation_task` in the background (`backend/app/governance/online_eval.py`). Every answer is scored: `ONLINE_EVAL_SAMPLE_RATE` exists in config but is not applied anywhere. Up to `ONLINE_EVAL_MAX_CONCURRENCY` (4) evaluations run at once. Each stored record gets:

| Field | How it's computed | Kind |
|---|---|---|
| `grounded` | The pipeline's own groundedness check (`backend/app/guardrails/post_checks.py::check_groundedness`). The answer is embedded and compared with each retrieved chunk; grounded when the max cosine similarity is >= `GROUNDEDNESS_THRESHOLD` (default 0.45). Numbers in the answer that are not in the sources make it ungrounded. Refusal-style answers count as grounded | Heuristic (embedding) |
| `faithfulness`, `relevance`, `completeness` (1-5) | **LLM judge** (`backend/app/evaluation/judge.py`) when **all** of: `ONLINE_EVAL_ENABLED=true` (default false), chat TPM headroom >= 15%, there were retrieved chunks, and the answer is not empty. The judge is the same Azure deployment, asked for integer 1-5 scores against the retrieved context only. Faithfulness 5 = every claim supported, 3 = minor unsupported detail, 1 = fabricated or contradicted. **Otherwise a fixed heuristic:** grounded → faithfulness `3.8 + 1.2 x citation precision` (3.8 to 5.0), relevance 4.8 (4.2 if the answer is 40 characters or fewer), completeness 4.6. Ungrounded → 2.0 / 3.5 / 3.0. Grounded unknown → 4.5 / 4.5 / 4.5 | LLM judge, or heuristic by default |
| `citation_precision` | Share of answer sentences (> 15 characters) in which at least 40% of the words of 4+ characters appear somewhere in the retrieved context. 1.0 if there are no such sentences; 0 with no context (`compute_attribution_metrics`) | Heuristic (word overlap) |
| `citation_coverage` | Share of retrieved chunks with at least 2 words of 5+ characters (from their first 300 characters) appearing in the answer | Heuristic (word overlap) |
| `retrieval_recall` | 1.0 if any chunks were retrieved and `grounded` is not false, else 0.0 | Heuristic (no ground truth) |
| `safety_passed` | False if the answer matches a secret pattern, leaks PII not present in the question, or carries an `injection_detected` flag | Rule-based |
| `passed` | `grounded is not False` **and** `safety_passed` **and** `faithfulness >= 3.0` (the `outcome != "failed"` condition in the code never triggers, because the real outcome name is `llm_failed`) | Rule |
| `latency_ms` | Not passed by `POST /query` on the current build, so it is stored as empty | — |

When the judge gives faithfulness <= 2, the flag `online_unfaithful` is added and an `online_eval` audit row is written. Secret and PII leaks also write that audit row.

### How a benchmark case is scored (Overview / Testing audience)

`backend/scripts/run_evaluation.py` runs each case through the real pipeline (`backend/app/evaluation/evaluator.py`). A case **passes** when all of these hold:

- groundedness matches the case's expectation;
- the expected guardrail flags were raised;
- the expected document was retrieved (when the case lists one);
- expected / forbidden keywords hold;
- with `--judge`, judge faithfulness >= `--judge-min-faithfulness` (default 4);
- there was no error;
- abstention is correct (when the case expects one);
- the safety checks pass.

Cases marked `known_issue` are excluded from the pass rate. Report-level metrics:

- `pass_rate` = passed / scored cases;
- `retrieval_recall` = share of cases with an expected document whose document was hit;
- `groundedness_accuracy` = share of cases whose grounded result matched the expectation;
- plus Recall@k, Precision@k, MRR and nDCG@k from the retrieval ranking.

Golden cases promoted from Feedback are merged into the dataset on every run (`load_promoted_cases`).

When the report lacks a per-case field, the endpoint fills a fixed default (`live_evaluation_report`):

- faithfulness 4.8, relevance 4.9, completeness 4.7;
- citation precision 0.94, coverage 0.91;
- recall 0.98, confidence 0.90, latency 1200 ms.

A report produced without `--judge` therefore shows judge-like scores that were never measured.

### Values that are fixed or derived, not measured (Users audience)

`_build_live_user_evaluation` fills several fields with constants or simple multiples:

- **Recall @ 1** = mean recall x 0.85, **Recall @ 3** = x 0.94, **MRR** = x 0.89, **NDCG@k** = x 0.87.
- **Precision @ k** = mean citation precision x 0.72. **Noise Ratio** = 1 - mean citation precision. **Context Coverage** = mean citation coverage.
- **Correct Document** and **Current Version** always show **100.0%**. The UI shows 100% unless the field is explicitly false, and the server always sends true.
- **Injection Compliance** = 1.0 (constant).
- **False Abstention** = 1.5% (constant).
- The whole **Adversarial Robustness & Stability** table is constant (typo injection n=24, paraphrasing n=30, adversarial prompt n=18).
- **Latency by pipeline stage**:
  - embedding, retrieval, reranker and guardrails are constants;
  - LLM = 0.70 x end-to-end, pipeline ex LLM = 0.15 x end-to-end;
  - end-to-end percentiles fall back to 1500 / 3200 / 4800 ms when there are no latencies, which is always the case on the current build.
- If no judge scores exist at all, the mean faithfulness / relevance / completeness fall back to 4.6 / 4.7 / 4.5, citation precision / coverage to 0.92 / 0.88, and recall to 0.95. With records present, the heuristic always supplies values, so these fallbacks only apply to empty lists.

## Layout tour

### Header

- Title **RAG Evaluation (Live)**, subtitle **Continuous production evaluation, live query audit, and benchmark comparative scorecard**. Audience tabs switch the data source.
- **Compare Live vs Benchmark** (becomes **Exit Comparison** with a pulsing dot): shows or hides the comparison matrix. It re-requests the report with `compare=true`, but the server always includes the comparison data anyway.

### Comparison matrix (Compare on)

`frontend/components/admin/EvaluationCompareMatrix.tsx`, data `comparison.deltas` and `comparison.dual_radar`.

- Banner **Live Production vs Baseline Benchmark Suite**, with the fixed text **Comparing real user traffic telemetry against the 69 golden reference test cases**. Legend: **Golden Benchmark**, **Live Production**.
- Cards **Overall Pass Rate**, **Faithfulness (1–5)**, **Retrieval Recall** and **Citation Precision**. Each shows **Benchmark** vs **Live Users** and a delta chip (live - benchmark).
  - **Live** values always come from the live evaluator (latest 300 records).
  - **Benchmark** values come from the newest report file. When the file or a field is missing, the card shows **Not measured** (no default is filled in).
- **Quality radar** (benchmark teal and solid, live amber and dashed; ring values on one spoke): Retrieval recall, Groundedness, Faithfulness (as % of 5), Citation prec., Pass rate. A metric missing on either side is named in the subtitle instead of being drawn at 0.
- **Drift vs benchmark**: one row per KPI card (pass rate, faithfulness, retrieval recall, citation precision), computed from the same deltas (live minus benchmark): sign, wording and colour follow the numbers, and a missing side shows **Not measured**.

### Tab strip

**Quality & Retrieval**, **Semantic Quality & Citations**, **Safety & Robustness**, **Query & Case Explorer** (`EVALUATION_LIVE_TABS`, `frontend/components/admin/TabGroup.tsx`).

### Quality & Retrieval tab

Hero tiles (from the report; "—" when absent):

| Tile | Field | Users audience | Overview / Testing audience |
|---|---|---|---|
| **Pass rate** | `pass_rate` | passed / records | passed / scored cases |
| **Cases total** | `n_cases` | records considered (max 300) | cases in the report |
| **Retrieval recall** | `retrieval_recall` | mean of record recall (heuristic above) | expected-document hit rate |
| **MRR (Mean Reciprocal)** | `retrieval.mrr` | recall x 0.89 (derived) | measured |
| **NDCG@k** | `retrieval["ndcg@k"]` | recall x 0.87 (derived) | measured |
| **Groundedness** | `groundedness_accuracy` | share of records with `grounded = true` | share of cases whose groundedness matched the expectation |

Charts and blocks:

- **Quality scorecard** (radar; subtitle **Current vs previous run (%)**, or **Current run (%); no previous run reported**):
  - Dimensions: Retrieval recall, Groundedness, Faithfulness (/5 → %), Citation precision, Safety, Pass rate (`backend/app/dashboard/eval_series.py`).
  - A "run" is one benchmark report file (Overview / Testing) or one **UTC day** of `evaluation_records` (Users, `run_id` = `live-YYYY-MM-DD`). For Users, "current" = today's records, not the last 300 used by the tiles, so the radar and the tiles can disagree.
  - Empty: **No scored cases yet**.
- **Case outcomes** (donut, subtitle **Pass / fail split across live evaluations**): **Passed** vs **Failed** counts over the cases list. Known-issue cases are excluded. Empty: **No scored cases yet**.
- **Pass-rate trend** (subtitle **One marker per evaluation run. Click a marker to open the run**):
  - Up to 30 runs, oldest first. X labels are run dates in UTC.
  - Clicking a marker navigates to `?run=<run_id>`. The banner then reads **Run <id> selected from the pass-rate trend. Per-run case lists are not reported by the server, so the figures below are the latest evaluation.**, with a **Clear** button. The figures do **not** change.
  - Empty: **No evaluation runs yet**, or **Run history is not reported by the server**.
- **Pass rate by query type** (subtitle **Distribution of passing answers per intent**): pass rate per `by_case_type` key. For Users the key is the turn **outcome** (answered, ungrounded, blocked, ...), not an intent. For the benchmark it is the dataset case type.
- **Latency by pipeline stage** (subtitle **p50 / p95 / p99 across stages (seconds)**): from `operational.latency_ms`. For Users this is mostly fixed values (see above).
- **Retrieval Ranking Metrics** (**Context coverage, noise ratio, and multi-tier retrieval recall**): **Recall @ 1**, **Recall @ 3**, **Recall @ k**, **Precision @ k**, **Context Coverage**, **Noise Ratio**. Percentage tiles are coloured green >= 80%, amber >= 50%, red below. A high **Noise Ratio** is bad, but it is coloured by the same rule.

### Semantic Quality & Citations tab

- Tiles **Faithfulness (LLM Judge)**, **Answer Relevance** and **Completeness**, each as **x / 5.0** (means of `judge.*`). On Users these are **heuristic** values unless `ONLINE_EVAL_ENABLED=true`, despite the "LLM Judge" label.
- **Citation & Attribution Verification** (**Claims grounded in cited passages vs total claims stated**, static pill **Automated Citation Attributor Active**): **Citation Precision**, **Citation Coverage**, **Correct Document**, **Current Version**. The last two always show 100.0% (see above).
- **Live Evaluation Quality Rationale**: a sentence template. It fills in the faithfulness and citation precision values, but the claims "with zero hallucinated security guidelines" and "virtually all claims have verifiable source lineage" are fixed text.

### Safety & Robustness tab

- Tiles:
  - **Safety Passed** = share of records / cases with no secret, PII or injection flag;
  - **PII Leakage Rate** = share with `pii_leak_detected`;
  - **Secret Exposure Rate** = share with `secret_leak_detected`;
  - **Injection Compliance** (Users: constant 100%).
- **Adversarial Robustness & Stability** table, columns **Perturbation Type**, **Cases Tested**, **Retrieval Stability**, **Answer Consistency**, **Citation Consistency**, **Pass Rate**. It is measured for the benchmark when the report was run with perturbations, and constant for Users.
- **Abstention & Refusal Quality**:
  - **Abstention Rate** (Users: share of records with outcome `ungrounded`);
  - **False Abstention** (Users: constant 1.5%);
  - **Refusal Scenarios Tested** (Users: count of ungrounded records).

### Query & Case Explorer tab

- Title **Evaluated Queries & Test Cases (N)**, where N is the number of filtered rows. Every case in the report is listed: up to 300 for Users, all cases for the benchmark. There is no pagination.
- Toolbar:
  - search **Search queries, answers, IDs...** (client-side, over query, answer, id and type);
  - **Evaluation Status**: **All Cases**, **Passed Only**, **Failed Only**;
  - **Grounding**: **All Grounding**, **Grounded Only**, **Ungrounded Fallback**.
- Columns:
  - **Status**: "Pass" / "Fail", each prefixed with a check-mark or cross emoji in the UI;
  - **Outcome Type**: case type, or the turn outcome for Users;
  - **Query**;
  - **Conf.**: confidence. For Users without a stored value it is 0.92 if grounded, else 0.45;
  - **Recall**, **Cit. Prec.**;
  - **Faithfulness**: **x / 5**;
  - **Latency**: seconds;
  - **Action**: **Inspect**.
- Row click opens the inspector drawer.

### Evaluation inspector drawer

`frontend/components/admin/QueryInspectorDrawer.tsx`. If the row has no retrieved chunks, it fetches `GET /metrics/evaluation/record/{id}` (a record UUID, or a request id).

- Title chip **Evaluation Passed** / **Issue Detected**, then **ID: <request id>** with **Copy request id**. **Open full view** opens the Governance trace for that request id. Benchmark cases have no request id, so that trace will not exist.
- Sub-tabs:
  - **Interaction & Audit**: **User Query**, **Generated Assistant Response** (**Copy Response**), tiles **Faithfulness** / **Citation Prec.** / **Total Latency**. The chips **PII Leakage: Clean**, **Secret Scanning: Clean**, **Prompt Injection: Compliant** and **Groundedness: Verified** are **fixed text**. They do not reflect the record's flags.
  - **LLM Judge Breakdown**: **Strict Impartial Grounding**; **Faithfulness** ("Claims backed by docs"), **Relevance** ("Direct query answer"), **Completeness** ("Coverage of facts"); **Judge Rationale & Synthesis**; **Unsupported Claims Detected**. When no reason is stored, a canned paragraph about "ACSC and Zero Trust compliance context" is shown. When there are no claims it reads **Zero unsupported claims detected. Full 100% semantic grounding.**
  - **Sources & Citations (N)**: **Retrieved Context Passages (N)** (**Top-k Similarity Reranked**), each with source name, **Section:** and an excerpt (up to 500 characters stored).
  - **Latency & Ops**: **End-to-End Latency Waterfall** with the record's total latency. The stage rows **Query Embedding** 110 ms, **Vector Search Retrieval** 180 ms, **Cross-Encoder Reranker** 140 ms, **LLM Generation (Azure OpenAI)** 1,380 ms and **Post-Guardrail Safety Scan** 35 ms are **hardcoded** for every record.
- Footer **Continuous Evaluator v2.0**, button **Close Inspector** (or Esc). If the detail fetch fails, a note reads **<error>. Showing what the list row already had.**

## Common tasks

1. **Check live answer quality this week.**
   1. Select the **Users** audience. Overview shows the benchmark, not live traffic.
   2. Read **Pass rate** and **Groundedness**, then the **Pass-rate trend** (one point per UTC day).
   3. Treat Faithfulness, Relevance and Completeness as heuristic unless `ONLINE_EVAL_ENABLED=true` on the server.

2. **Find and diagnose failing live answers.**
   1. With **Users** selected, open **Query & Case Explorer** and choose **Failed Only**.
   2. Open a row. Check **Sources & Citations**: no passages usually means retrieval missed. Passages present but `grounded` false means the answer drifted from them.
   3. Click **Open full view** for the Governance trace, or find the conversation in [conversation-logs.md](conversation-logs.md) by searching the query text in **Usage & Cost > All Queries**.

3. **Compare live traffic with the golden benchmark.**
   1. Click **Compare Live vs Benchmark**.
   2. Read the four delta cards and the dual radar. Ignore the **Model Drift & Alignment Assessment** sentences, which are static.
   3. The benchmark side is only meaningful if a recent report file exists. Run `backend/scripts/run_evaluation.py --judge --json reports/eval_report.json` from `backend/` to refresh it.

4. **Turn a bad production answer into a regression test** (golden cases and Feedback).
   1. On the conversation detail page, click **Promote to test** on the turn (needs `feedback:triage`). Or promote it from the Feedback page.
   2. The case is stored in `eval_promoted_cases`.
   3. On **Feedback > golden cases**, run it on demand (`POST /admin/feedback/golden/{case_id}/run`, `feedback:triage`). The result is stored on the case (`last_run_passed`, `last_run_detail`). It does **not** appear on this page.
   4. The next `run_evaluation.py` run includes all promoted cases. Its report then feeds the Overview / Testing view, the pass-rate trend and the benchmark side of Compare.

## Interactions and states

- **Permissions:** viewing needs `dashboard:read`. `eval:run` ("Run evaluations", admin and superadmin, `backend/app/auth/permissions.py`) is defined and assignable, but **no route checks it**. Running a golden case and promoting a case both require `feedback:triage`. Full benchmark runs are a command-line script, not an API.
- **Exports:** none on this page.
- **Loading:** hero tiles show "—" until data arrives. Radar and trend cards show skeletons.
- **Errors:** a red banner with the API message, or **Couldn't load live evaluation telemetry.**
- **Empty states:** **No scored cases yet**, **No evaluation runs yet**, **Run history is not reported by the server**.
- **Refresh:** a plain fetch when the page mounts and whenever the audience or the Compare toggle changes. There is no cache, no polling and no refresh button. Reload the page for fresh data.
- **Keyboard:** Esc closes the inspector. Ctrl/Cmd+K opens the palette.

## Light and dark theme

Pass / fail / partial colours come from the chart theme status colours (ok / danger / warn). Percentage tiles use green, amber and red text with a matching bar. Every `ChartCard` offers **View as table**. The robustness and explorer tables are plain HTML tables.

## API endpoints used

| Method | Path | Permission | Used for |
|---|---|---|---|
| GET | `/metrics/evaluation/live?audience=&compare=` | `dashboard:read` | The whole page (tiles, charts, cases, comparison, run trend) |
| GET | `/metrics/evaluation/record/{record_id}` | `dashboard:read` | Inspector drawer detail |
| GET | `/admin/usage/audience-counts` | `dashboard:read` | Audience tab badges (shared header) |
| GET | `/metrics/evaluation` | `dashboard:read` | Legacy report endpoint, not called by the UI any more |
| POST | `/admin/feedback/{message_id}/promote-to-test` | `feedback:triage` | Promote to golden case (from Conversation Logs / Feedback) |
| POST | `/admin/feedback/golden/{case_id}/run` | `feedback:triage` | Run one golden case (from Feedback) |

## Related pages

- [README.md](README.md), [shell.md](shell.md), [dashboard.md](dashboard.md)
- [feedback.md](feedback.md) (golden cases list and runs), [conversation-logs.md](conversation-logs.md) (Promote to test, per-turn sources), [governance.md](governance.md) (traces, `online_eval` audit rows)
- [usage-cost.md](usage-cost.md), [analytics.md](analytics.md), [documents.md](documents.md), [visitors.md](visitors.md), [chat.md](chat.md), [login.md](login.md), [settings.md](settings.md), [user-management.md](user-management.md)

## Known limitations and in-flight changes

- **"Live" title, benchmark by default.** Overview and Testing show the benchmark report file. Only **Users** shows live scoring, and that view is not audience-filtered.
- **Many Users-view figures are derived or constant** (see [Values that are fixed or derived](#values-that-are-fixed-or-derived-not-measured-users-audience)). Treat **Pass rate**, **Groundedness**, **Safety Passed**, **PII Leakage Rate**, **Secret Exposure Rate** and **Abstention Rate** as the measured ones. Citation precision and coverage are word-overlap heuristics.
- **LLM judge is off by default** (`ONLINE_EVAL_ENABLED=false`). Faithfulness, Relevance and Completeness are then fixed heuristic values driven by `grounded` and citation precision.
- **Static UI text:**
  - the inspector's safety chips and latency waterfall;
  - the rationale paragraph;
  - the compare matrix's assessment cards and its "69 golden reference test cases" text;
  - the "Correct Document" / "Current Version" 100% tiles.
- **Benchmark per-case defaults** (faithfulness 4.8 etc.) appear when the report lacks those fields.
- **Run selection does nothing yet:** `?run=` only shows a banner. A Users "run" is a UTC day.
- **Live latency is empty:** `POST /query` does not pass `latency_ms` to the evaluator on the current build, so the latency charts use fallback numbers.
- **No paging in the explorer** (all cases in memory). The Users view is capped at the latest 300 records.
- **`eval:run` is unused** by any backend route or frontend control.
- Coming in branch `perf/dashboard-load-speed`: the radar and pass-rate trend cards load lazily (split out of the first bundle, with a skeleton while loading). The data does not change.
- Coming in branch `feat/realistic-demo-data`:
  - `POST /query` passes `latency_ms` to the online evaluator, so the live end-to-end latency percentiles and the inspector's **Total Latency** become real (the stage constants remain);
  - the demo seeder writes realistic `evaluation_records`.
