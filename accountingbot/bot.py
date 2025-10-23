"""Telegram bot entry point for AccountingBot."""
from __future__ import annotations

import asyncio
import csv
import logging
import re
import signal
from datetime import datetime
from html import escape
from io import BytesIO, StringIO
from typing import Any, Iterable, Optional

from telegram import Update, constants
from telegram.ext import (
    AIORateLimiter,
    Application,
    ApplicationBuilder,
    BaseHandler,
    CallbackQueryHandler,
    CommandHandler,
    ConversationHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

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
    cancel_keyboard,
    language_keyboard,
    main_menu_keyboard,
    person_menu_keyboard,
    search_results_keyboard,
    selection_method_keyboard,
)
from .localization import available_languages, get_text

# Patched ConversationHandler support for per-message tracking with message updates
if not hasattr(ConversationHandler, "_accountingbot_per_message_patch"):
    _original_get_key = ConversationHandler._get_key

    def _accountingbot_get_key(self: ConversationHandler, update: Update):
        if self.per_message and update.callback_query is None and update.message:
            chat = update.effective_chat
            user = update.effective_user
            key = []
            if self.per_chat:
                if chat is None:
                    raise RuntimeError("Can't build key for update without effective chat!")
                key.append(chat.id)
            if self.per_user:
                if user is None:
                    raise RuntimeError("Can't build key for update without effective user!")
                key.append(user.id)
            key.append(update.message.message_id)
            return tuple(key)
        return _original_get_key(self, update)

    ConversationHandler._get_key = _accountingbot_get_key  # type: ignore[assignment]
    ConversationHandler._accountingbot_per_message_patch = True


class _CallbackHandlerWrapper(CallbackQueryHandler):
    __slots__ = ("_inner",)

    def __init__(self, handler: BaseHandler[Update, ContextTypes.DEFAULT_TYPE]):
        self._inner = handler
        super().__init__(handler.callback, block=handler.block)

    def check_update(self, update: object):
        return self._inner.check_update(update)

    async def handle_update(
        self,
        update: Update,
        application: Application,
        check_result: object,
        context: ContextTypes.DEFAULT_TYPE,
    ) -> Any:
        return await self._inner.handle_update(update, application, check_result, context)

    def collect_additional_context(
        self,
        context: ContextTypes.DEFAULT_TYPE,
        update: Update,
        application: Application,
        check_result: object,
    ) -> None:
        self._inner.collect_additional_context(context, update, application, check_result)

    def __getattr__(self, item: str) -> Any:
        return getattr(self._inner, item)


def _wrap_handlers(handlers: Iterable[BaseHandler[Update, ContextTypes.DEFAULT_TYPE]]):
    wrapped = []
    for handler in handlers:
        if isinstance(handler, CallbackQueryHandler):
            wrapped.append(handler)
        elif isinstance(handler, _CallbackHandlerWrapper):
            wrapped.append(handler)
        else:
            wrapped.append(_CallbackHandlerWrapper(handler))
    return wrapped


# Conversation states
ADD_PERSON_NAME = 1
DEBT_ENTRY = 10
PAYMENT_ENTRY = 20
HISTORY_PERSON, HISTORY_DATES = range(30, 32)
SEARCH_QUERY = 40
LANGUAGE_SELECTION = 50

LOGGER = logging.getLogger(__name__)

PERSON_MENU_PAGE_SIZE = 5

MAIN_MENU_ACTIONS = (
    "add_person",
    "add_debt",
    "pay_debt",
    "history",
    "dashboard",
    "list_people",
    "language",
    "export",
)

MENU_CALLBACK_FALLBACK_PATTERN = re.compile(
    rf"^menu:(?!(?:{'|'.join(MAIN_MENU_ACTIONS)})$).+$"
)


async def get_language(context: ContextTypes.DEFAULT_TYPE, user_id: int) -> str:
    language = context.user_data.get("language")
    if language:
        return language
    db: Database = context.bot_data["db"]
    language = await db.get_user_language(user_id)
    context.user_data["language"] = language
    return language


def get_reply_target(update: Update):
    if update.message:
        return update.message
    if update.callback_query:
        return update.callback_query.message
    raise ValueError("No reply target available")


async def answer_callback(update: Update) -> None:
    if update.callback_query:
        await update.callback_query.answer()


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


def with_cancel_hint(message: str, language: str) -> str:
    cancel_hint = get_text("cancel_anytime", language)
    if cancel_hint in message:
        return message
    if message.endswith("\n"):
        return f"{message}{cancel_hint}"
    return f"{message}\n\n{cancel_hint}"


