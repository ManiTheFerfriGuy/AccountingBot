"""Keyboard helpers for AccountingBot."""
from __future__ import annotations

from typing import Sequence

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from .database import SearchResult
from .localization import available_languages, get_text


def main_menu_keyboard(language: str) -> InlineKeyboardMarkup:
    """Return the inline keyboard shown on the start screen."""

    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    get_text("add_person", language), callback_data="menu:add_person"
                ),
                InlineKeyboardButton(
                    get_text("add_debt", language), callback_data="menu:add_debt"
                ),
            ],
            [
                InlineKeyboardButton(
                    get_text("pay_debt", language), callback_data="menu:pay_debt"
                ),
                InlineKeyboardButton(
                    get_text("history", language), callback_data="menu:history"
                ),
            ],
            [
                InlineKeyboardButton(
                    get_text("dashboard", language), callback_data="menu:dashboard"
                ),
                InlineKeyboardButton(
                    get_text("search", language), callback_data="menu:search"
                ),
            ],
            [
                InlineKeyboardButton(
                    get_text("list_people", language), callback_data="menu:list_people"
                ),
                InlineKeyboardButton(
                    get_text("language", language), callback_data="menu:language"
                ),
            ],
        ]
    )


def cancel_keyboard(language: str) -> InlineKeyboardMarkup:
    """Inline keyboard with a single cancel button."""

    return InlineKeyboardMarkup(
        [[InlineKeyboardButton(get_text("cancel", language), callback_data="workflow:cancel")]]
    )


def skip_keyboard(language: str, flow: str) -> InlineKeyboardMarkup:
    """Inline keyboard allowing the user to skip optional steps."""

    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    get_text("skip_optional", language), callback_data=f"skip:{flow}"
                ),
                InlineKeyboardButton(
                    get_text("cancel", language), callback_data="workflow:cancel"
                ),
            ]
        ]
    )


def selection_method_keyboard(language: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    get_text("select_using_id", language), callback_data="method:id"
                ),
                InlineKeyboardButton(
                    get_text("select_using_menu", language), callback_data="method:menu"
                ),
            ],
            [
                InlineKeyboardButton(
                    get_text("cancel", language), callback_data="workflow:cancel"
                )
            ],
        ]
    )


def confirmation_keyboard(language: str, action: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    get_text("confirmed", language), callback_data=f"confirm:{action}"
                ),
                InlineKeyboardButton(
                    get_text("back", language), callback_data=f"back:{action}"
                ),
            ],
            [
                InlineKeyboardButton(
                    get_text("cancel", language), callback_data="workflow:cancel"
                )
            ],
        ]
    )


def language_keyboard(language: str) -> InlineKeyboardMarkup:
    buttons = [
        [InlineKeyboardButton(label, callback_data=f"lang:{code}")]
        for code, label in available_languages().items()
    ]
    buttons.append(
        [InlineKeyboardButton(get_text("cancel", language), callback_data="workflow:cancel")]
    )
    return InlineKeyboardMarkup(buttons)


def search_results_keyboard(matches: Sequence[SearchResult]) -> InlineKeyboardMarkup:
    """Inline keyboard listing the top person matches for quick selection."""

    buttons = [
        [
            InlineKeyboardButton(
                f"{match.person.name} (#{match.person.id})",
                callback_data=f"select_person:{match.person.id}",
            )
        ]
        for match in matches[:5]
    ]
    return InlineKeyboardMarkup(buttons)
