"""Flatten ``app.routes`` across FastAPI's lazy route inclusion.

Since FastAPI 0.159 (``include_router`` becomes lazy for startup
performance), ``app.routes``/``router.routes`` no longer contains a flat
list of ``APIRoute``/``Route`` objects: each ``include_router`` call appends
an internal ``_IncludedRouter`` wrapper instead, resolved on demand. A route
nested two (or more) routers deep -- e.g. ``app`` includes ``admin.router``,
which itself includes ``admin_usage.router`` -- also only gets the combined
path/dependencies/tags from that chain lazily; the raw route object's own
``.path``/``.dependant`` only reflect what was declared locally on its own
router, not what the full inclusion chain contributes.

``fastapi.routing.iter_route_contexts`` (the same helper FastAPI's own
OpenAPI generator uses to build the schema) resolves this: it yields one
``RouteContext`` per route with the fully composed ``path``, ``methods``,
``name``, ``endpoint`` and ``dependant``, while ``.original_route`` is the
real object FastAPI dispatches to.
"""

from __future__ import annotations

from collections.abc import Sequence

from fastapi import FastAPI
from fastapi.routing import APIRoute, RouteContext, iter_route_contexts


def flat_routes(app: FastAPI) -> list[RouteContext]:
    """Every route, flattened through FastAPI's lazy ``include_router``.

    Each item exposes the fully composed ``path``/``methods``/``name``/
    ``endpoint``/``dependant`` (including anything contributed by an
    intermediate ``include_router`` call), in the same relative order
    FastAPI would dispatch them. Use ``is_api_route`` in place of
    ``isinstance(route, APIRoute)`` -- the item is a context wrapper, not
    the route itself.
    """
    return list(iter_route_contexts(app.routes))


def is_api_route(route: RouteContext) -> bool:
    """Whether ``route`` (from ``flat_routes``) wraps a real ``APIRoute``."""
    return isinstance(route.original_route, APIRoute)


def routes_with_path_containing(
    routes: Sequence[RouteContext], needle: str
) -> list[RouteContext]:
    """Routes (already flattened) whose composed ``path`` contains ``needle``."""
    return [r for r in routes if needle in (r.path or "")]
