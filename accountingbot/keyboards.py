"""Keyboard helpers for AccountingBot."""
from __future__ import annotations

from itertools import islice
from typing import Iterable, Sequence

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
                    get_text("management_menu", language),
                    callback_data="menu:management",
                ),
                InlineKeyboardButton(
                    get_text("export_transactions", language),
                    callback_data="menu:export",
                ),
            ],
        ]
    )


def back_to_main_menu_keyboard(language: str) -> InlineKeyboardMarkup:
    """Inline keyboard with a single button to return to the main menu."""

    return InlineKeyboardMarkup(
        [[InlineKeyboardButton(get_text("back", language), callback_data="menu:back_to_main")]]
    )


def history_back_to_menu_keyboard(language: str) -> InlineKeyboardMarkup:
    """Inline keyboard prompting the user to return to the main menu after history results."""

    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    get_text("history_back_to_menu", language),
                    callback_data="menu:back_to_main",
                )
            ]
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


def management_menu_keyboard(language: str) -> InlineKeyboardMarkup:
    """Inline keyboard for management options."""

    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    get_text("language_management", language),
                    callback_data="management:language",
                )
            ],
            [
                InlineKeyboardButton(
                    get_text("contact_management", language),
                    callback_data="management:contacts",
                )
            ],
            [
                InlineKeyboardButton(
                    get_text("description_management", language),
                    callback_data="management:descriptions",
                )
            ],
            [
                InlineKeyboardButton(
                    get_text("database_management", language),
                    callback_data="management:database",
                )
            ],
            [
                InlineKeyboardButton(
                    get_text("back", language), callback_data="menu:back_to_main"
                )
            ],
        ]
    )


def contact_management_keyboard(language: str) -> InlineKeyboardMarkup:
    """Inline keyboard for contact management options."""

    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    get_text("contact_management_view_all", language),
                    callback_data="management:contacts:list",
                )
            ],
            [
                InlineKeyboardButton(
                    get_text("contact_management_edit", language),
                    callback_data="management:contacts:edit",
                )
            ],
            [
                InlineKeyboardButton(
                    get_text("contact_management_delete", language),
                    callback_data="management:contacts:delete",
                )
            ],
            [
                InlineKeyboardButton(
                    get_text("back", language), callback_data="management:menu"
                )
            ],
        ]
    )


def database_management_keyboard(language: str) -> InlineKeyboardMarkup:
    """Inline keyboard for database management options."""

    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    get_text("database_management_backup_now", language),
                    callback_data="management:database:backup",
                )
            ],
            [
                InlineKeyboardButton(
                    get_text("database_management_zip_all", language),
                    callback_data="management:database:zip",
                )
            ],
            [
                InlineKeyboardButton(
                    get_text("back", language), callback_data="management:menu"
                )
            ],
        ]
    )


def description_management_keyboard(language: str) -> InlineKeyboardMarkup:
    """Inline keyboard for description management options."""

    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    get_text("description_management_edit", language),
                    callback_data="management:descriptions:edit",
                )
            ],
            [
                InlineKeyboardButton(
                    get_text("description_management_delete", language),
                    callback_data="management:descriptions:delete",
                )
            ],
            [
                InlineKeyboardButton(
                    get_text("back", language), callback_data="management:menu"
                )
            ],
        ]
    )


def person_description_keyboard(
    descriptions: Sequence[str], language: str
) -> InlineKeyboardMarkup:
    """Inline keyboard listing descriptions for a person."""

    buttons: list[list[InlineKeyboardButton]] = []
    for index, description in enumerate(descriptions):
        label = description.strip() or get_text("description_management_empty", language)
        label = label.replace("\n", " ")
        if len(label) > 32:
            label = label[:29] + "..."
        buttons.append(
            [
                InlineKeyboardButton(
                    label,
                    callback_data=f"description:select:{index}",
                )
            ]
        )

    buttons.append(
        [
            InlineKeyboardButton(
                get_text("back", language), callback_data="description:back_contact"
            )
        ]
    )
    buttons.append(
        [
            InlineKeyboardButton(
                get_text("cancel", language), callback_data="workflow:cancel"
            )
        ]
    )
    return InlineKeyboardMarkup(buttons)


def description_delete_confirmation_keyboard(language: str) -> InlineKeyboardMarkup:
    """Inline keyboard prompting the user to confirm description deletion."""

    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    get_text("confirm_delete", language),
                    callback_data="description:delete:confirm",
                )
            ],
            [
                InlineKeyboardButton(
                    get_text("back", language),
                    callback_data="description:delete:back",
                )
            ],
            [
                InlineKeyboardButton(
                    get_text("cancel", language), callback_data="workflow:cancel"
                )
            ],
        ]
    )


