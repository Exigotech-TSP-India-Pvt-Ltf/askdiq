# Azure Insights: how a source plugs in

Free Microsoft Foundry / Azure data for the admin console (plan: `plan.md`).
Only free APIs: no Log Analytics, App Insights, alert rules, exports or
`metrics:getBatch`.

## The contract (every source, enforced by `scheduler.run_source`)

**poll on schedule -> persist with history -> refresh snapshots -> announce live**

1. **Poll on schedule.** Jobs poll Azure continuously at the configured
   interval (metrics 15 min, min 5; health 10 min; service health 30 min;
   capacity 6 h; costs daily; retail weekly). Never only on demand.
2. **Persist with history.** `run(ctx)` upserts into its own table with
   `INSERT ... ON CONFLICT (natural key) DO UPDATE`. Re-running a slot is
   harmless. History accumulates; nothing is overwritten except the same
   natural key. Prune only by the retention in the plan (PT1M 7 d, PT1H 93 d,
   costs 400 d).
3. **Refresh snapshots.** After every run (success or failure), the scheduler
   recomputes `azure.status` plus every kind listed in `snapshot_kinds`, for
   each registered default parameter set (`compute_and_store`).
4. **Announce live.** `compute_and_store` publishes `TOPIC_SNAPSHOT_UPDATED`
   (other replicas reload their memory copy) and `console.changed` with the
   kind (open consoles refetch through `/admin/live`). Extra scopes go in
   `live_scopes`.

Pages read only snapshots (`snapshot_response`: ETag/304, `X-Data-As-Of`),
built from Postgres. A page view never calls Azure.

## Adding a source

```python
# app/azure_insights/metrics.py
from app.azure_insights.scheduler import (
    AzureSource,
    SourceContext,
    SourceResult,
    register_source,
)

METRICS_KIND = "azure.reconciliation"


async def run_metrics(ctx: SourceContext) -> SourceResult:
    account = ctx.settings.azure_account_resource_id
    # budgeted=True: every call counts against the monthly cap.
    async with ctx.arm_client(budgeted=True) as api:
        url = f"{account}/providers/Microsoft.Insights/metrics"
        body = await api.get_json(url, params)
        # or: async for item in api.paged(url, params): ...
    async with ctx.session_factory() as session:
        # INSERT ... ON CONFLICT (natural key) DO UPDATE
        n = await upsert_points(session, normalise(body))
        await session.commit()
    # detail: small JSON kept on the status row.
    return SourceResult(rows=n, detail={"window": "PT2H"})


register_source(
    AzureSource(
        # Job type azure.metrics, status row "metrics".
        name="metrics",
        label="Monitor metrics",
        interval=lambda s: s.azure_metrics_interval_seconds,
        run=run_metrics,
        # Recomputed + announced on /admin/live after every run.
        snapshot_kinds=(METRICS_KIND,),
        call_cap=lambda s: s.azure_metrics_monthly_call_cap,
        # Optional extra flag, e.g. lambda s: s.azure_service_health_enabled.
        enabled=lambda s: True,
        # False for public APIs (retail).
        needs_credentials=True,
        # Position on the status card.
        order=20,
    )
)
```

Then:

- import the module in `app/azure_insights/jobs.py` (registers the handler
  for the worker and the API);
- register the snapshot kind next to its builder:
  `app.dashboard.snapshots.register(METRICS_KIND, builder, [{"days": 7}])`
  (builder: `async (gather, params) -> JSON`, reads Postgres only);
- serve it from a route with `snapshot_response(request, KIND, params, runner.gather)`
  and `Depends(require_permission(Permission.DASHBOARD_READ))`; add the path to
  `tests/test_route_guards.py`;
- frontend: add the query to `lib/route-prefetch.ts` / idle preload and map the
  kind to its query keys in `lib/live-updates.ts` so the view refetches when
  the kind is announced;
- one Alembic migration per phase, `down_revision` = current head.

## Errors and throttling (handled by the scheduler)

| Raised by `run` | Effect |
|---|---|
| `AzureThrottledError(retry_after)` (429, QPU header, low remaining reads) | status `throttled`; the same job is deferred to `now + retry_after` (no attempt spent) |
| `AzureBudgetExhaustedError` (monthly cap) | status `budget_exhausted`; waits for the next slot |
| `AzureUnsupportedError` (API not offered for the subscription type, e.g. Cost Management on classic CSP) | status `unsupported`, no failure streak; waits for the next slot |
| `AzureApiError` 4xx, `AzureAuthError`, `AzureNotConfiguredError` | status `error` (sanitised code/message), `consecutive_failures + 1`; next slot |
| `AzureApiError` 5xx / connection, unexpected exception | recorded, then re-raised: the queue retries with backoff |

