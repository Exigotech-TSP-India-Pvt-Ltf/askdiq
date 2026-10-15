"""Client-id sanitizing helper.

Authentication and authorization moved to `app.auth.deps` (SSO sessions plus
the `X-API-Key` proxy service key). The actor identity is now the signed-in
user's email; `X-Client-Id` is client-controlled and is never trusted as an
identity. `sanitize_client_id` remains for callers that log or echo a
client-supplied id and must normalise it safely.
"""

import re

_MAX_CLIENT_ID_LENGTH = 64

DEFAULT_ACTOR = "default@local"

# Allows UUID v4 or safe alphanumeric strings with dash/underscore/dot/at, max 64 chars.
_SAFE_CLIENT_ID_RE = re.compile(
    r"^(?:[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}|[a-zA-Z0-9_\-\.@]{1,64})$"
)


def sanitize_client_id(client_id: str | None) -> str:
    """Validate client_id format; normalize to DEFAULT_ACTOR if invalid/suspicious."""
    if not client_id:
        return DEFAULT_ACTOR
    cleaned = client_id.strip()
    if len(cleaned) <= _MAX_CLIENT_ID_LENGTH and _SAFE_CLIENT_ID_RE.match(cleaned):
        return cleaned
    return DEFAULT_ACTOR
