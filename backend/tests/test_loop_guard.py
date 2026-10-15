"""The event-loop blocking detector catches real blocking and nothing else."""

from __future__ import annotations

import asyncio
import time

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from tests.loop_guard import LoopBlockingDetector


def _app() -> FastAPI:
    mini = FastAPI()

    @mini.get("/blocking")
    async def _blocking() -> dict[str, bool]:
        time.sleep(0.3)  # noqa: ASYNC251 - deliberate: the detector must catch this
        return {"ok": True}

    @mini.get("/awaiting")
    async def _awaiting() -> dict[str, bool]:
        await asyncio.sleep(0.3)
        return {"ok": True}

    return mini


async def _get(path: str) -> int:
    async with AsyncClient(
        transport=ASGITransport(app=_app()), base_url="http://t"
    ) as ac:
        return (await ac.get(path)).status_code


async def test_detector_fails_on_a_blocking_sleep_in_a_route():
    assert await _get("/awaiting") == 200  # warm-up
    with pytest.raises(AssertionError, match="blocked"):
        async with LoopBlockingDetector():
            assert await _get("/blocking") == 200


async def test_detector_fails_on_repeated_medium_stalls():
    with pytest.raises(AssertionError, match="times"):
        async with LoopBlockingDetector():
            for _ in range(5):
                time.sleep(0.08)  # noqa: ASYNC251 - deliberate repeated stalls
                await asyncio.sleep(0.03)


async def test_detector_passes_concurrent_awaiting_requests():
    assert await _get("/awaiting") == 200  # warm-up
    async with LoopBlockingDetector() as detector:
        codes = await asyncio.gather(*(_get("/awaiting") for _ in range(20)))
    assert codes == [200] * 20
    assert all(s <= detector.hard_s for s in detector.stalls)