`AzureClient` already retries 5xx / connection errors (exponential backoff
with jitter), sleeps short `Retry-After` waits, refreshes the token once on a
401, and follows `nextLink` / `NextPageLink` with a page cap. Never log
headers, tokens or bodies; raise the package errors (they sanitise).

## Scheduling

`ensure_azure_scheduled()` runs at startup (`PlatformServices.start`) and
every 5 minutes from the jobs worker. It queues each active source for its
next slot (`scheduler.source_slot`; `dedupe_key = azure.<name>:<slot>`); a
source that has not run within its interval runs at once. Each run re-arms
the next slot. Slots are epoch-aligned unless the source sets `anchor`
(`(IANA zone, local time)`, DST-aware via `zoneinfo`; the `tzdata` package
supplies the zones): costs run daily at `AZURE_COSTS_RUN_AT` (03:00)
`INSIGHTS_TIMEZONE`.
`AZURE_INSIGHTS_ENABLED=false`: nothing is queued and queued jobs no-op;
the status card shows "Not connected". On-demand runs of public sources
(retail) still work.

## Sources

All imported once in `jobs.py`. Job type `azure.<name>`, status row `<name>`.

| Source | Phase | Module | Interval | Enabled | Snapshot kinds |
|---|---|---|---|---|---|
| `retail` | 1 | `retail.py` | `AZURE_RETAIL_INTERVAL_SECONDS` (weekly) | always (public API) | `azure.retail` |
| `metrics` | 2 | `metrics.py` (+ `reconciliation.py`) | `AZURE_METRICS_INTERVAL_SECONDS` (15 min) | always | `azure.metrics.summary`, `azure.metrics.series` |
| `costs` | 3 | `costs.py` (+ `billed.py`) | `AZURE_COSTS_INTERVAL_SECONDS`, anchored at `AZURE_COSTS_RUN_AT` | always | `azure.costs.billed` |
| `capacity` | 4 | `capacity.py` | `AZURE_CAPACITY_INTERVAL_SECONDS` (6 h) | always | `azure.capacity` |
| `health` | 4 | `health.py` | `AZURE_HEALTH_INTERVAL_SECONDS` (10 min) | always | `azure.health` |
| `service_health` | 4 | `health.py` | `AZURE_SERVICE_HEALTH_INTERVAL_SECONDS` (30 min) | `AZURE_SERVICE_HEALTH_ENABLED` | `azure.health` |
| `advisor` | 4 | `advisor.py` | `AZURE_ADVISOR_INTERVAL_SECONDS` (daily) | `AZURE_ADVISOR_ENABLED` | `azure.advisor` |

`azure.status` (core, `scheduler.STATUS_KIND`) is recomputed after every run.

Routes (`/admin/azure/*`, `DASHBOARD_READ`): `status`, `metrics/summary`,
`metrics/series`, `costs`, `capacity`, `health`, `advisor`; retail prices are
served from `/admin/models/prices/official` and `/admin/models/prices/sync`.

## Tables

- `azure_sync_status` (core): per source state, last run / success / error,
  failures, next run, monthly call counter (`status.consume_call`), `detail`.
- `azure_retail_prices` (Phase 1): one row per meter, currency and effective
  date (history kept); `role` / `model_key` / `deployment_type` from the
  explicit meter map (`prices.py`). Also re-prices the estimate for a
  non-USD bill (Phase 3, `billed.retail_card`).
- `azure_metric_points` (Phase 2): Azure Monitor points per deployment,
  metric, status code (requests only), grain (`PT1M` kept 7 d, `PT1H` 93 d)
  and bucket. The `azure_quota_near_limit` alert prefers its PT1M
  `TokenTransaction` / `AzureOpenAIRequests` peaks over `chat_messages`.
- `azure_billed_costs` (Phase 3): Cost Management cost per UTC usage day,
  meter and resource, billing currency, `is_final`; kept
  `AZURE_COSTS_RETENTION_DAYS` (400).
- `azure_deployments`, `azure_quota_usage` (Phase 4): deployment SKU /
  capacity / Azure TPM and RPM, and regional quota, one row per poll.
- `azure_health_status`, `azure_service_events`, `azure_advisor_recs`
  (Phase 4): Resource Health status changes, Service Health events by
  tracking id, Advisor recommendations (`resolved_at` once gone).

Migrations (one linear chain on main): `ca723897761c` (sync status) ->
`39e042e6bb64` (retail) -> `a788c15bf749` (metrics) -> `6e0a31f87ff3`
(capacity / health) -> `144fe4240293` (billed costs).
