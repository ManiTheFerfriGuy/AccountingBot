"""Keyboard helpers for AccountingBot."""
from __future__ import annotations

from typing import Sequence

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from .database import PersonUsageStats, SearchResult
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
                    get_text("list_people", language), callback_data="menu:list_people"
                ),
            ],
            [
                InlineKeyboardButton(
                    get_text("language", language), callback_data="menu:language"
                ),
                InlineKeyboardButton(
                    get_text("export_transactions", language),
                    callback_data="menu:export",
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


def person_menu_keyboard(
    people: Sequence[PersonUsageStats],
    language: str,
    page: int,
    total_pages: int,
) -> InlineKeyboardMarkup:
    buttons: list[list[InlineKeyboardButton]] = [
        [
            InlineKeyboardButton(
                f"{entry.person.name} (#{entry.person.id})",
                callback_data=f"select_person:{entry.person.id}",
            )
        ]
        for entry in people
    ]

    if total_pages > 1:
        nav_buttons: list[InlineKeyboardButton] = []
        if page > 0:
            nav_buttons.append(
                InlineKeyboardButton(
                    get_text("previous_page", language),
                    callback_data=f"person_page:{page - 1}",
                )
            )
        if page < total_pages - 1:
            nav_buttons.append(
                InlineKeyboardButton(
                    get_text("next_page", language),
                    callback_data=f"person_page:{page + 1}",
                )
            )
        if nav_buttons:
            buttons.append(nav_buttons)

    buttons.append(
        [InlineKeyboardButton(get_text("cancel", language), callback_data="workflow:cancel")]
    )

    return InlineKeyboardMarkup(buttons)


def export_mode_keyboard(language: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    get_text("export_type_all", language), callback_data="export:mode:all"
                )
            ],
            [
                InlineKeyboardButton(
                    get_text("export_type_debt", language), callback_data="export:mode:debt"
                )
            ],
            [
                InlineKeyboardButton(
                    get_text("export_type_payment", language),
                    callback_data="export:mode:payment",
                )
            ],
            [
                InlineKeyboardButton(
                    get_text("cancel", language), callback_data="workflow:cancel"
                )
            ],
        ]
    )


def export_contact_keyboard(language: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    get_text("export_contacts_all", language),
                    callback_data="export:contacts:all",
                )
            ],
            [
                InlineKeyboardButton(
                    get_text("export_contacts_specific", language),
                    callback_data="export:contacts:choose",
                )
            ],
            [
                InlineKeyboardButton(
                    get_text("cancel", language), callback_data="workflow:cancel"
                )
            ],
        ]
    )
