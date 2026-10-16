"""GZip middleware that never buffers Server-Sent Events.

Starlette's ``GZipMiddleware`` compresses streaming bodies through a zlib
buffer, which would hold SSE frames (and heartbeats) back from the client.
``text/event-stream`` responses are passed through uncompressed.
"""

from __future__ import annotations

from starlette.datastructures import Headers
from starlette.middleware.gzip import GZipMiddleware, GZipResponder
from starlette.types import Message, Receive, Scope, Send

SSE_MEDIA_TYPE = "text/event-stream"


class _SSEAwareGZipResponder(GZipResponder):
    async def send_with_gzip(self, message: Message) -> None:
        if message["type"] == "http.response.start":
            content_type = Headers(raw=message["headers"]).get("content-type", "")
            if content_type.startswith(SSE_MEDIA_TYPE):
                self.initial_message = message
                # Same path Starlette takes for pre-encoded bodies: pass through.
                self.content_encoding_set = True
                return
        await super().send_with_gzip(message)


class SSEAwareGZipMiddleware(GZipMiddleware):
    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http":
            headers = Headers(scope=scope)
            if "gzip" in headers.get("Accept-Encoding", ""):
                responder = _SSEAwareGZipResponder(
                    self.app, self.minimum_size, compresslevel=self.compresslevel
                )
                await responder(scope, receive, send)
                return
        await self.app(scope, receive, send)
