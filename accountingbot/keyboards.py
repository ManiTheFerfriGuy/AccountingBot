"""Keyboard helpers for AccountingBot."""
from __future__ import annotations

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
            [get_text("dashboard", language)],
            [get_text("language", language)],
        ],
        resize_keyboard=True,
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
            [InlineKeyboardButton(get_text("cancel", language), callback_data="cancel")],
        ]
    )


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