def description_edit_keyboard(language: str) -> InlineKeyboardMarkup:
    """Inline keyboard shown while editing a description."""

    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    get_text("back", language), callback_data="description:back_list"
                )
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
    *,
    search_active: bool,
) -> InlineKeyboardMarkup:
    buttons: list[list[InlineKeyboardButton]] = []

    search_row: list[InlineKeyboardButton] = [
        InlineKeyboardButton(
            get_text("menu_search_button", language),
            callback_data="person_search:start",
        )
    ]
    if search_active:
        search_row.append(
            InlineKeyboardButton(
                get_text("clear_search", language),
                callback_data="person_search:clear",
            )
        )
    buttons.append(search_row)

    buttons.extend(
        [
            [
                InlineKeyboardButton(
                    f"{entry.person.name} (#{entry.person.id})",
                    callback_data=f"select_person:{entry.person.id}",
                )
            ]
            for entry in people
        ]
    )

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


def manage_person_keyboard(person_id: int, language: str) -> InlineKeyboardMarkup:
    """Inline keyboard offering actions for a specific contact."""

    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    get_text("rename_person", language),
                    callback_data=f"person_manage:rename:{person_id}",
                ),
                InlineKeyboardButton(
                    get_text("delete_person", language),
                    callback_data=f"person_manage:delete:{person_id}",
                ),
            ],
            [
                InlineKeyboardButton(
                    get_text("cancel", language), callback_data="workflow:cancel"
                )
            ],
        ]
    )


def confirm_delete_person_keyboard(
    person_id: int, language: str
) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    get_text("confirm_delete", language),
                    callback_data=f"person_manage:confirm_delete:{person_id}",
                ),
                InlineKeyboardButton(
                    get_text("go_back", language),
                    callback_data=f"person_manage:back:{person_id}",
                ),
            ],
            [
                InlineKeyboardButton(
                    get_text("cancel", language), callback_data="workflow:cancel"
                )
            ],
        ]
    )


def history_range_keyboard(language: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    get_text("history_range_today", language),
                    callback_data="history:range:today",
                ),
                InlineKeyboardButton(
                    get_text("history_range_last_7_days", language),
                    callback_data="history:range:last7",
                ),
            ],
            [
                InlineKeyboardButton(
                    get_text("history_range_this_month", language),
                    callback_data="history:range:this_month",
                ),
                InlineKeyboardButton(
                    get_text("history_range_custom", language),
                    callback_data="history:range:custom",
                ),
            ],
            [
                InlineKeyboardButton(
                    get_text("history_range_skip", language),
                    callback_data="history:range:skip",
                )
            ],
            [
                InlineKeyboardButton(
                    get_text("cancel", language), callback_data="workflow:cancel"
                )
            ],
        ]
    )


def _chunked(iterable: Iterable[str], size: int) -> Iterable[list[str]]:
    iterator = iter(iterable)
    while True:
        chunk = list(islice(iterator, size))
        if not chunk:
            return
        yield chunk


def _history_custom_keyboard(
    language: str,
    phase: str,
    level: str,
    values: Iterable[str],
    label_fn,
) -> InlineKeyboardMarkup:
    buttons: list[list[InlineKeyboardButton]] = []
    for chunk in _chunked(values, 3):
        row = [
            InlineKeyboardButton(
                label_fn(value),
                callback_data=f"history:custom:{phase}:{level}:{value}",
            )
            for value in chunk
        ]
        buttons.append(row)
    buttons.append(
        [InlineKeyboardButton(get_text("cancel", language), callback_data="workflow:cancel")]
    )
    return InlineKeyboardMarkup(buttons)


def history_custom_year_keyboard(
    language: str, years: Sequence[int], phase: str
) -> InlineKeyboardMarkup:
    values = [str(year) for year in years]
    return _history_custom_keyboard(
        language,
        phase,
        "year",
        values,
        label_fn=lambda value: value,
    )


def history_custom_month_keyboard(
    language: str, months: Sequence[int], phase: str
) -> InlineKeyboardMarkup:
    values = [str(month) for month in months]
    return _history_custom_keyboard(
        language,
        phase,
        "month",
        values,
        label_fn=lambda value: f"{int(value):02d}",
    )


def history_custom_day_keyboard(
    language: str, days: Sequence[int], phase: str
) -> InlineKeyboardMarkup:
    values = [str(day) for day in days]
    return _history_custom_keyboard(
        language,
        phase,
        "day",
        values,
        label_fn=lambda value: f"{int(value):02d}",
    )


def history_custom_hour_keyboard(
    language: str, hours: Sequence[int], phase: str
) -> InlineKeyboardMarkup:
    values = [str(hour) for hour in hours]
    return _history_custom_keyboard(
        language,
        phase,
        "hour",
        values,
        label_fn=lambda value: f"{int(value):02d}:00",
    )


def history_confirmation_keyboard(language: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    get_text("history_confirm_button", language),
                    callback_data="history:confirm:ok",
                ),
                InlineKeyboardButton(
                    get_text("history_restart_button", language),
                    callback_data="history:confirm:restart",
                ),
            ],
            [
                InlineKeyboardButton(
                    get_text("cancel", language), callback_data="workflow:cancel"
                )
            ],
        ]
    )


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
