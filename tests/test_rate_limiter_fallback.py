"""Tests for graceful fallback when the optional rate limiter is unavailable."""
from types import SimpleNamespace

import logging


from accountingbot import bot


class _DummyBuilder:
    """Stand-in for :class:`ApplicationBuilder` to observe configuration calls."""

    def __init__(self):
        self.token_value = None
        self.rate_limiter_called = False

    def token(self, value):  # pragma: no cover - simple pass-through
        self.token_value = value
        return self

    def rate_limiter(self, limiter):  # pragma: no cover - simple pass-through
        self.rate_limiter_called = True
        return self

    def build(self):  # pragma: no cover - simple pass-through
        return SimpleNamespace(token=self.token_value, limited=self.rate_limiter_called)


def test_build_application_without_rate_limiter(monkeypatch, caplog):
    """When the rate limiter dependencies are missing, the bot should still start."""

    builder_instances = []

    def fake_builder():
        instance = _DummyBuilder()
        builder_instances.append(instance)
        return instance

    class FailingRateLimiter:
        def __init__(self):
            raise RuntimeError("extras missing")

    monkeypatch.setattr(bot, "ApplicationBuilder", fake_builder)
    monkeypatch.setattr(bot, "AIORateLimiter", FailingRateLimiter)

    caplog.set_level(logging.WARNING)

    config = SimpleNamespace(token="dummy-token")
    application = bot.build_application(config)

    assert builder_instances, "Expected ApplicationBuilder to be instantiated"
    builder = builder_instances[0]
    assert builder.token_value == "dummy-token"
    assert builder.rate_limiter_called is False
    assert application.token == "dummy-token"
    assert application.limited is False
    assert any("Rate limiter disabled" in message for message in caplog.messages)
