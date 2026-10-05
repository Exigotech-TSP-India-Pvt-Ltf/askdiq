import pytest
from fastapi import HTTPException

from app.core import rate_limit


def _reset():
    rate_limit._user_request_log.clear()
    rate_limit._ip_request_log.clear()


def test_allows_requests_up_to_the_configured_limit(monkeypatch):
    _reset()
    monkeypatch.setattr(rate_limit.settings, "rate_limit_per_minute", 3)

    for _ in range(3):
        rate_limit.enforce_rate_limit("user@example.com")


def test_blocks_once_the_limit_is_exceeded(monkeypatch):
    _reset()
    monkeypatch.setattr(rate_limit.settings, "rate_limit_per_minute", 2)

    rate_limit.enforce_rate_limit("user@example.com")
    rate_limit.enforce_rate_limit("user@example.com")

    with pytest.raises(HTTPException) as exc_info:
        rate_limit.enforce_rate_limit("user@example.com")

    assert exc_info.value.status_code == 429
    assert "Retry-After" in exc_info.value.headers


def test_limits_are_tracked_independently_per_user(monkeypatch):
    _reset()
    monkeypatch.setattr(rate_limit.settings, "rate_limit_per_minute", 1)

    rate_limit.enforce_rate_limit("user-a@example.com")
    # A different user must not be blocked by user A's usage.
    rate_limit.enforce_rate_limit("user-b@example.com")

    with pytest.raises(HTTPException):
        rate_limit.enforce_rate_limit("user-a@example.com")


def test_old_requests_fall_out_of_the_sliding_window(monkeypatch):
    _reset()
    monkeypatch.setattr(rate_limit.settings, "rate_limit_per_minute", 1)

    fake_now = [1000.0]
    monkeypatch.setattr(rate_limit.time, "monotonic", lambda: fake_now[0])

    rate_limit.enforce_rate_limit("user@example.com")
    with pytest.raises(HTTPException):
        rate_limit.enforce_rate_limit("user@example.com")

    # Advance time past the 60s window — the old request should no longer count.
    fake_now[0] += 61
    rate_limit.enforce_rate_limit("user@example.com")


def test_ip_limit_allows_up_to_the_configured_limit(monkeypatch):
    _reset()
    monkeypatch.setattr(rate_limit.settings, "rate_limit_per_minute_per_ip", 3)

    for _ in range(3):
        rate_limit.enforce_ip_rate_limit("203.0.113.5")


def test_ip_limit_blocks_once_exceeded(monkeypatch):
    _reset()
    monkeypatch.setattr(rate_limit.settings, "rate_limit_per_minute_per_ip", 2)

    rate_limit.enforce_ip_rate_limit("203.0.113.5")
    rate_limit.enforce_ip_rate_limit("203.0.113.5")

    with pytest.raises(HTTPException) as exc_info:
        rate_limit.enforce_ip_rate_limit("203.0.113.5")

    assert exc_info.value.status_code == 429


def test_user_and_ip_limits_are_tracked_independently(monkeypatch):
    _reset()
    monkeypatch.setattr(rate_limit.settings, "rate_limit_per_minute", 1)
    monkeypatch.setattr(rate_limit.settings, "rate_limit_per_minute_per_ip", 100)

    # Exhausting the per-user budget must not touch the per-IP counter.
    rate_limit.enforce_rate_limit("user@example.com")
    with pytest.raises(HTTPException):
        rate_limit.enforce_rate_limit("user@example.com")

    rate_limit.enforce_ip_rate_limit("203.0.113.5")
