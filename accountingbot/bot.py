"""Telegram bot entry point for AccountingBot."""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from html import escape
from typing import Optional

from telegram import InlineKeyboardMarkup, Update, constants
from telegram.ext import (AIORateLimiter, Application, ApplicationBuilder,
                          CallbackQueryHandler, CommandHandler,
                          ConversationHandler, ContextTypes, MessageHandler,
                          filters)

from .config import load_config
from .database import (
    DashboardSummary,
    Database,
    InvalidPersonNameError,
    Person,
    PersonAlreadyExistsError,
    SearchResponse,
)
from .keyboards import (
    confirmation_keyboard,
    language_keyboard,
    main_menu,
    selection_method_keyboard,
)
from .localization import get_text

# Conversation states
ADD_PERSON_NAME = 1
SELECT_DEBT_PERSON, ENTER_DEBT_AMOUNT, ENTER_DEBT_DESCRIPTION, CONFIRM_DEBT = range(10, 14)
SELECT_PAYMENT_PERSON, ENTER_PAYMENT_AMOUNT, ENTER_PAYMENT_DESCRIPTION, CONFIRM_PAYMENT = range(20, 24)
SELECT_HISTORY_PERSON, ENTER_HISTORY_DATES = range(30, 32)
SEARCH_QUERY = 40

LOGGER = logging.getLogger(__name__)


async def get_language(context: ContextTypes.DEFAULT_TYPE, user_id: int) -> str:
    language = context.user_data.get("language")
    if language:
        return language
    db: Database = context.bot_data["db"]
    language = await db.get_user_language(user_id)
    context.user_data["language"] = language
    return language


def build_people_menu_keyboard(
    language: str,
    people: list[Person],
    *,
    page: int,
    has_prev: bool,
    has_next: bool,
    query: str | None,
) -> InlineKeyboardMarkup:
    from telegram import InlineKeyboardButton

    buttons: list[list[InlineKeyboardButton]] = []
    row: list[InlineKeyboardButton] = []
    for person in people:
        row.append(
            InlineKeyboardButton(
                f"{person.name} (#{person.id})", callback_data=f"person:{person.id}"
            )
        )
        if len(row) == 2:
            buttons.append(row)
            row = []
    if row:
        buttons.append(row)

    nav_row: list[InlineKeyboardButton] = []
    if has_prev:
        nav_row.append(
            InlineKeyboardButton(
                get_text("previous_page", language), callback_data=f"menu:page:{page - 1}"
            )
        )
    if has_next:
        nav_row.append(
            InlineKeyboardButton(
                get_text("next_page", language), callback_data=f"menu:page:{page + 1}"
            )
        )
    if nav_row:
        buttons.append(nav_row)

    search_row: list[InlineKeyboardButton] = [
        InlineKeyboardButton(get_text("search", language), callback_data="menu:search")
    ]
    if query:
        search_row.append(
            InlineKeyboardButton(
                get_text("clear_search", language), callback_data="menu:clear"
            )
        )
    buttons.append(search_row)

    buttons.append(
        [
            InlineKeyboardButton(get_text("back", language), callback_data="menu:back"),
            InlineKeyboardButton(get_text("cancel", language), callback_data="cancel"),
        ]
    )
    return InlineKeyboardMarkup(buttons)


def format_balance_status(balance: float, language: str) -> str:
    if balance > 0:
        return get_text("balance_debtor", language).format(amount=balance)
    if balance < 0:
        return get_text("balance_creditor", language).format(amount=abs(balance))
    return get_text("balance_settled", language)


def format_search_results(language: str, response: SearchResponse) -> str:
    lines = [get_text("search_results", language)]
    for index, match in enumerate(response.matches[:5], start=1):
        person = match.person
        score_percent = int(round(min(max(match.score, 0.0), 1.0) * 100))
        status = format_balance_status(match.balance, language)
        lines.append(
            get_text("search_result_item", language).format(
                index=index,
                name=person.name,
                id=person.id,
                status=status,
                score=score_percent,
            )
        )
    if response.suggestions:
        suggestions = ", ".join(response.suggestions)
        lines.append(
            get_text("search_suggestions", language).format(suggestions=suggestions)
        )
    return "\n".join(lines)