async def show_people_list(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    language = await get_language(context, update.effective_user.id)
    if update.callback_query:
        await update.callback_query.answer()
    target = get_reply_target(update)
    db: Database = context.bot_data["db"]
    people = await db.list_people()
    if not people:
        await target.reply_text(get_text("no_people", language))
        clear_workflow(context)
        await send_main_menu_reply(update, context, language)
        return

    header = get_text("people_list_header", language).format(count=len(people))
    lines = [header]
    lines.extend(f"• {person.name} (#{person.id})" for person in people)

    max_length = 3500
    chunk = ""
    for line in lines:
        candidate = f"{chunk}\n{line}" if chunk else line
        if len(candidate) > max_length and chunk:
            await target.reply_text(chunk)
            chunk = line
        else:
            chunk = candidate

    if chunk:
        await target.reply_text(chunk)

    clear_workflow(context)
    await send_main_menu_reply(update, context, language)


def compose_start_message(language: str) -> str:
    lines = [get_text("start_message", language), ""]
    lines.append(get_text("start_command_overview", language))
    lines.append("")
    actions = [
        get_text("add_person", language),
        get_text("add_debt", language),
        get_text("pay_debt", language),
        get_text("history", language),
        get_text("dashboard", language),
        get_text("list_people", language),
        get_text("export_transactions", language),
        get_text("language", language),
    ]
    lines.extend(f"• {action}" for action in actions)
    lines.append("")
    lines.append(get_text("start_search_hint", language))
    lines.append(get_text("start_cancel_hint", language))
    return "\n".join(lines)


async def send_start_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    language = await get_language(context, update.effective_user.id)
    message = compose_start_message(language)
    if update.message:
        await update.message.reply_text(
            message,
            disable_web_page_preview=True,
            reply_markup=main_menu_keyboard(language),
        )
    elif update.callback_query:
        await update.callback_query.answer()
        await update.callback_query.message.edit_text(
            message,
            disable_web_page_preview=True,
            reply_markup=main_menu_keyboard(language),
        )


async def send_main_menu_reply(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    language: Optional[str] = None,
) -> None:
    if language is None:
        language = await get_language(context, update.effective_user.id)
    message = compose_start_message(language)
    target = get_reply_target(update)
    await target.reply_text(
        message,
        disable_web_page_preview=True,
        reply_markup=main_menu_keyboard(language),
    )


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    clear_workflow(context)
    await send_start_message(update, context)


async def show_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await send_start_message(update, context)


async def show_dashboard(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    language = await get_language(context, update.effective_user.id)
    db: Database = context.bot_data["db"]
    summary = await db.get_dashboard_summary()
    text = format_dashboard(summary, language)
    if update.callback_query:
        await update.callback_query.answer()
    target = get_reply_target(update)
    await target.reply_text(
        text,
        disable_web_page_preview=True,
    )
    clear_workflow(context)
    await send_main_menu_reply(update, context, language)


async def export_transactions_handler(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    language = await get_language(context, update.effective_user.id)
    await answer_callback(update)
    target = get_reply_target(update)
    db: Database = context.bot_data["db"]

    try:
        rows = await db.export_transactions()
        buffer = StringIO()
        writer = csv.writer(buffer)
        writer.writerow(["id", "person_id", "amount", "description", "created_at"])
        for row in rows:
            writer.writerow(
                [
                    row["id"],
                    row["person_id"],
                    row["amount"],
                    row["description"],
                    row["created_at"],
                ]
            )
    except Exception:  # pragma: no cover - defensive logging
        LOGGER.exception("Failed to export transactions")
        await target.reply_text(get_text("export_error", language))
        clear_workflow(context)
        await send_main_menu_reply(update, context, language)
        return

    buffer.seek(0)
    document = BytesIO(buffer.getvalue().encode("utf-8"))
    document.name = f"transactions-{datetime.utcnow().strftime('%Y%m%d-%H%M%S')}.csv"

    await target.reply_document(
        document,
        caption=get_text("export_success", language),
    )
    clear_workflow(context)
    await send_main_menu_reply(update, context, language)


def _has_active_workflow(context: ContextTypes.DEFAULT_TYPE) -> bool:
    """Return ``True`` when the user currently has an active workflow."""

    for key in ("flow", "person_state", "person_next_state", "entry_mode"):
        if context.user_data.get(key) is not None:
            return True
    return False


def clear_workflow(context: ContextTypes.DEFAULT_TYPE) -> None:
    for key in (
        "flow",
        "person",
        "amount",
        "description",
        "person_state",
        "person_next_state",
        "entry_mode",
        "person_menu_page",
    ):
        context.user_data.pop(key, None)


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    language = await get_language(context, update.effective_user.id)
    clear_workflow(context)
    if update.message:
        await update.message.reply_text(get_text("action_cancelled", language))
    elif update.callback_query:
        await update.callback_query.answer(get_text("action_cancelled", language))
        await update.callback_query.message.edit_text(
            get_text("action_cancelled", language),
            reply_markup=None,
        )
    await send_main_menu_reply(update, context, language)
    return ConversationHandler.END


async def prompt_person_name(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    language = await get_language(context, update.effective_user.id)
    clear_workflow(context)
    await answer_callback(update)
    target = get_reply_target(update)
    await target.reply_text(
        with_cancel_hint(get_text("enter_person_name", language), language),
        reply_markup=cancel_keyboard(language),
    )
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
    )
    clear_workflow(context)
    await send_main_menu_reply(update, context, language)
    return ConversationHandler.END


async def prompt_person_selection(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> int:
    language = await get_language(context, update.effective_user.id)
    context.user_data.pop("entry_mode", None)
    await answer_callback(update)
    target = get_reply_target(update)
    await target.reply_text(
        with_cancel_hint(get_text("choose_selection_method", language), language),
        reply_markup=selection_method_keyboard(language),
    )
    return context.user_data.get("person_state", ConversationHandler.END)


async def advance_person_workflow(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    person: Person,
    language: str,
) -> int:
    context.user_data["person"] = person
    target = get_reply_target(update)
    next_state = context.user_data.get("person_next_state", ConversationHandler.END)
    flow = context.user_data.get("flow")
    entry_mode = context.user_data.get("entry_mode")

    if flow == "debt" and entry_mode == "menu":
        await target.reply_text(
            with_cancel_hint(
                get_text("menu_enter_debt_details", language).format(name=person.name),
                language,
            ),
            reply_markup=cancel_keyboard(language),
        )
        return DEBT_ENTRY

    if flow == "payment" and entry_mode == "menu":
        await target.reply_text(
            with_cancel_hint(
                get_text("menu_enter_payment_details", language).format(name=person.name),
                language,
            ),
            reply_markup=cancel_keyboard(language),
        )
        return PAYMENT_ENTRY

    if next_state == HISTORY_DATES:
        await target.reply_text(
            with_cancel_hint(get_text("prompt_date_range", language), language)
        )
        return HISTORY_DATES

    if context.user_data.get("person_state") == SEARCH_QUERY:
        return SEARCH_QUERY
    return ConversationHandler.END


async def show_person_menu(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    language: str,
    page: int = 0,
) -> int:
    db: Database = context.bot_data["db"]
    people = await db.list_people_with_usage()
    if not people:
        target = get_reply_target(update)
        await target.reply_text(
            with_cancel_hint(get_text("no_people", language), language),
            reply_markup=cancel_keyboard(language),
        )
        context.user_data.pop("entry_mode", None)
        return context.user_data.get("person_state", ConversationHandler.END)

    total = len(people)
    total_pages = max(1, (total + PERSON_MENU_PAGE_SIZE - 1) // PERSON_MENU_PAGE_SIZE)
    page = max(0, min(page, total_pages - 1))
    start = page * PERSON_MENU_PAGE_SIZE
    end = start + PERSON_MENU_PAGE_SIZE
    current_slice = people[start:end]

    message = with_cancel_hint(
        get_text("menu_prompt", language).format(page=f"{page + 1}/{total_pages}"),
        language,
    )
    keyboard = person_menu_keyboard(current_slice, language, page, total_pages)

    query = update.callback_query
    if query and query.message:
        await query.message.edit_text(message, reply_markup=keyboard)
    else:
        target = get_reply_target(update)
        await target.reply_text(message, reply_markup=keyboard)

    context.user_data["person_menu_page"] = page
    return context.user_data.get("person_state", ConversationHandler.END)


async def handle_selection_method(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> int:
    query = update.callback_query
    if not query or not query.data:
        return context.user_data.get("person_state", ConversationHandler.END)

    language = await get_language(context, update.effective_user.id)
    payload = query.data.split(":", 1)
    state = context.user_data.get("person_state", ConversationHandler.END)
    if len(payload) != 2:
        await query.answer()
        return state
    method = payload[1]
    flow = context.user_data.get("flow")

    if method == "id":
        context.user_data["entry_mode"] = "id"
        context.user_data.pop("person", None)
        context.user_data.pop("person_menu_page", None)
        if flow == "debt":
            message = "\n".join(
                [
                    get_text("quick_debt_prompt", language),
                    get_text("quick_debt_example", language),
                ]
            )
        elif flow == "payment":
            message = "\n".join(
                [
                    get_text("quick_payment_prompt", language),
                    get_text("quick_payment_example", language),
                ]
            )
        else:
            message = get_text("prompt_person_id", language)
        await query.answer()
        if query.message:
            await query.message.edit_text(
                with_cancel_hint(message, language),
                reply_markup=cancel_keyboard(language),
            )
        return state

    if method == "menu":
        context.user_data["entry_mode"] = "menu"
        context.user_data.pop("person", None)
        context.user_data.pop("person_menu_page", None)
        await query.answer()
        return await show_person_menu(update, context, language, page=0)

    await query.answer()
    return state


async def handle_person_menu_navigation(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> int:
    query = update.callback_query
    if not query or not query.data:
        return context.user_data.get("person_state", ConversationHandler.END)

    language = await get_language(context, update.effective_user.id)
    try:
        _, page_str = query.data.split(":", 1)
        page = int(page_str)
    except (ValueError, IndexError):
        await query.answer()
        return context.user_data.get("person_state", ConversationHandler.END)

    await query.answer()
    return await show_person_menu(update, context, language, page)


async def receive_person_reference(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> int:
    language = await get_language(context, update.effective_user.id)
    db: Database = context.bot_data["db"]
    text = update.message.text.strip()
    state = context.user_data.get("person_state", ConversationHandler.END)
    if not text:
        await update.message.reply_text(
            with_cancel_hint(get_text("person_id_or_menu_hint", language), language),
            reply_markup=cancel_keyboard(language),
        )
        return state

    normalized = text.lstrip("#")
    person: Optional[Person] = None
    if normalized.isdigit():
        person = await db.get_person(int(normalized))
        if not person:
            await update.message.reply_text(get_text("not_found", language))
            await update.message.reply_text(
                with_cancel_hint(get_text("person_id_or_menu_hint", language), language),
                reply_markup=cancel_keyboard(language),
            )
            return state
    else:
        await update.message.reply_text(
            with_cancel_hint(get_text("person_id_or_menu_hint", language), language),
            reply_markup=cancel_keyboard(language),
        )
        return state

    return await advance_person_workflow(update, context, person, language)


async def _handle_person_selection_failure(
    update: Update, context: ContextTypes.DEFAULT_TYPE, language: str
) -> int:
    target = get_reply_target(update)
    await target.reply_text(get_text("not_found", language))
    state = context.user_data.get("person_state", ConversationHandler.END)
    if state == SEARCH_QUERY:
        await target.reply_text(
            with_cancel_hint(get_text("search_filters_hint", language), language),
            reply_markup=cancel_keyboard(language),
        )
        return SEARCH_QUERY
    if state != ConversationHandler.END:
        await target.reply_text(
            with_cancel_hint(get_text("person_id_or_menu_hint", language), language),
            reply_markup=cancel_keyboard(language),
        )
    return state


async def handle_person_selection(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> int:
    query = update.callback_query
    if not query or not query.data:
        return context.user_data.get("person_state", ConversationHandler.END)

    language = await get_language(context, update.effective_user.id)
    await query.answer()
    if query.message:
        await query.message.edit_reply_markup(reply_markup=None)

    payload = query.data.split(":", 1)
    if len(payload) != 2 or not payload[1].isdigit():
        return await _handle_person_selection_failure(update, context, language)

    person_id = int(payload[1])
    db: Database = context.bot_data["db"]
    person = await db.get_person(person_id)
    if not person:
        return await _handle_person_selection_failure(update, context, language)

    return await advance_person_workflow(update, context, person, language)


# ---- Add Debt ----
async def start_add_debt(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    clear_workflow(context)
    context.user_data["flow"] = "debt"
    context.user_data["person_state"] = DEBT_ENTRY
    return await prompt_person_selection(update, context)


async def receive_debt_entry(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    language = await get_language(context, update.effective_user.id)
    text = update.message.text.strip()
    entry_mode = context.user_data.get("entry_mode")
    selected_person: Optional[Person] = context.user_data.get("person")

    if entry_mode == "menu" and selected_person:
        parts = text.split(None, 1)
        if not parts:
            await update.message.reply_text(
                with_cancel_hint(
                    get_text("menu_enter_debt_details", language).format(
                        name=selected_person.name
                    ),
                    language,
                ),
                reply_markup=cancel_keyboard(language),
            )
            return DEBT_ENTRY

        raw_amount = parts[0]
        try:
            amount = float(raw_amount)
            if amount <= 0:
                raise ValueError
        except ValueError:
            await update.message.reply_text(
                with_cancel_hint(get_text("quick_entry_invalid_amount", language), language)
            )
            return DEBT_ENTRY

        description = parts[1].strip() if len(parts) > 1 else ""

        db: Database = context.bot_data["db"]
        await db.add_transaction(selected_person.id, amount, description)
        balance = await db.get_balance(selected_person.id)
        await update.message.reply_text(
            get_text("debt_recorded", language).format(
                name=selected_person.name, amount=amount, balance=balance
            )
        )
        LOGGER.info(
            "Debt recorded for person_id=%s amount=%s description=%s",
            selected_person.id,
            amount,
            description,
        )
        clear_workflow(context)
        await send_main_menu_reply(update, context, language)
        return ConversationHandler.END

    parts = text.split(None, 2)
    if len(parts) < 3:
        await update.message.reply_text(
            with_cancel_hint(get_text("quick_entry_invalid_format", language), language)
        )
        return DEBT_ENTRY

    raw_id, raw_amount, description = parts[0], parts[1], parts[2].strip()
    person_id = raw_id.lstrip("#")
    if not person_id.isdigit():
        await update.message.reply_text(
            with_cancel_hint(get_text("quick_entry_invalid_id", language), language)
        )
        return DEBT_ENTRY

    try:
        amount = float(raw_amount)
        if amount <= 0:
            raise ValueError
    except ValueError:
        await update.message.reply_text(
            with_cancel_hint(get_text("quick_entry_invalid_amount", language), language)
        )
        return DEBT_ENTRY

    db: Database = context.bot_data["db"]
    person = await db.get_person(int(person_id))
    if not person:
        await update.message.reply_text(
            with_cancel_hint(get_text("quick_entry_person_not_found", language), language)
        )
        return DEBT_ENTRY

    await db.add_transaction(person.id, amount, description)
    balance = await db.get_balance(person.id)
    await update.message.reply_text(
        get_text("debt_recorded", language).format(
            name=person.name, amount=amount, balance=balance
        )
    )
    LOGGER.info(
        "Debt recorded for person_id=%s amount=%s description=%s",
        person.id,
        amount,
        description,
    )
    clear_workflow(context)
    await send_main_menu_reply(update, context, language)
    return ConversationHandler.END


# ---- Payments ----
async def start_payment(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    clear_workflow(context)
    context.user_data["flow"] = "payment"
    context.user_data["person_state"] = PAYMENT_ENTRY
    return await prompt_person_selection(update, context)


async def receive_payment_entry(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    language = await get_language(context, update.effective_user.id)
    text = update.message.text.strip()
    entry_mode = context.user_data.get("entry_mode")
    selected_person: Optional[Person] = context.user_data.get("person")

    if entry_mode == "menu" and selected_person:
        parts = text.split(None, 1)
        if not parts:
            await update.message.reply_text(
                with_cancel_hint(
                    get_text("menu_enter_payment_details", language).format(
                        name=selected_person.name
                    ),
                    language,
                ),
                reply_markup=cancel_keyboard(language),
            )
            return PAYMENT_ENTRY

        raw_amount = parts[0]
        try:
            amount = float(raw_amount)
            if amount <= 0:
                raise ValueError
        except ValueError:
            await update.message.reply_text(
                with_cancel_hint(get_text("quick_entry_invalid_amount", language), language)
            )
            return PAYMENT_ENTRY

        description = parts[1].strip() if len(parts) > 1 else ""

        db: Database = context.bot_data["db"]
        stored_amount = -abs(amount)
        await db.add_transaction(selected_person.id, stored_amount, description)
        balance = await db.get_balance(selected_person.id)
        await update.message.reply_text(
            get_text("payment_recorded", language).format(
                name=selected_person.name, balance=balance
            )
        )
        LOGGER.info(
            "Payment recorded for person_id=%s amount=%s description=%s",
            selected_person.id,
            stored_amount,
            description,
        )
        clear_workflow(context)
        await send_main_menu_reply(update, context, language)
        return ConversationHandler.END

    parts = text.split(None, 2)
    if len(parts) < 3:
        await update.message.reply_text(
            with_cancel_hint(get_text("quick_entry_invalid_format", language), language)
        )
        return PAYMENT_ENTRY

    raw_id, raw_amount, description = parts[0], parts[1], parts[2].strip()
    person_id = raw_id.lstrip("#")
    if not person_id.isdigit():
        await update.message.reply_text(
            with_cancel_hint(get_text("quick_entry_invalid_id", language), language)
        )
        return PAYMENT_ENTRY

    try:
        amount = float(raw_amount)
        if amount <= 0:
            raise ValueError
    except ValueError:
        await update.message.reply_text(
            with_cancel_hint(get_text("quick_entry_invalid_amount", language), language)
        )
        return PAYMENT_ENTRY

    db: Database = context.bot_data["db"]
    person = await db.get_person(int(person_id))
    if not person:
        await update.message.reply_text(
            with_cancel_hint(get_text("quick_entry_person_not_found", language), language)
        )
        return PAYMENT_ENTRY

    stored_amount = -abs(amount)
    await db.add_transaction(person.id, stored_amount, description)
    balance = await db.get_balance(person.id)
    await update.message.reply_text(
        get_text("payment_recorded", language).format(name=person.name, balance=balance)
    )
    LOGGER.info(
        "Payment recorded for person_id=%s amount=%s description=%s",
        person.id,
        stored_amount,
        description,
    )
    clear_workflow(context)
    await send_main_menu_reply(update, context, language)
    return ConversationHandler.END


# ---- History ----
async def start_history(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    clear_workflow(context)
    context.user_data["flow"] = "history"
    context.user_data["person_state"] = HISTORY_PERSON
    context.user_data["person_next_state"] = HISTORY_DATES
    return await prompt_person_selection(update, context)


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
            await update.message.reply_text(
                with_cancel_hint(get_text("invalid_date_range", language), language),
                reply_markup=cancel_keyboard(language),
            )
            return HISTORY_DATES

    history = await db.get_history(
        person.id, start_date=start_date, end_date=end_date
    )
    if not history:
        await update.message.reply_text(get_text("history_empty", language))
        clear_workflow(context)
        await send_main_menu_reply(update, context, language)
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
        parse_mode=constants.ParseMode.HTML,
        reply_markup=cancel_keyboard(language),
    )
    clear_workflow(context)
    await send_main_menu_reply(update, context, language)
    return ConversationHandler.END


# ---- Search ----
async def start_search(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    language = await get_language(context, update.effective_user.id)
    has_pending_workflow = context.user_data.get("person_next_state") is not None
    if not has_pending_workflow:
        context.user_data["person_state"] = SEARCH_QUERY
    await answer_callback(update)
    target = get_reply_target(update)
    prompt = "\n".join(
        [get_text("search_prompt", language), get_text("search_filters_hint", language)]
    )
    await target.reply_text(
        with_cancel_hint(prompt, language),
        reply_markup=cancel_keyboard(language),
    )
    return SEARCH_QUERY


async def search_people(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    language = await get_language(context, update.effective_user.id)
    db: Database = context.bot_data["db"]
    text = update.message.text.strip()
    response = await db.search_people(text)
    if not response.matches:
        message = get_text("not_found", language)
        if response.suggestions:
            message = get_text("search_suggestions", language).format(
                suggestions=", ".join(response.suggestions)
            )
        await update.message.reply_text(message)
        await update.message.reply_text(
            with_cancel_hint(get_text("search_filters_hint", language), language),
            reply_markup=cancel_keyboard(language),
        )
        return SEARCH_QUERY

    formatted = format_search_results(language, response)
    keyboard = search_results_keyboard(response.matches)
    await update.message.reply_text(formatted, reply_markup=keyboard)
    await update.message.reply_text(
        with_cancel_hint(get_text("search_filters_hint", language), language),
        reply_markup=cancel_keyboard(language),
    )
    return SEARCH_QUERY


# ---- Language ----
async def start_language(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    language = await get_language(context, update.effective_user.id)
    await answer_callback(update)
    target = get_reply_target(update)
    await target.reply_text(
        with_cancel_hint(get_text("language_prompt", language), language),
        reply_markup=language_keyboard(language),
    )
    return LANGUAGE_SELECTION


async def change_language(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    languages = available_languages()
    matched_code: Optional[str] = None
    if update.callback_query:
        query = update.callback_query
        await query.answer()
        payload = query.data.split(":", 1)[-1]
        if payload in languages:
            matched_code = payload
        target = query.message
        if matched_code:
            await query.message.edit_reply_markup(reply_markup=None)
    else:
        requested = update.message.text.strip().casefold()
        for code, label in languages.items():
            if requested == code.casefold() or requested == label.casefold():
                matched_code = code
                break
        target = update.message

    if not matched_code:
        language = await get_language(context, update.effective_user.id)
        await target.reply_text(
            with_cancel_hint(get_text("language_prompt_codes", language), language),
            reply_markup=language_keyboard(language),
        )
        return LANGUAGE_SELECTION

    db: Database = context.bot_data["db"]
    await db.set_user_language(update.effective_user.id, matched_code)
    context.user_data["language"] = matched_code
    label = languages[matched_code]
    await target.reply_text(
        get_text("language_updated", matched_code).format(language=label),
    )
    clear_workflow(context)
    await send_main_menu_reply(update, context, matched_code)
    return ConversationHandler.END


async def unknown(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if _has_active_workflow(context):
        # Let the active conversation continue without interrupting the user.
        return
    await send_start_message(update, context)


def build_application(config) -> Application:
    builder = ApplicationBuilder().token(config.token)
    try:
        rate_limiter = AIORateLimiter()
    except RuntimeError as exc:  # pragma: no cover - depends on optional extras
        LOGGER.warning("Rate limiter disabled: %s", exc)
    else:
        builder = builder.rate_limiter(rate_limiter)
    application = builder.build()
    return application


def register_handlers(application: Application) -> None:
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", show_help))
    application.add_handler(CommandHandler("dashboard", show_dashboard))
    application.add_handler(CommandHandler("export", export_transactions_handler))
    application.add_handler(CommandHandler("people", show_people_list))
    application.add_handler(CallbackQueryHandler(show_dashboard, pattern="^menu:dashboard$"))
    application.add_handler(CallbackQueryHandler(export_transactions_handler, pattern="^menu:export$"))
    application.add_handler(CallbackQueryHandler(show_people_list, pattern="^menu:list_people$"))

    add_person_conv = ConversationHandler(
        entry_points=_wrap_handlers(
            [
                CommandHandler("add_person", prompt_person_name),
                CallbackQueryHandler(prompt_person_name, pattern="^menu:add_person$"),
            ]
        ),
        states={
            ADD_PERSON_NAME: _wrap_handlers(
                [
                    MessageHandler(filters.TEXT & ~filters.COMMAND, save_person_name),
                    CallbackQueryHandler(cancel, pattern="^workflow:cancel$"),
                ]
            ),
        },
        fallbacks=_wrap_handlers(
            [
                CommandHandler("cancel", cancel),
                CallbackQueryHandler(cancel, pattern="^workflow:cancel$"),
            ]
        ),
        name="add_person",
        persistent=False,
        per_message=True,
    )
    application.add_handler(add_person_conv)

    add_debt_conv = ConversationHandler(
        entry_points=_wrap_handlers(
            [
                CommandHandler("add_debt", start_add_debt),
                CallbackQueryHandler(start_add_debt, pattern="^menu:add_debt$"),
            ]
        ),
        states={
            DEBT_ENTRY: _wrap_handlers(
                [
                    MessageHandler(filters.TEXT & ~filters.COMMAND, receive_debt_entry),
                    CallbackQueryHandler(handle_selection_method, pattern="^method:"),
                    CallbackQueryHandler(
                        handle_person_menu_navigation, pattern="^person_page:"
                    ),
                    CallbackQueryHandler(handle_person_selection, pattern="^select_person:"),
                    CallbackQueryHandler(cancel, pattern="^workflow:cancel$"),
                ]
            ),
        },
        fallbacks=_wrap_handlers(
            [
                CommandHandler("cancel", cancel),
                CallbackQueryHandler(cancel, pattern="^workflow:cancel$"),
            ]
        ),
        name="add_debt",
        per_message=True,
    )
    application.add_handler(add_debt_conv)

    payment_conv = ConversationHandler(
        entry_points=_wrap_handlers(
            [
                CommandHandler("record_payment", start_payment),
                CallbackQueryHandler(start_payment, pattern="^menu:pay_debt$"),
            ]
        ),
        states={
            PAYMENT_ENTRY: _wrap_handlers(
                [
                    MessageHandler(filters.TEXT & ~filters.COMMAND, receive_payment_entry),
                    CallbackQueryHandler(handle_selection_method, pattern="^method:"),
                    CallbackQueryHandler(
                        handle_person_menu_navigation, pattern="^person_page:"
                    ),
                    CallbackQueryHandler(handle_person_selection, pattern="^select_person:"),
                    CallbackQueryHandler(cancel, pattern="^workflow:cancel$"),
                ]
            ),
        },
        fallbacks=_wrap_handlers(
            [
                CommandHandler("cancel", cancel),
                CallbackQueryHandler(cancel, pattern="^workflow:cancel$"),
            ]
        ),
        name="payment",
        per_message=True,
    )
    application.add_handler(payment_conv)

    history_conv = ConversationHandler(
        entry_points=_wrap_handlers(
            [
                CommandHandler("history", start_history),
                CallbackQueryHandler(start_history, pattern="^menu:history$"),
            ]
        ),
        states={
            HISTORY_PERSON: _wrap_handlers(
                [
                    MessageHandler(filters.TEXT & ~filters.COMMAND, receive_person_reference),
                    CallbackQueryHandler(handle_selection_method, pattern="^method:"),
                    CallbackQueryHandler(
                        handle_person_menu_navigation, pattern="^person_page:"
                    ),
                    CallbackQueryHandler(handle_person_selection, pattern="^select_person:"),
                    CallbackQueryHandler(cancel, pattern="^workflow:cancel$"),
                ]
            ),
            HISTORY_DATES: _wrap_handlers(
                [
                    MessageHandler(filters.TEXT & ~filters.COMMAND, fetch_history),
                    CommandHandler("skip", fetch_history),
                ]
            ),
        },
        fallbacks=_wrap_handlers(
            [
                CommandHandler("cancel", cancel),
                CallbackQueryHandler(cancel, pattern="^workflow:cancel$"),
            ]
        ),
        name="history",
        per_message=True,
    )
    application.add_handler(history_conv)

    application.add_handler(
        ConversationHandler(
            entry_points=_wrap_handlers(
                [
                    CommandHandler("search", start_search),
                ]
            ),
            states={
                SEARCH_QUERY: _wrap_handlers(
                    [
                        MessageHandler(filters.TEXT & ~filters.COMMAND, search_people),
                        CallbackQueryHandler(handle_person_selection, pattern="^select_person:"),
                        CallbackQueryHandler(cancel, pattern="^workflow:cancel$"),
                    ]
                ),
            },
            fallbacks=_wrap_handlers(
                [
                    CommandHandler("cancel", cancel),
                    CallbackQueryHandler(cancel, pattern="^workflow:cancel$"),
                ]
            ),
            name="search",
            per_message=True,
        )
    )

    application.add_handler(
        ConversationHandler(
            entry_points=_wrap_handlers(
                [
                    CommandHandler("language", start_language),
                    CallbackQueryHandler(start_language, pattern="^menu:language$"),
                ]
            ),
            states={
                LANGUAGE_SELECTION: _wrap_handlers(
                    [
                        CallbackQueryHandler(change_language, pattern="^lang:"),
                        MessageHandler(filters.TEXT & ~filters.COMMAND, change_language),
                        CallbackQueryHandler(cancel, pattern="^workflow:cancel$"),
                    ]
                )
            },
            fallbacks=_wrap_handlers(
                [
                    CommandHandler("cancel", cancel),
                    CallbackQueryHandler(cancel, pattern="^workflow:cancel$"),
                ]
            ),
            name="language",
            per_message=True,
        )
    )

    application.add_handler(
        CallbackQueryHandler(
            send_start_message, pattern=MENU_CALLBACK_FALLBACK_PATTERN
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

    stop_event = asyncio.Event()

    def _signal_handler() -> None:
        stop_event.set()

    loop = asyncio.get_running_loop()
    try:
        for sig in (signal.SIGINT, signal.SIGTERM, signal.SIGABRT):
            loop.add_signal_handler(sig, _signal_handler)
    except NotImplementedError:
        LOGGER.warning("Signal handlers are not supported on this platform")

    try:
        await stop_event.wait()
    except (KeyboardInterrupt, SystemExit):
        LOGGER.info("Shutdown requested by user")
    finally:
        if application.updater.running:
            await application.updater.stop()
        await application.stop()
        await application.shutdown()


if __name__ == "__main__":
    asyncio.run(main())
