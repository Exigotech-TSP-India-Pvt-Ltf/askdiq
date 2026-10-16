"""Async-only guard (plan B1.5.5 / B1.7): no blocking endpoints or helpers."""

from __future__ import annotations

import inspect

from fastapi.routing import APIRoute

from app.retrieval import embeddings
from main import app


def _endpoint_routes() -> list[APIRoute]:
    return [route for route in app.routes if isinstance(route, APIRoute)]


def test_app_exposes_routes():
    assert len(_endpoint_routes()) > 10


def test_every_endpoint_is_async_def():
    offenders = [
        f"{sorted(route.methods)} {route.path} -> {route.endpoint.__qualname__}"
        for route in _endpoint_routes()
        if not inspect.iscoroutinefunction(route.endpoint)
    ]
    assert not offenders, "Plain def endpoints tie up a threadpool slot:\n" + (
        "\n".join(offenders)
    )


def test_unused_sync_embedding_helpers_are_gone():
    assert not hasattr(embeddings, "embed_texts_sync")
    assert not hasattr(embeddings, "embed_text_sync")
