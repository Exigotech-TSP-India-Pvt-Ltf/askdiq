"""Event-loop thread pool sizing for blocking Azure/ONNX calls."""

import asyncio
from concurrent.futures import ThreadPoolExecutor

from app.core.config import get_settings


def configure_thread_pool() -> None:
    """Replace the running loop's default executor (cpu+4 workers) with a larger one."""
    asyncio.get_running_loop().set_default_executor(
        ThreadPoolExecutor(
            max_workers=get_settings().thread_pool_workers,
            thread_name_prefix="askdiq",
        )
    )
