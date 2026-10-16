"""Client-id sanitizing helper.

Authentication and authorization live in `app.auth.deps` (SSO sessions plus
the `X-API-Key` service key). For dashboard users the actor is the signed-in
user's email. For the website (service key, no session) the actor is the
sanitised `X-Client-Id`: it partitions sessions and rate limits between
visitors but is not a security boundary (the key is).
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