def format_dashboard(summary: DashboardSummary, language: str) -> str:
    lines = [get_text("dashboard_summary", language)]
    totals = summary.totals
    lines.extend(
        [
            f"{get_text('total_debt', language)}: {totals.total_debt:.2f}",
            f"{get_text('total_payments', language)}: {totals.total_payments:.2f}",
            f"{get_text('outstanding_balance', language)}: {totals.outstanding_balance:.2f}",
        ]
    )

    if summary.top_debtors:
        lines.append(get_text("top_debtors", language))
        for index, debtor in enumerate(summary.top_debtors, start=1):
            person = debtor.person
            lines.append(
                f"{index}. {person.name} (#{person.id}) — {debtor.balance:.2f}"
            )
    else:
        lines.append(get_text("no_debtors", language))

    if summary.recent_transactions:
        lines.append(get_text("recent_transactions", language))
        for activity in summary.recent_transactions:
            transaction = activity.transaction
            template = (
                "recent_transaction_debt"
                if transaction.amount > 0
                else "recent_transaction_payment"
            )
            lines.append(
                get_text(template, language).format(
                    name=activity.person_name,
                    amount=abs(transaction.amount),
                    date=transaction.created_at.strftime("%Y-%m-%d %H:%M"),
                    description=transaction.description or "-",
                )
            )
    else:
        lines.append(get_text("no_transactions", language))

    return "\n".join(lines)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    language = await get_language(context, user.id)
    keyboard = main_menu(language)
    await update.message.reply_text(
        get_text("start_message", language),
        reply_markup=keyboard,
    )


async def show_main_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    language = await get_language(context, user.id)
    keyboard = main_menu(language)
    if update.callback_query:
        await update.callback_query.answer()
        await update.callback_query.message.edit_text(
            get_text("start_message", language), reply_markup=keyboard
        )
    else:
        await update.message.reply_text(
            get_text("start_message", language), reply_markup=keyboard
        )


