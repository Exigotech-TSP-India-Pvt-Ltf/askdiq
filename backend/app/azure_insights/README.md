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
    async with ctx.arm_client(budgeted=True) as api:  # counts against the monthly cap
        body = await api.get_json(
            f"{account}/providers/Microsoft.Insights/metrics", params
        )
        # or: async for item in api.paged(url, params): ...
    async with ctx.session_factory() as session:
        n = await upsert_points(session, normalise(body))  # ON CONFLICT DO UPDATE
        await session.commit()
    return SourceResult(
        rows=n, detail={"window": "PT2H"}
    )  # detail: small JSON on the status row


register_source(
    AzureSource(
        name="metrics",  # job type azure.metrics, status row "metrics"
        label="Monitor metrics",
        interval=lambda s: s.azure_metrics_interval_seconds,
        run=run_metrics,
        snapshot_kinds=(METRICS_KIND,),  # recomputed + announced after each run
        call_cap=lambda s: s.azure_metrics_monthly_call_cap,
        enabled=lambda s: (
            True
        ),  # optional extra flag (e.g. AZURE_SERVICE_HEALTH_ENABLED)
        needs_credentials=True,  # False for public APIs (retail)
        order=20,  # position on the status card
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
| `AzureApiError` 4xx, `AzureAuthError`, `AzureNotConfiguredError` | status `error` (sanitised code/message), `consecutive_failures + 1`; next slot |
| `AzureApiError` 5xx / connection, unexpected exception | recorded, then re-raised: the queue retries with backoff |

`AzureClient` already retries 5xx / connection errors (exponential backoff
with jitter), sleeps short `Retry-After` waits, refreshes the token once on a
401, and follows `nextLink` / `NextPageLink` with a page cap. Never log
headers, tokens or bodies; raise the package errors (they sanitise).

## Scheduling

`ensure_azure_scheduled()` runs at startup (`PlatformServices.start`) and
every 5 minutes from the jobs worker. It queues each active source for its
next epoch-aligned slot (`dedupe_key = azure.<name>:<slot>`); a source that
has not run within its interval runs at once. Each run re-arms the next slot.
`AZURE_INSIGHTS_ENABLED=false`: nothing is queued and queued jobs no-op;
the status card shows "Not connected". On-demand runs of public sources
(retail) still work.

## Tables

- `azure_sync_status` (core): per source state, last run / success / error,
  failures, next run, monthly call counter (`status.consume_call`), `detail`.
- `azure_retail_prices` (Phase 1).
- `azure_metric_points` (Phase 2): Monitor metrics per deployment, metric,
  status code, grain (PT1M kept 7 d, PT1H 93 d) and bucket; source `metrics`
  (`metrics.py`), reconciliation + series snapshots `azure.metrics.*`
  (`reconciliation.py`).
