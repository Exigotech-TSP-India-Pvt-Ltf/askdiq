"""Unit tests for usage_queries SQL builder and ILIKE escaping (Task 3.1)."""

from app.governance.usage_queries import escape_ilike_pattern


def test_escape_ilike_pattern():
    # Wildcards
    assert escape_ilike_pattern("100% discount") == "100\\% discount"
    assert escape_ilike_pattern("user_name") == "user\\_name"
    assert escape_ilike_pattern("path\\to\\file") == "path\\\\to\\\\file"
    assert escape_ilike_pattern("50%_off\\now") == "50\\%\\_off\\\\now"

    # Empty / clean text
    assert escape_ilike_pattern("") == ""
    assert escape_ilike_pattern("regular query text") == "regular query text"