async def show_dashboard(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    language = await get_language(context, update.effective_user.id)
    db: Database = context.bot_data["db"]
    summary = await db.get_dashboard_summary()
    text = format_dashboard(summary, language)
    if update.callback_query:
        await update.callback_query.answer()
        await update.callback_query.message.edit_text(
            text, reply_markup=main_menu(language)
        )
    else:
        await update.message.reply_text(text, reply_markup=main_menu(language))
    clear_workflow(context)


def clear_workflow(context: ContextTypes.DEFAULT_TYPE) -> None:
    for key in (
        "selection_mode",
        "selection_state",
        "after_state",
        "selected_person",
        "person",
        "amount",
        "description",
        "flow",
        "selection_method",
        "menu_query",
        "menu_page",
        "awaiting_menu_search",
    ):
        context.user_data.pop(key, None)


async def show_selection_method(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> int:
    language = await get_language(context, update.effective_user.id)
    keyboard = selection_method_keyboard(language)
    text = get_text("choose_selection_method", language)
    if update.callback_query:
        await update.callback_query.edit_message_text(text, reply_markup=keyboard)
    else:
        await update.message.reply_text(text, reply_markup=keyboard)
    return context.user_data.get("selection_state", ConversationHandler.END)


async def show_people_menu(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    *,
    page: int = 0,
    query: str | None = None,
) -> int:
    language = await get_language(context, update.effective_user.id)
    db: Database = context.bot_data["db"]
    page_size = 8
    if query is None:
        stored_query = context.user_data.get("menu_query", "")
        query = stored_query if stored_query else None
    else:
        context.user_data["menu_query"] = query
    context.user_data["menu_page"] = page
    context.user_data["selection_method"] = "menu"
    context.user_data["selection_mode"] = True
    context.user_data["awaiting_menu_search"] = False

    people: list[Person]
    has_next = False
    has_prev = page > 0
    header_text: str

    if query:
        response = await db.search_people(query)
        matches = [match.person for match in response.matches]
        total_matches = len(matches)
        start_index = page * page_size
        people = matches[start_index : start_index + page_size]
        has_next = start_index + page_size < total_matches
        has_prev = page > 0 and start_index > 0
        if people:
            header_text = get_text("menu_search_results", language).format(
                query=query, count=total_matches, page=page + 1
            )
        else:
            header_text = get_text("menu_search_no_results", language).format(
                query=query
            )
    else:
        offset = page * page_size
        results = await db.list_people(limit=page_size + 1, offset=offset)
        has_next = len(results) > page_size
        people = results[:page_size]
        if not people and page > 0:
            # If the requested page no longer has items, show the previous page.
            return await show_people_menu(update, context, page=page - 1, query=None)
        if people:
            header_text = get_text("menu_prompt", language).format(page=page + 1)
        else:
            # No people in the database at all.
            message = get_text("no_people", language)
            if update.callback_query:
                await update.callback_query.edit_message_text(
                    message, reply_markup=main_menu(language)
                )
            else:
                await update.message.reply_text(message, reply_markup=main_menu(language))
            clear_workflow(context)
            return ConversationHandler.END

    keyboard = build_people_menu_keyboard(
        language,
        people,
        page=page,
        has_prev=has_prev,
        has_next=has_next,
        query=query,
    )

    if update.callback_query:
        await update.callback_query.edit_message_text(header_text, reply_markup=keyboard)
    else:
        await update.message.reply_text(header_text, reply_markup=keyboard)
    return context.user_data.get("selection_state", ConversationHandler.END)


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    language = await get_language(context, update.effective_user.id)
    if update.callback_query:
        await update.callback_query.answer(get_text("action_cancelled", language))
        await update.callback_query.message.edit_text(get_text("action_cancelled", language))
    else:
        await update.message.reply_text(get_text("action_cancelled", language))
    clear_workflow(context)
    return ConversationHandler.END


# ---- Add Person ----
async def prompt_person_name(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    language = await get_language(context, update.effective_user.id)
    await update.message.reply_text(get_text("enter_person_name", language))
    return ADD_PERSON_NAME


async def save_person_name(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    language = await get_language(context, update.effective_user.id)
    name = update.message.text.strip()
    db: Database = context.bot_data["db"]
    try:
        person = await db.add_person(name)
    except InvalidPersonNameError:
        await update.message.reply_text(get_text("invalid_person_name", language))
        return ADD_PERSON_NAME
    except PersonAlreadyExistsError:
        await update.message.reply_text(
            get_text("duplicate_person_name", language).format(name=name)
        )
        return ADD_PERSON_NAME
    await update.message.reply_text(
        get_text("person_added", language).format(name=person.name, id=person.id),
        reply_markup=main_menu(language),
    )
    return ConversationHandler.END


# ---- Helper: Select person ----
async def list_people_keyboard(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    selection_state: int,
    after_state: int,
) -> Optional[int]:
    db: Database = context.bot_data["db"]
    language = await get_language(context, update.effective_user.id)
    has_people = await db.list_people(limit=1)
    if not has_people:
        message = get_text("no_people", language)
        if update.callback_query:
            await update.callback_query.answer()
            await update.callback_query.message.edit_text(
                message, reply_markup=main_menu(language)
            )
        else:
            await update.message.reply_text(message, reply_markup=main_menu(language))
        clear_workflow(context)
        return ConversationHandler.END

    context.user_data["selection_mode"] = False
    context.user_data["selection_state"] = selection_state
    context.user_data["after_state"] = after_state
    context.user_data["selection_method"] = None
    context.user_data["menu_query"] = ""
    context.user_data["menu_page"] = 0
    context.user_data["awaiting_menu_search"] = False
    if update.callback_query:
        await update.callback_query.answer()
    return await show_selection_method(update, context)


async def select_person_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    language = await get_language(context, update.effective_user.id)
    data = query.data
    if data == "cancel":
        return await cancel(update, context)
    if data == "method:id":
        context.user_data["selection_method"] = "id"
        context.user_data["selection_mode"] = False
        context.user_data["awaiting_menu_search"] = False
        await query.edit_message_text(get_text("prompt_person_id", language))
        return context.user_data.get("selection_state", ConversationHandler.END)
    if data == "method:menu":
        context.user_data["menu_query"] = ""
        context.user_data["menu_page"] = 0
        return await show_people_menu(update, context, page=0, query=None)
    if data.startswith("menu:page:"):
        try:
            page = int(data.split(":", 2)[2])
        except (ValueError, IndexError):
            page = context.user_data.get("menu_page", 0)
        return await show_people_menu(update, context, page=max(page, 0), query=None)
    if data == "menu:search":
        context.user_data["awaiting_menu_search"] = True
        await query.edit_message_text(get_text("menu_search_prompt", language))
        return context.user_data.get("selection_state", ConversationHandler.END)
    if data == "menu:clear":
        context.user_data["menu_query"] = ""
        context.user_data["menu_page"] = 0
        return await show_people_menu(update, context, page=0, query=None)
    if data == "menu:back":
        context.user_data["selection_method"] = None
        context.user_data["selection_mode"] = False
        context.user_data["awaiting_menu_search"] = False
        return await show_selection_method(update, context)
    if data.startswith("person:"):
        person_id = int(data.split(":", 1)[1])
        context.user_data["selected_person"] = person_id
        context.user_data["selection_mode"] = False
        context.user_data["selection_method"] = None
        after_state = context.user_data.get("after_state")
        if after_state == ENTER_DEBT_AMOUNT:
            return await prompt_debt_amount(update, context)
        if after_state == ENTER_PAYMENT_AMOUNT:
            return await prompt_payment_amount(update, context)
        if after_state == ENTER_HISTORY_DATES:
            return await prompt_history_dates(update, context)
    return context.user_data.get("selection_state", ConversationHandler.END)


async def handle_person_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    language = await get_language(context, update.effective_user.id)
    db: Database = context.bot_data["db"]
    text = update.message.text.strip()
    selection_state = context.user_data.get("selection_state", ConversationHandler.END)

    normalized_text = text.casefold()
    if normalized_text == get_text("select_using_menu", language).casefold():
        context.user_data["menu_query"] = ""
        context.user_data["menu_page"] = 0
        return await show_people_menu(update, context, page=0, query=None)

    if normalized_text == get_text("select_using_id", language).casefold():
        context.user_data["selection_method"] = "id"
        context.user_data["selection_mode"] = False
        context.user_data["awaiting_menu_search"] = False
        await update.message.reply_text(get_text("prompt_person_id", language))
        return selection_state

    if context.user_data.get("awaiting_menu_search"):
        context.user_data["menu_query"] = text
        context.user_data["menu_page"] = 0
        return await show_people_menu(update, context, page=0, query=text)

    stripped_text = text.lstrip("#")
    method = context.user_data.get("selection_method")

    if stripped_text.isdigit():
        person = await db.get_person(int(stripped_text))
        if not person:
            await update.message.reply_text(get_text("not_found", language))
            return selection_state
        context.user_data["selected_person"] = person.id
        context.user_data["person"] = person
        context.user_data["selection_mode"] = False
        context.user_data["selection_method"] = None
        after_state = context.user_data.get("after_state")
        if after_state == ENTER_DEBT_AMOUNT:
            await update.message.reply_text(
                get_text("enter_debt_amount", language).format(name=person.name)
            )
            return ENTER_DEBT_AMOUNT
        if after_state == ENTER_PAYMENT_AMOUNT:
            await update.message.reply_text(
                get_text("enter_payment_amount", language).format(name=person.name)
            )
            return ENTER_PAYMENT_AMOUNT
        if after_state == ENTER_HISTORY_DATES:
            await update.message.reply_text(get_text("prompt_date_range", language))
            return ENTER_HISTORY_DATES
        return ConversationHandler.END

    if method == "id":
        await update.message.reply_text(get_text("prompt_person_id", language))
        return selection_state

    if method == "menu" or context.user_data.get("selection_mode"):
        context.user_data["menu_query"] = text
        context.user_data["menu_page"] = 0
        return await show_people_menu(update, context, page=0, query=text)

    await update.message.reply_text(get_text("choose_selection_method", language))
    return selection_state


async def search_people(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    language = await get_language(context, update.effective_user.id)
    db: Database = context.bot_data["db"]
    text = update.message.text.strip()
    response = await db.search_people(text)
    selection_mode = context.user_data.get("selection_mode")
    if not response.matches:
        message = get_text("not_found", language)
        if response.suggestions:
            message = get_text("search_suggestions", language).format(
                suggestions=", ".join(response.suggestions)
            )
        await update.message.reply_text(message)
        if selection_mode:
            return context.user_data.get("selection_state", ConversationHandler.END)
        await update.message.reply_text(get_text("search_filters_hint", language))
        return SEARCH_QUERY
    if selection_mode:
        context.user_data["menu_query"] = text
        context.user_data["menu_page"] = 0
        return await show_people_menu(update, context, page=0, query=text)

    formatted = format_search_results(language, response)
    await update.message.reply_text(formatted, reply_markup=main_menu(language))
    await update.message.reply_text(get_text("search_filters_hint", language))
    return SEARCH_QUERY


# ---- Add Debt ----
async def start_add_debt(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data["flow"] = "debt"
    return await list_people_keyboard(update, context, SELECT_DEBT_PERSON, ENTER_DEBT_AMOUNT)


async def prompt_debt_amount(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    language = await get_language(context, update.effective_user.id)
    db: Database = context.bot_data["db"]
    person_id = context.user_data["selected_person"]
    person = await db.get_person(person_id)
    context.user_data["person"] = person
    context.user_data["selection_mode"] = False
    message_text = get_text("enter_debt_amount", language).format(name=person.name)
    if update.callback_query:
        await update.callback_query.edit_message_text(message_text)
    else:
        await update.message.reply_text(message_text)
    return ENTER_DEBT_AMOUNT


async def receive_debt_amount(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    language = await get_language(context, update.effective_user.id)
    try:
        amount = float(update.message.text)
        if amount <= 0:
            raise ValueError
    except ValueError:
        await update.message.reply_text(get_text("invalid_number", language))
        return ENTER_DEBT_AMOUNT
    context.user_data["amount"] = amount
    await update.message.reply_text(get_text("enter_debt_description", language))
    return ENTER_DEBT_DESCRIPTION


async def receive_debt_description(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data["description"] = update.message.text.strip()
    return await confirm_debt(update, context)


async def skip_description(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data["description"] = ""
    flow = context.user_data.get("flow")
    if flow == "payment":
        return await confirm_payment(update, context)
    return await confirm_debt(update, context)


async def confirm_debt(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    language = await get_language(context, update.effective_user.id)
    person: Person = context.user_data["person"]
    amount = context.user_data["amount"]
    description = context.user_data.get("description", "")
    if description:
        text = get_text("confirm_debt_with_description", language).format(
            name=person.name, amount=amount, description=description
        )
    else:
        text = get_text("confirm_debt", language).format(name=person.name, amount=amount)
    keyboard = confirmation_keyboard(language, "debt")
    await update.message.reply_text(text, reply_markup=keyboard)
    return CONFIRM_DEBT


async def handle_back(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    flow = context.user_data.get("flow")
    language = await get_language(context, update.effective_user.id)
    person: Person = context.user_data.get("person")
    context.user_data.pop("amount", None)
    context.user_data.pop("description", None)
    if flow == "debt" and person:
        await query.edit_message_text(
            get_text("enter_debt_amount", language).format(name=person.name)
        )
        return ENTER_DEBT_AMOUNT
    if flow == "payment" and person:
        await query.edit_message_text(
            get_text("enter_payment_amount", language).format(name=person.name)
        )
        return ENTER_PAYMENT_AMOUNT
    await query.edit_message_text(get_text("action_cancelled", language))
    clear_workflow(context)
    return ConversationHandler.END


async def finalize_debt(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    language = await get_language(context, update.effective_user.id)
    db: Database = context.bot_data["db"]
    person: Person = context.user_data["person"]
    amount = context.user_data["amount"]
    description = context.user_data.get("description", "")
    await db.add_transaction(person.id, amount, description)
    balance = await db.get_balance(person.id)
    await query.edit_message_text(
        get_text("debt_recorded", language).format(
            name=person.name, amount=amount, balance=balance
        ),
        reply_markup=main_menu(language),
    )
    LOGGER.info(
        "Debt recorded for person_id=%s amount=%s description=%s", person.id, amount, description
    )
    clear_workflow(context)
    return ConversationHandler.END


# ---- Payments ----
async def start_payment(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data["flow"] = "payment"
    return await list_people_keyboard(
        update, context, SELECT_PAYMENT_PERSON, ENTER_PAYMENT_AMOUNT
    )


async def prompt_payment_amount(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    language = await get_language(context, update.effective_user.id)
    db: Database = context.bot_data["db"]
    person_id = context.user_data["selected_person"]
    person = await db.get_person(person_id)
    context.user_data["person"] = person
    context.user_data["selection_mode"] = False
    message_text = get_text("enter_payment_amount", language).format(name=person.name)
    if update.callback_query:
        await update.callback_query.edit_message_text(message_text)
    else:
        await update.message.reply_text(message_text)
    return ENTER_PAYMENT_AMOUNT


async def receive_payment_amount(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    language = await get_language(context, update.effective_user.id)
    try:
        amount = float(update.message.text)
        if amount <= 0:
            raise ValueError
    except ValueError:
        await update.message.reply_text(get_text("invalid_number", language))
        return ENTER_PAYMENT_AMOUNT
    context.user_data["amount"] = amount
    context.user_data.pop("description", None)
    await update.message.reply_text(get_text("enter_payment_description", language))
    return ENTER_PAYMENT_DESCRIPTION


async def receive_payment_description(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> int:
    context.user_data["description"] = update.message.text.strip()
    return await confirm_payment(update, context)


async def confirm_payment(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    language = await get_language(context, update.effective_user.id)
    person: Person = context.user_data["person"]
    amount = context.user_data["amount"]
    description = context.user_data.get("description", "")
    if description:
        text = get_text("confirm_payment_with_description", language).format(
            name=person.name, amount=amount, description=description
        )
    else:
        text = get_text("confirm_payment", language).format(name=person.name, amount=amount)
    keyboard = confirmation_keyboard(language, "payment")
    await update.message.reply_text(text, reply_markup=keyboard)
    return CONFIRM_PAYMENT


async def finalize_payment(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    language = await get_language(context, update.effective_user.id)
    db: Database = context.bot_data["db"]
    person: Person = context.user_data["person"]
    amount = -abs(context.user_data["amount"])
    await db.add_transaction(person.id, amount, context.user_data.get("description", ""))
    balance = await db.get_balance(person.id)
    await query.edit_message_text(
        get_text("payment_recorded", language).format(name=person.name, balance=balance),
        reply_markup=main_menu(language),
    )
    LOGGER.info("Payment recorded for person_id=%s amount=%s", person.id, amount)
    clear_workflow(context)
    return ConversationHandler.END


# ---- History ----
async def start_history(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data["flow"] = "history"
    return await list_people_keyboard(
        update, context, SELECT_HISTORY_PERSON, ENTER_HISTORY_DATES
    )


async def prompt_history_dates(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    language = await get_language(context, update.effective_user.id)
    db: Database = context.bot_data["db"]
    person_id = context.user_data["selected_person"]
    person = await db.get_person(person_id)
    context.user_data["person"] = person
    context.user_data["selection_mode"] = False
    message_text = get_text("prompt_date_range", language)
    if update.callback_query:
        await update.callback_query.answer()
        await update.callback_query.edit_message_text(message_text)
    else:
        await update.message.reply_text(message_text)
    return ENTER_HISTORY_DATES


async def fetch_history(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    language = await get_language(context, update.effective_user.id)
    db: Database = context.bot_data["db"]
    person: Person = context.user_data["person"]

    text = update.message.text.strip()
    start_date: Optional[datetime] = None
    end_date: Optional[datetime] = None
    if text.lower() != "/skip":
        try:
            start_str, end_str = [part.strip() for part in text.split(",", 1)]
            start_date = datetime.fromisoformat(start_str)
            end_date = datetime.fromisoformat(end_str)
        except ValueError:
            await update.message.reply_text(get_text("invalid_date_range", language))
            return ENTER_HISTORY_DATES

    history = await db.get_history(person.id, start_date, end_date)
    if not history:
        await update.message.reply_text(get_text("history_empty", language))
        clear_workflow(context)
        return ConversationHandler.END

    lines = [
        get_text("history_header", language).format(name=escape(person.name))
    ]
    for item in history:
        template = "history_item_payment" if item.is_payment else "history_item_debt"
        description = escape(item.description) if item.description else "-"
        lines.append(
            get_text(template, language).format(
                amount=abs(item.amount),
                description=description,
                date=item.created_at.strftime("%Y-%m-%d %H:%M"),
            )
        )
    await update.message.reply_text(
        "\n".join(lines),
        reply_markup=main_menu(language),
        parse_mode=constants.ParseMode.HTML,
    )
    clear_workflow(context)
    return ConversationHandler.END


# ---- Search ----
async def start_search(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    language = await get_language(context, update.effective_user.id)
    context.user_data["selection_mode"] = False
    prompt = "\n".join(
        [get_text("search_prompt", language), get_text("search_filters_hint", language)]
    )
    await update.message.reply_text(prompt)
    return SEARCH_QUERY


# ---- Language ----
async def start_language(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    language = await get_language(context, update.effective_user.id)
    await update.message.reply_text(
        get_text("language_prompt", language), reply_markup=language_keyboard()
    )
    return SEARCH_QUERY


async def change_language(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    language_code = query.data.split(":", 1)[1]
    db: Database = context.bot_data["db"]
    await db.set_user_language(update.effective_user.id, language_code)
    context.user_data["language"] = language_code
    language = await get_language(context, update.effective_user.id)
    await query.edit_message_text(
        get_text("language_updated", language).format(language=language)
    )
    clear_workflow(context)
    return ConversationHandler.END


async def unknown(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    language = await get_language(context, update.effective_user.id)
    await update.message.reply_text(get_text("start_message", language))


def build_application(config) -> Application:
    application = (
        ApplicationBuilder()
        .token(config.token)
        .rate_limiter(AIORateLimiter())
        .build()
    )
    return application


def register_handlers(application: Application) -> None:
    # Commands
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("menu", show_main_menu))
    application.add_handler(CommandHandler("dashboard", show_dashboard))
    application.add_handler(
        MessageHandler(filters.Regex(r"^(Dashboard|داشبورد)$"), show_dashboard)
    )

    # Add person conversation
    add_person_conv = ConversationHandler(
        entry_points=[
            MessageHandler(filters.Regex(r"^(Add Person|افزودن شخص)$"), prompt_person_name)
        ],
        states={
            ADD_PERSON_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, save_person_name)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
        name="add_person",
        persistent=False,
    )
    application.add_handler(add_person_conv)

    # Add debt conversation
    add_debt_conv = ConversationHandler(
        entry_points=[
            MessageHandler(filters.Regex(r"^(Add Debt|ثبت بدهی)$"), start_add_debt),
        ],
        states={
            SELECT_DEBT_PERSON: [
                CallbackQueryHandler(select_person_callback),
                MessageHandler(filters.TEXT & ~filters.COMMAND, handle_person_text),
            ],
            ENTER_DEBT_AMOUNT: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, receive_debt_amount),
            ],
            ENTER_DEBT_DESCRIPTION: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, receive_debt_description),
                CommandHandler("skip", skip_description),
            ],
            CONFIRM_DEBT: [
                CallbackQueryHandler(finalize_debt, pattern=r"^confirm:debt$"),
                CallbackQueryHandler(handle_back, pattern=r"^back$"),
            ],
        },
        fallbacks=[CommandHandler("cancel", cancel), CallbackQueryHandler(cancel, pattern="cancel")],
        name="add_debt",
    )
    application.add_handler(add_debt_conv)

    # Payment conversation
    payment_conv = ConversationHandler(
        entry_points=[
            MessageHandler(filters.Regex(r"^(Pay Debt|پرداخت بدهی)$"), start_payment),
        ],
        states={
            SELECT_PAYMENT_PERSON: [
                CallbackQueryHandler(select_person_callback),
                MessageHandler(filters.TEXT & ~filters.COMMAND, handle_person_text),
            ],
            ENTER_PAYMENT_AMOUNT: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, receive_payment_amount),
            ],
            ENTER_PAYMENT_DESCRIPTION: [
                MessageHandler(
                    filters.TEXT & ~filters.COMMAND, receive_payment_description
                ),
                CommandHandler("skip", skip_description),
            ],
            CONFIRM_PAYMENT: [
                CallbackQueryHandler(finalize_payment, pattern=r"^confirm:payment$"),
                CallbackQueryHandler(handle_back, pattern=r"^back$"),
            ],
        },
        fallbacks=[CommandHandler("cancel", cancel), CallbackQueryHandler(cancel, pattern="cancel")],
        name="payment",
    )
    application.add_handler(payment_conv)

    # History conversation
    history_conv = ConversationHandler(
        entry_points=[
            MessageHandler(filters.Regex(r"^(History|تاریخچه)$"), start_history),
        ],
        states={
            SELECT_HISTORY_PERSON: [
                CallbackQueryHandler(select_person_callback),
                MessageHandler(filters.TEXT & ~filters.COMMAND, handle_person_text),
            ],
            ENTER_HISTORY_DATES: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, fetch_history),
            ],
        },
        fallbacks=[CommandHandler("cancel", cancel), CallbackQueryHandler(cancel, pattern="cancel")],
        name="history",
    )
    application.add_handler(history_conv)

    # Search entry
    application.add_handler(
        ConversationHandler(
            entry_points=[
                MessageHandler(filters.Regex(r"^(Search|جستجو)$"), start_search),
                CommandHandler("search", start_search),
            ],
            states={
                SEARCH_QUERY: [MessageHandler(filters.TEXT & ~filters.COMMAND, search_people)],
            },
            fallbacks=[CommandHandler("cancel", cancel)],
            name="search",
        )
    )

    # Language selection
    application.add_handler(
        ConversationHandler(
            entry_points=[
                MessageHandler(filters.Regex(r"^(Language|زبان)$"), start_language),
                CommandHandler("language", start_language),
            ],
            states={
                SEARCH_QUERY: [CallbackQueryHandler(change_language, pattern=r"^lang:")],
            },
            fallbacks=[CommandHandler("cancel", cancel)],
            name="language",
        )
    )

    application.add_handler(MessageHandler(filters.COMMAND, unknown))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, unknown))


async def main() -> None:
    config = load_config()
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        handlers=[logging.FileHandler(config.log_file), logging.StreamHandler()],
    )
    db = Database(config.database_path)
    await db.initialize()
    application = build_application(config)
    application.bot_data["db"] = db
    register_handlers(application)
    await application.initialize()
    await application.start()
    LOGGER.info("Bot started")
    await application.updater.start_polling()
    try:
        await application.updater.wait()
    finally:
        await application.stop()
        await application.shutdown()


if __name__ == "__main__":
    asyncio.run(main())
