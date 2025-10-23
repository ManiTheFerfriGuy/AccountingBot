from __future__ import annotations

from datetime import datetime, timezone

from telegram import Update
from telegram.ext import ConversationHandler

from accountingbot.bot import (
    ADD_PERSON_NAME,
    LANGUAGE_SELECTION,
    cancel,
    change_language,
    register_handlers,
)


class DummyBot:
    def __init__(self) -> None:
        self.id = 123456
        self.token = "TEST:TOKEN"


class FakeApplication:
    def __init__(self) -> None:
        self.handlers: list[object] = []

    def add_handler(self, handler, group=None) -> None:  # noqa: D401 - match signature
        self.handlers.append(handler)


def _make_callback_update(data: str, chat_id: int, user_id: int) -> Update:
    payload = {
        "update_id": 1,
        "callback_query": {
            "id": "1",
            "from": {"id": user_id, "is_bot": False, "first_name": "Test"},
            "data": data,
            "chat_instance": "abc",
            "message": {
                "message_id": 100,
                "date": int(datetime.now(tz=timezone.utc).timestamp()),
                "chat": {"id": chat_id, "type": "private"},
            },
        },
    }
    return Update.de_json(payload, DummyBot())


def _find_conversation(app: FakeApplication, name: str) -> ConversationHandler:
    for handler in app.handlers:
        if isinstance(handler, ConversationHandler) and handler.name == name:
            return handler
    raise AssertionError(f"ConversationHandler {name!r} not registered")


def test_cancel_callback_reaches_handler() -> None:
    app = FakeApplication()
    register_handlers(app)

    conv = _find_conversation(app, "add_person")
    assert not conv.per_message

    chat_id, user_id = 99, 42
    conv._conversations[(chat_id, user_id)] = ADD_PERSON_NAME

    update = _make_callback_update("workflow:cancel", chat_id, user_id)
    result = conv.check_update(update)
    assert result is not None

    state, key, handler, _ = result
    assert state == ADD_PERSON_NAME
    assert key == (chat_id, user_id)
    assert handler.callback is cancel


def test_language_option_callback_reaches_handler() -> None:
    app = FakeApplication()
    register_handlers(app)

    conv = _find_conversation(app, "language")
    assert not conv.per_message

    chat_id, user_id = 199, 7
    conv._conversations[(chat_id, user_id)] = LANGUAGE_SELECTION

    update = _make_callback_update("lang:en", chat_id, user_id)
    result = conv.check_update(update)
    assert result is not None

    state, key, handler, _ = result
    assert state == LANGUAGE_SELECTION
    assert key == (chat_id, user_id)
    assert handler.callback is change_language
