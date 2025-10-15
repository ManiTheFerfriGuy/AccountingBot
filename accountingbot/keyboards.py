"""Keyboard helpers for AccountingBot."""
from __future__ import annotations

from typing import Iterable, List, Tuple

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup

from .localization import get_text


def main_menu(language: str) -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        [
            [
                get_text("add_person", language),
                get_text("add_debt", language),
            ],
            [
                get_text("pay_debt", language),
                get_text("history", language),
            ],
            [
                get_text("search", language),
                get_text("dashboard", language),
            ],
            [get_text("language", language)],
        ],
        resize_keyboard=True,
    )


def person_keyboard(language: str, people: Iterable[Tuple[int, str]]) -> InlineKeyboardMarkup:
    buttons: List[List[InlineKeyboardButton]] = []
    row: List[InlineKeyboardButton] = []
    for person_id, name in people:
        label = f"{name} (#{person_id})"
        row.append(InlineKeyboardButton(label, callback_data=f"person:{person_id}"))
        if len(row) == 2:
            buttons.append(row)
            row = []
    if row:
        buttons.append(row)
    buttons.append([InlineKeyboardButton(get_text("search", language), callback_data="search")])
    buttons.append([InlineKeyboardButton(get_text("cancel", language), callback_data="cancel")])
    return InlineKeyboardMarkup(buttons)


def confirmation_keyboard(language: str, action: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton(get_text("confirmed", language), callback_data=f"confirm:{action}")],
            [InlineKeyboardButton(get_text("back", language), callback_data="back")],
        ]
    )


def language_keyboard() -> InlineKeyboardMarkup:
    from .localization import available_languages

    buttons = [
        [InlineKeyboardButton(label, callback_data=f"lang:{code}")]
        for code, label in available_languages().items()
    ]
    return InlineKeyboardMarkup(buttons)
