"""Telegram bot entry point for AccountingBot."""
from __future__ import annotations

import asyncio
import csv
import logging
import re
import signal
from datetime import datetime, timedelta
from html import escape
from io import BytesIO, StringIO
from typing import Any, Iterable, Optional, Sequence, Tuple

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
    PersonUsageStats,
    PersonAlreadyExistsError,
    SearchResponse,
)
from .keyboards import (
    back_to_main_menu_keyboard,
    cancel_keyboard,
    export_contact_keyboard,
    export_mode_keyboard,
    history_confirmation_keyboard,
    history_custom_day_keyboard,
    history_custom_hour_keyboard,
    history_custom_month_keyboard,
    history_custom_year_keyboard,
    history_range_keyboard,
    language_keyboard,
    main_menu_keyboard,
    person_menu_keyboard,
    search_results_keyboard,
    selection_method_keyboard,
    skip_keyboard,
)
from .localization import available_languages, get_text

# Patched ConversationHandler support for per-message tracking with message updates
_WORKFLOW_PROMPT_KEY = "_workflow_prompt_key"
_WORKFLOW_PROMPT_MESSAGE_IDS: dict[Tuple[int, ...], int] = {}
_AMOUNT_PATTERN = re.compile(r"^\d+$")


def _parse_positive_amount(text: str) -> Optional[int]:
    """Parse and validate a user-provided positive integer amount."""

    cleaned = text.strip()
    if not cleaned or not _AMOUNT_PATTERN.fullmatch(cleaned):
        return None
    try:
        value = int(cleaned)
    except ValueError:
        return None
    if value <= 0:
        return None
    return value


def _format_amount(amount: int) -> str:
    """Format an integer amount for display without cents."""

    return f"{amount}"


def _conversation_base_key(
    update: Update, per_chat: bool, per_user: bool
) -> Tuple[Any, ...]:
    chat = update.effective_chat
    user = update.effective_user
    parts: list[Any] = []
    if per_chat:
        if chat is None:
            raise RuntimeError("Can't build key for update without effective chat!")
        parts.append(chat.id)
    if per_user:
        if user is None:
            raise RuntimeError("Can't build key for update without effective user!")
        parts.append(user.id)
    return tuple(parts)


if not hasattr(ConversationHandler, "_accountingbot_per_message_patch"):
    _original_get_key = ConversationHandler._get_key

    def _accountingbot_get_key(self: ConversationHandler, update: Update):
        if self.per_message:
            base_parts = _conversation_base_key(update, self.per_chat, self.per_user)
            stored_message_id = _WORKFLOW_PROMPT_MESSAGE_IDS.get(base_parts)
            if stored_message_id is not None:
                conversation_key = (*base_parts, stored_message_id)
                if (
                    conversation_key not in self._conversations
                    and base_parts
                    and self._conversations
                ):
                    for existing_key in list(self._conversations.keys()):
                        if (
                            isinstance(existing_key, tuple)
                            and len(existing_key) == len(base_parts) + 1
                            and existing_key[:-1] == base_parts
                        ):
                            self._conversations[conversation_key] = self._conversations.pop(
                                existing_key
                            )
                            break
                return conversation_key

            if update.callback_query is None and update.message:
                key = list(base_parts)
                key.append(update.message.message_id)
                if base_parts:
                    _WORKFLOW_PROMPT_MESSAGE_IDS[base_parts] = update.message.message_id
                return tuple(key)

            if update.callback_query:
                key = list(base_parts)
                query = update.callback_query
                if query.inline_message_id:
                    key.append(query.inline_message_id)
                elif query.message:
                    key.append(query.message.message_id)
                    if base_parts:
                        _WORKFLOW_PROMPT_MESSAGE_IDS[base_parts] = query.message.message_id
                return tuple(key)

        return _original_get_key(self, update)

    ConversationHandler._get_key = _accountingbot_get_key  # type: ignore[assignment]
    ConversationHandler._accountingbot_per_message_patch = True

else:
    _WORKFLOW_PROMPT_KEY = "_workflow_prompt_key"


def _remember_prompt_message(
    update: Update, context: ContextTypes.DEFAULT_TYPE, message_id: Optional[int]
) -> None:
    if message_id is None:
        return
    base_key = _conversation_base_key(update, True, True)
    if not base_key:
        return
    _WORKFLOW_PROMPT_MESSAGE_IDS[base_key] = message_id
    context.user_data[_WORKFLOW_PROMPT_KEY] = base_key


def _drop_prompt_message(context: ContextTypes.DEFAULT_TYPE) -> None:
    base_key = context.user_data.pop(_WORKFLOW_PROMPT_KEY, None)
    if base_key is not None:
        _WORKFLOW_PROMPT_MESSAGE_IDS.pop(base_key, None)


def _reset_person_menu_context(context: ContextTypes.DEFAULT_TYPE) -> None:
    for key in (
        "person_menu_page",
        "person_menu_results",
        "person_menu_mode",
        "person_menu_search_query",
        "person_menu_search_expected",
    ):
        context.user_data.pop(key, None)


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
DEBT_AMOUNT = 11
DEBT_DESCRIPTION = 12
PAYMENT_ENTRY = 20
PAYMENT_AMOUNT = 21
PAYMENT_DESCRIPTION = 22
HISTORY_PERSON, HISTORY_DATES = range(30, 32)
SEARCH_QUERY = 40
LANGUAGE_SELECTION = 50
EXPORT_MODE, EXPORT_CONTACT_CHOICE, EXPORT_PERSON = range(60, 63)

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


def format_balance_status(balance: int, language: str) -> str:
    if balance > 0:
        return get_text("balance_debtor", language).format(
            amount=_format_amount(balance)
        )
    if balance < 0:
        return get_text("balance_creditor", language).format(
            amount=_format_amount(abs(balance))
        )
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
            f"{get_text('total_debt', language)}: {_format_amount(totals.total_debt)}",
            f"{get_text('total_payments', language)}: {_format_amount(totals.total_payments)}",
            f"{get_text('outstanding_balance', language)}: {_format_amount(totals.outstanding_balance)}",
        ]
    )

    if summary.top_debtors:
        lines.append(get_text("top_debtors", language))
        for index, debtor in enumerate(summary.top_debtors, start=1):
            person = debtor.person
            lines.append(
                f"{index}. {person.name} (#{person.id}) — {_format_amount(debtor.balance)}"
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
                    amount=_format_amount(abs(transaction.amount)),
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
        await target.reply_text(
            get_text("no_people", language),
            reply_markup=back_to_main_menu_keyboard(language),
        )
        clear_workflow(context)
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
        await target.reply_text(
            chunk,
            reply_markup=back_to_main_menu_keyboard(language),
        )

    clear_workflow(context)


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
        reply_markup=back_to_main_menu_keyboard(language),
    )
    clear_workflow(context)


async def go_back_to_main_menu(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    clear_workflow(context)
    await send_start_message(update, context)


async def start_export_transactions(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> int:
    language = await get_language(context, update.effective_user.id)
    clear_workflow(context)
    context.user_data["flow"] = "export"
    context.user_data["export_mode"] = "all"
    await answer_callback(update)
    target = get_reply_target(update)
    message = await target.reply_text(
        with_cancel_hint(get_text("export_choose_type", language), language),
        reply_markup=export_mode_keyboard(language),
    )
    _remember_prompt_message(update, context, getattr(message, "message_id", None))
    return EXPORT_MODE


async def handle_export_mode(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> int:
    query = update.callback_query
    if not query or not query.data:
        return EXPORT_MODE

    language = await get_language(context, update.effective_user.id)
    parts = query.data.split(":", 2)
    if len(parts) != 3:
        await query.answer()
        return EXPORT_MODE

    mode = parts[2]
    context.user_data["export_mode"] = mode
    await query.answer()

    prompt = get_text("export_choose_contacts", language)
    if query.message:
        await query.message.edit_text(
            with_cancel_hint(prompt, language),
            reply_markup=export_contact_keyboard(language),
        )
    return EXPORT_CONTACT_CHOICE


async def handle_export_contact_choice(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> int:
    query = update.callback_query
    if not query or not query.data:
        return EXPORT_CONTACT_CHOICE

    language = await get_language(context, update.effective_user.id)
    parts = query.data.split(":", 2)
    if len(parts) != 3:
        await query.answer()
        return EXPORT_CONTACT_CHOICE

    choice = parts[2]
    await query.answer()

    if query.message:
        await query.message.edit_reply_markup(reply_markup=None)

    if choice == "all":
        return await perform_export(update, context, language, person_ids=None)

    if choice == "choose":
        context.user_data["person_state"] = EXPORT_PERSON
        context.user_data.pop("person_next_state", None)
        context.user_data.pop("entry_mode", None)
        return await prompt_person_selection(update, context)

    return EXPORT_CONTACT_CHOICE


async def skip_export_contacts(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> int:
    language = await get_language(context, update.effective_user.id)
    return await perform_export(update, context, language, person_ids=None)


async def perform_export(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    language: str,
    *,
    person_ids: Optional[Sequence[int]],
) -> int:
    await answer_callback(update)
    db: Database = context.bot_data["db"]

    mode = context.user_data.get("export_mode", "all")
    amount_filter: Optional[str]
    if mode == "debt":
        amount_filter = "debt"
    elif mode == "payment":
        amount_filter = "payment"
    else:
        amount_filter = None

    try:
        rows = await db.export_transactions(
            amount_filter=amount_filter,
            person_ids=person_ids,
        )
    except Exception:  # pragma: no cover - defensive logging
        LOGGER.exception("Failed to export transactions")
        target = get_reply_target(update)
        await target.reply_text(get_text("export_error", language))
        clear_workflow(context)
        await send_main_menu_reply(update, context, language)
        return ConversationHandler.END

    if not rows:
        target = get_reply_target(update)
        await target.reply_text(get_text("export_no_transactions", language))
        clear_workflow(context)
        await send_main_menu_reply(update, context, language)
        return ConversationHandler.END

    buffer = StringIO()
    writer = csv.writer(buffer)
    writer.writerow(
        [
            get_text("export_column_transaction_id", language),
            get_text("export_column_contact", language),
            get_text("export_column_contact_id", language),
            get_text("export_column_type", language),
            get_text("export_column_amount", language),
            get_text("export_column_description", language),
            get_text("export_column_created_at", language),
        ]
    )

    for row in rows:
        amount = int(row["amount"])
        type_key = (
            "export_type_label_debt" if amount > 0 else "export_type_label_payment"
        )
        writer.writerow(
            [
                row["id"],
                row["person_name"],
                row["person_id"],
                get_text(type_key, language),
                _format_amount(abs(amount)),
                row["description"] or "-",
                row["created_at"],
            ]
        )

    buffer.seek(0)
    document = BytesIO(buffer.getvalue().encode("utf-8"))

    suffix = ""
    if person_ids:
        if len(person_ids) == 1:
            suffix = f"-person-{person_ids[0]}"
        else:
            suffix = "-filtered"

    document.name = (
        f"transactions{suffix}-{datetime.utcnow().strftime('%Y%m%d-%H%M%S')}.csv"
    )

    target = get_reply_target(update)
    await target.reply_document(
        document,
        caption=get_text("export_success", language),
    )
    clear_workflow(context)
    await send_main_menu_reply(update, context, language)
    return ConversationHandler.END


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
        "export_mode",
        "person_state",
        "person_next_state",
        "entry_mode",
        "history_selection",
        "history_available_datetimes",
    ):
        context.user_data.pop(key, None)
    _reset_person_menu_context(context)
    _drop_prompt_message(context)


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
    message = await target.reply_text(
        with_cancel_hint(get_text("enter_person_name", language), language),
        reply_markup=cancel_keyboard(language),
    )
    _remember_prompt_message(update, context, getattr(message, "message_id", None))
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
    flow = context.user_data.get("flow")
    if flow == "export":
        prompt_text = get_text("export_contact_prompt", language)
    else:
        prompt_text = get_text("choose_selection_method", language)
    message = await target.reply_text(
        with_cancel_hint(prompt_text, language),
        reply_markup=selection_method_keyboard(language),
    )
    _remember_prompt_message(update, context, getattr(message, "message_id", None))
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

    if flow == "export":
        return await perform_export(
            update,
            context,
            language,
            person_ids=[person.id],
        )

    if flow == "debt" and entry_mode == "menu":
        context.user_data.pop("amount", None)
        context.user_data.pop("description", None)
        context.user_data["person_state"] = DEBT_AMOUNT
        await target.reply_text(
            with_cancel_hint(
                get_text("enter_debt_amount", language).format(name=person.name),
                language,
            ),
            reply_markup=cancel_keyboard(language),
        )
        return DEBT_AMOUNT

    if flow == "payment" and entry_mode == "menu":
        context.user_data.pop("amount", None)
        context.user_data.pop("description", None)
        context.user_data["person_state"] = PAYMENT_AMOUNT
        await target.reply_text(
            with_cancel_hint(
                get_text("enter_payment_amount", language).format(name=person.name),
                language,
            ),
            reply_markup=cancel_keyboard(language),
        )
        return PAYMENT_AMOUNT

    if next_state == HISTORY_DATES:
        message = await target.reply_text(
            with_cancel_hint(get_text("history_choose_range", language), language),
            reply_markup=history_range_keyboard(language),
        )
        _remember_prompt_message(update, context, getattr(message, "message_id", None))
        return HISTORY_DATES

    if context.user_data.get("person_state") == SEARCH_QUERY:
        return SEARCH_QUERY
    return ConversationHandler.END


async def show_person_menu(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    language: str,
    page: int = 0,
    search_query: Optional[str] = None,
) -> int:
    db: Database = context.bot_data["db"]
    search_mode = False
    people: Sequence[PersonUsageStats]
    query_text: Optional[str] = None

    if search_query is not None:
        all_people = await db.list_people_with_usage()
        filtered = [
            entry
            for entry in all_people
            if search_query.casefold() in entry.person.name.casefold()
        ]
        if not filtered:
            target = get_reply_target(update)
            await target.reply_text(get_text("menu_search_no_results", language))
            _reset_person_menu_context(context)
            return await show_person_menu(update, context, language, page=0)
        search_mode = True
        query_text = search_query
        people = filtered
        context.user_data["person_menu_mode"] = "search"
        context.user_data["person_menu_search_query"] = search_query
        context.user_data["person_menu_results"] = filtered
        context.user_data["person_menu_search_expected"] = False
        page = 0
    else:
        mode = context.user_data.get("person_menu_mode")
        stored_query = context.user_data.get("person_menu_search_query")
        stored_results = context.user_data.get("person_menu_results")
        if mode == "search" and stored_query:
            search_mode = True
            query_text = stored_query
            if stored_results is None:
                all_people = await db.list_people_with_usage()
                stored_results = [
                    entry
                    for entry in all_people
                    if stored_query.casefold() in entry.person.name.casefold()
                ]
                context.user_data["person_menu_results"] = stored_results
            people = stored_results or []
        else:
            people = await db.list_people_with_usage()
            if not people:
                target = get_reply_target(update)
                await target.reply_text(
                    with_cancel_hint(get_text("no_people", language), language),
                    reply_markup=cancel_keyboard(language),
                )
                context.user_data.pop("entry_mode", None)
                _reset_person_menu_context(context)
                return context.user_data.get("person_state", ConversationHandler.END)
            context.user_data["person_menu_mode"] = "all"
            context.user_data["person_menu_results"] = people
            context.user_data.pop("person_menu_search_query", None)
            context.user_data["person_menu_search_expected"] = False

    if search_mode and not people:
        target = get_reply_target(update)
        await target.reply_text(get_text("menu_search_no_results", language))
        _reset_person_menu_context(context)
        return await show_person_menu(update, context, language, page=0)

    total = len(people)
    total_pages = max(1, (total + PERSON_MENU_PAGE_SIZE - 1) // PERSON_MENU_PAGE_SIZE)
    page = max(0, min(page, total_pages - 1))
    start = page * PERSON_MENU_PAGE_SIZE
    end = start + PERSON_MENU_PAGE_SIZE
    current_slice = people[start:end]

    if search_mode and query_text is not None:
        base_message = get_text("menu_search_results", language).format(
            query=query_text,
            count=total,
            page=f"{page + 1}/{total_pages}",
        )
    else:
        base_message = get_text("menu_prompt", language).format(
            page=f"{page + 1}/{total_pages}"
        )
    message = with_cancel_hint(base_message, language)
    keyboard = person_menu_keyboard(
        current_slice,
        language,
        page,
        total_pages,
        search_active=search_mode,
    )

    query = update.callback_query
    if query and query.message:
        await query.message.edit_text(message, reply_markup=keyboard)
    else:
        target = get_reply_target(update)
        await target.reply_text(message, reply_markup=keyboard)

    context.user_data["person_menu_page"] = page
    return context.user_data.get("person_state", ConversationHandler.END)


async def handle_person_menu_search(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> int:
    query = update.callback_query
    if not query or not query.data:
        return context.user_data.get("person_state", ConversationHandler.END)

    language = await get_language(context, update.effective_user.id)
    parts = query.data.split(":", 1)
    action = parts[1] if len(parts) == 2 else "start"

    await query.answer()

    if action == "clear":
        _reset_person_menu_context(context)
        return await show_person_menu(update, context, language, page=0)

    context.user_data["person_menu_search_expected"] = True
    target = get_reply_target(update)
    await target.reply_text(get_text("menu_search_question", language))
    return context.user_data.get("person_state", ConversationHandler.END)


async def _maybe_handle_person_menu_search_message(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> Optional[int]:
    if not context.user_data.get("person_menu_search_expected"):
        return None

    language = await get_language(context, update.effective_user.id)
    text = update.message.text.strip()
    if not text:
        await update.message.reply_text(get_text("menu_search_question", language))
        context.user_data["person_menu_search_expected"] = True
        return context.user_data.get("person_state", ConversationHandler.END)

    context.user_data["person_menu_search_expected"] = False
    return await show_person_menu(update, context, language, page=0, search_query=text)


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
        _reset_person_menu_context(context)
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
        elif flow == "export":
            message = get_text("export_prompt_person_id", language)
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
        _reset_person_menu_context(context)
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
    maybe_state = await _maybe_handle_person_menu_search_message(update, context)
    if maybe_state is not None:
        return maybe_state

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

    _reset_person_menu_context(context)
    return await advance_person_workflow(update, context, person, language)


# ---- Add Debt ----
async def start_add_debt(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    clear_workflow(context)
    context.user_data["flow"] = "debt"
    context.user_data["person_state"] = DEBT_ENTRY
    return await prompt_person_selection(update, context)


async def receive_debt_entry(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    maybe_state = await _maybe_handle_person_menu_search_message(update, context)
    if maybe_state is not None:
        return maybe_state

    language = await get_language(context, update.effective_user.id)
    text = update.message.text.strip()
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

    amount = _parse_positive_amount(raw_amount)
    if amount is None:
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
            name=person.name,
            amount=_format_amount(amount),
            balance=_format_amount(balance),
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


async def receive_debt_amount(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    language = await get_language(context, update.effective_user.id)
    person: Optional[Person] = context.user_data.get("person")
    if not person:
        return await cancel(update, context)

    text = update.message.text.strip()
    amount = _parse_positive_amount(text)
    if amount is None:
        context.user_data["person_state"] = DEBT_AMOUNT
        await update.message.reply_text(
            with_cancel_hint(get_text("invalid_number", language), language),
            reply_markup=cancel_keyboard(language),
        )
        return DEBT_AMOUNT

    context.user_data["amount"] = amount
    context.user_data["person_state"] = DEBT_DESCRIPTION
    message = with_cancel_hint(
        get_text("enter_debt_description", language),
        language,
    )
    await update.message.reply_text(
        message,
        reply_markup=skip_keyboard(language, "debt_description"),
    )
    return DEBT_DESCRIPTION


async def _complete_menu_debt(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    language: str,
    description: str,
) -> int:
    person: Optional[Person] = context.user_data.get("person")
    amount = context.user_data.get("amount")
    if not person or amount is None:
        return await cancel(update, context)

    amount_value = int(amount)
    db: Database = context.bot_data["db"]
    await db.add_transaction(person.id, amount_value, description)
    balance = await db.get_balance(person.id)
    target = get_reply_target(update)
    await target.reply_text(
        get_text("debt_recorded", language).format(
            name=person.name,
            amount=_format_amount(amount_value),
            balance=_format_amount(balance),
        )
    )
    LOGGER.info(
        "Debt recorded for person_id=%s amount=%s description=%s",
        person.id,
        amount_value,
        description,
    )
    clear_workflow(context)
    await send_main_menu_reply(update, context, language)
    return ConversationHandler.END


async def receive_debt_description(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> int:
    language = await get_language(context, update.effective_user.id)
    description = update.message.text.strip()
    return await _complete_menu_debt(update, context, language, description)


async def skip_debt_description(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    language = await get_language(context, update.effective_user.id)
    if update.callback_query:
        await update.callback_query.answer()
        if update.callback_query.message:
            await update.callback_query.message.edit_reply_markup(reply_markup=None)
    return await _complete_menu_debt(update, context, language, "")


# ---- Payments ----
async def start_payment(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    clear_workflow(context)
    context.user_data["flow"] = "payment"
    context.user_data["person_state"] = PAYMENT_ENTRY
    return await prompt_person_selection(update, context)


async def receive_payment_entry(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    maybe_state = await _maybe_handle_person_menu_search_message(update, context)
    if maybe_state is not None:
        return maybe_state

    language = await get_language(context, update.effective_user.id)
    text = update.message.text.strip()
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

    amount = _parse_positive_amount(raw_amount)
    if amount is None:
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
        get_text("payment_recorded", language).format(
            name=person.name, balance=_format_amount(balance)
        )
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


async def receive_payment_amount(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    language = await get_language(context, update.effective_user.id)
    person: Optional[Person] = context.user_data.get("person")
    if not person:
        return await cancel(update, context)

    text = update.message.text.strip()
    amount = _parse_positive_amount(text)
    if amount is None:
        context.user_data["person_state"] = PAYMENT_AMOUNT
        await update.message.reply_text(
            with_cancel_hint(get_text("invalid_number", language), language),
            reply_markup=cancel_keyboard(language),
        )
        return PAYMENT_AMOUNT

    context.user_data["amount"] = amount
    context.user_data["person_state"] = PAYMENT_DESCRIPTION
    message = with_cancel_hint(
        get_text("enter_payment_description", language),
        language,
    )
    await update.message.reply_text(
        message,
        reply_markup=skip_keyboard(language, "payment_description"),
    )
    return PAYMENT_DESCRIPTION


async def _complete_menu_payment(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    language: str,
    description: str,
) -> int:
    person: Optional[Person] = context.user_data.get("person")
    amount = context.user_data.get("amount")
    if not person or amount is None:
        return await cancel(update, context)

    db: Database = context.bot_data["db"]
    stored_amount = -abs(int(amount))
    await db.add_transaction(person.id, stored_amount, description)
    balance = await db.get_balance(person.id)
    target = get_reply_target(update)
    await target.reply_text(
        get_text("payment_recorded", language).format(
            name=person.name, balance=_format_amount(balance)
        )
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


async def receive_payment_description(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> int:
    language = await get_language(context, update.effective_user.id)
    description = update.message.text.strip()
    return await _complete_menu_payment(update, context, language, description)


async def skip_payment_description(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    language = await get_language(context, update.effective_user.id)
    if update.callback_query:
        await update.callback_query.answer()
        if update.callback_query.message:
            await update.callback_query.message.edit_reply_markup(reply_markup=None)
    return await _complete_menu_payment(update, context, language, "")


# ---- History ----
async def start_history(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    clear_workflow(context)
    context.user_data["flow"] = "history"
    context.user_data["person_state"] = HISTORY_PERSON
    context.user_data["person_next_state"] = HISTORY_DATES
    return await prompt_person_selection(update, context)


def _ensure_history_selection(context: ContextTypes.DEFAULT_TYPE) -> dict[str, Any]:
    selection = context.user_data.get("history_selection")
    if selection is None:
        selection = {"phase": "start", "start": {}, "end": {}}
        context.user_data["history_selection"] = selection
    else:
        selection.setdefault("phase", "start")
        selection.setdefault("start", {})
        selection.setdefault("end", {})
    return selection


async def _load_history_datetimes(
    context: ContextTypes.DEFAULT_TYPE,
) -> Sequence[datetime]:
    datetimes: Optional[Sequence[datetime]] = context.user_data.get(
        "history_available_datetimes"
    )
    if datetimes is not None:
        return datetimes
    db: Database = context.bot_data["db"]
    person: Person = context.user_data["person"]
    datetimes = await db.get_transaction_timestamps(person.id)
    context.user_data["history_available_datetimes"] = datetimes
    return datetimes


def _history_phase_label(language: str, phase: str) -> str:
    key = "history_phase_start" if phase == "start" else "history_phase_end"
    return get_text(key, language)


def _history_available_years(
    datetimes: Sequence[datetime], *, min_dt: Optional[datetime] = None
) -> list[int]:
    return sorted({dt.year for dt in datetimes if min_dt is None or dt >= min_dt})


def _history_available_months(
    datetimes: Sequence[datetime],
    year: int,
    *,
    min_dt: Optional[datetime] = None,
) -> list[int]:
    return sorted(
        {
            dt.month
            for dt in datetimes
            if dt.year == year and (min_dt is None or dt >= min_dt)
        }
    )


def _history_available_days(
    datetimes: Sequence[datetime],
    year: int,
    month: int,
    *,
    min_dt: Optional[datetime] = None,
) -> list[int]:
    return sorted(
        {
            dt.day
            for dt in datetimes
            if dt.year == year
            and dt.month == month
            and (min_dt is None or dt >= min_dt)
        }
    )


def _history_available_hours(
    datetimes: Sequence[datetime],
    year: int,
    month: int,
    day: int,
    *,
    min_dt: Optional[datetime] = None,
) -> list[int]:
    return sorted(
        {
            dt.hour
            for dt in datetimes
            if dt.year == year
            and dt.month == month
            and dt.day == day
            and (min_dt is None or dt >= min_dt)
        }
    )


def _history_pick_datetime(
    datetimes: Sequence[datetime],
    *,
    year: int,
    month: int,
    day: int,
    hour: int,
    min_dt: Optional[datetime] = None,
    phase: str,
) -> datetime:
    candidates = sorted(
        dt
        for dt in datetimes
        if dt.year == year
        and dt.month == month
        and dt.day == day
        and dt.hour == hour
        and (min_dt is None or dt >= min_dt)
    )
    if not candidates:
        return datetime(year, month, day, hour)
    return candidates[0] if phase == "start" else candidates[-1]


def _format_history_datetime(value: datetime) -> str:
    return value.strftime("%Y-%m-%d %H:%M")


async def _prompt_history_custom_level(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    language: str,
    *,
    phase: str,
    level: str,
) -> int:
    datetimes = await _load_history_datetimes(context)
    selection = _ensure_history_selection(context)
    min_dt: Optional[datetime] = None
    if phase == "end":
        start_dt: Optional[datetime] = selection.get("start", {}).get("datetime")
        if start_dt is not None:
            min_dt = start_dt

    query = update.callback_query
    if query is None:
        raise RuntimeError("Custom range selection requires a callback query")

    if level == "year":
        options = _history_available_years(datetimes, min_dt=min_dt)
        keyboard = history_custom_year_keyboard(language, options, phase)
    elif level == "month":
        year = int(selection[phase]["year"])
        options = _history_available_months(datetimes, year, min_dt=min_dt)
        keyboard = history_custom_month_keyboard(language, options, phase)
    elif level == "day":
        year = int(selection[phase]["year"])
        month = int(selection[phase]["month"])
        options = _history_available_days(datetimes, year, month, min_dt=min_dt)
        keyboard = history_custom_day_keyboard(language, options, phase)
    elif level == "hour":
        year = int(selection[phase]["year"])
        month = int(selection[phase]["month"])
        day = int(selection[phase]["day"])
        options = _history_available_hours(
            datetimes, year, month, day, min_dt=min_dt
        )
        keyboard = history_custom_hour_keyboard(language, options, phase)
    else:
        raise ValueError(f"Unknown custom selection level: {level}")

    if not options:
        if phase == "end":
            selection.setdefault("end", {})["datetime"] = selection["start"]["datetime"]
            summary = get_text("history_custom_range_summary", language).format(
                start=_format_history_datetime(selection["start"]["datetime"]),
                end=_format_history_datetime(selection["end"]["datetime"]),
            )
            await query.message.edit_text(
                with_cancel_hint(summary, language),
                reply_markup=history_confirmation_keyboard(language),
            )
            return HISTORY_DATES
        raise RuntimeError("No options available for custom range selection")

    phase_label = _history_phase_label(language, phase)
    prompt = get_text(f"history_custom_select_{level}", language).format(
        phase=phase_label
    )
    await query.message.edit_text(
        with_cancel_hint(prompt, language), reply_markup=keyboard
    )
    return HISTORY_DATES


async def _show_history(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    *,
    start_date: Optional[datetime] = None,
    end_date: Optional[datetime] = None,
) -> int:
    language = await get_language(context, update.effective_user.id)
    db: Database = context.bot_data["db"]
    person: Person = context.user_data["person"]
    history = await db.get_history(
        person.id, start_date=start_date, end_date=end_date
    )
    target = get_reply_target(update)
    if not history:
        await target.reply_text(get_text("history_empty", language))
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
                amount=_format_amount(abs(item.amount)),
                description=description,
                date=item.created_at.strftime("%Y-%m-%d %H:%M"),
            )
        )
    await target.reply_text(
        "\n".join(lines),
        parse_mode=constants.ParseMode.HTML,
        reply_markup=cancel_keyboard(language),
    )
    clear_workflow(context)
    await send_main_menu_reply(update, context, language)
    return ConversationHandler.END


async def fetch_history(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    language = await get_language(context, update.effective_user.id)
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
    return await _show_history(
        update, context, start_date=start_date, end_date=end_date
    )


async def handle_history_range_selection(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> int:
    await answer_callback(update)
    query = update.callback_query
    if query is None:
        return HISTORY_DATES
    language = await get_language(context, update.effective_user.id)
    choice = query.data.split(":", 2)[2]

    now = datetime.now()
    today_start = datetime(now.year, now.month, now.day)
    end_of_today = today_start + timedelta(days=1) - timedelta(microseconds=1)

    if choice == "skip":
        await query.message.edit_text(
            with_cancel_hint(get_text("history_range_all_records", language), language),
            reply_markup=None,
        )
        return await _show_history(update, context)

    if choice == "custom":
        datetimes = await _load_history_datetimes(context)
        if not datetimes:
            await query.message.edit_text(
                with_cancel_hint(get_text("history_no_custom_data", language), language),
                reply_markup=None,
            )
            return await _show_history(update, context)
        selection = _ensure_history_selection(context)
        selection["phase"] = "start"
        selection["start"] = {}
        selection["end"] = {}
        return await _prompt_history_custom_level(
            update, context, language, phase="start", level="year"
        )

    label_map = {
        "today": get_text("history_range_today", language),
        "last7": get_text("history_range_last_7_days", language),
        "this_month": get_text("history_range_this_month", language),
    }

    if choice == "today":
        start_date = today_start
        end_date = end_of_today
    elif choice == "last7":
        start_date = today_start - timedelta(days=6)
        end_date = end_of_today
    elif choice == "this_month":
        start_date = today_start.replace(day=1)
        end_date = end_of_today
    else:
        LOGGER.warning("Unknown history range choice: %s", choice)
        return HISTORY_DATES

    await query.message.edit_text(
        with_cancel_hint(
            get_text("history_fetching_range", language).format(
                label=label_map.get(choice, get_text("history_range_custom", language))
            ),
            language,
        ),
        reply_markup=None,
    )
    return await _show_history(
        update, context, start_date=start_date, end_date=end_date
    )


async def handle_history_custom_selection(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> int:
    await answer_callback(update)
    query = update.callback_query
    if query is None or query.data is None:
        return HISTORY_DATES
    language = await get_language(context, update.effective_user.id)
    parts = query.data.split(":", 4)
    if len(parts) != 5:
        LOGGER.warning("Invalid custom range payload: %s", query.data)
        return HISTORY_DATES
    _, _, phase, level, raw_value = parts
    selection = _ensure_history_selection(context)
    phase_bucket = selection.setdefault(phase, {})
    phase_bucket[level] = int(raw_value)

    order = ["year", "month", "day", "hour"]
    current_index = order.index(level)
    if current_index < len(order) - 1:
        next_level = order[current_index + 1]
        return await _prompt_history_custom_level(
            update, context, language, phase=phase, level=next_level
        )

    datetimes = await _load_history_datetimes(context)
    year = int(phase_bucket["year"])
    month = int(phase_bucket["month"])
    day = int(phase_bucket["day"])
    hour = int(phase_bucket["hour"])
    min_dt = selection.get("start", {}).get("datetime") if phase == "end" else None
    chosen_dt = _history_pick_datetime(
        datetimes,
        year=year,
        month=month,
        day=day,
        hour=hour,
        min_dt=min_dt,
        phase=phase,
    )
    phase_bucket["datetime"] = chosen_dt

    if phase == "start":
        selection["phase"] = "end"
        available_years = _history_available_years(datetimes, min_dt=chosen_dt)
        if not available_years:
            selection.setdefault("end", {})["datetime"] = chosen_dt
            summary = get_text("history_custom_range_summary", language).format(
                start=_format_history_datetime(chosen_dt),
                end=_format_history_datetime(chosen_dt),
            )
            await query.message.edit_text(
                with_cancel_hint(summary, language),
                reply_markup=history_confirmation_keyboard(language),
            )
            return HISTORY_DATES
        start_text = get_text("history_custom_start_selected", language).format(
            start=_format_history_datetime(chosen_dt)
        )
        await query.message.edit_text(
            with_cancel_hint(start_text, language),
            reply_markup=history_custom_year_keyboard(
                language, available_years, phase="end"
            ),
        )
        return HISTORY_DATES

    start_dt: Optional[datetime] = selection.get("start", {}).get("datetime")
    if start_dt and chosen_dt < start_dt:
        chosen_dt = start_dt
        phase_bucket["datetime"] = chosen_dt
    summary = get_text("history_custom_range_summary", language).format(
        start=_format_history_datetime(start_dt or chosen_dt),
        end=_format_history_datetime(chosen_dt),
    )
    await query.message.edit_text(
        with_cancel_hint(summary, language),
        reply_markup=history_confirmation_keyboard(language),
    )
    return HISTORY_DATES


async def handle_history_confirmation(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> int:
    await answer_callback(update)
    query = update.callback_query
    if query is None or query.data is None:
        return HISTORY_DATES
    language = await get_language(context, update.effective_user.id)
    parts = query.data.split(":", 2)
    action = parts[2] if len(parts) == 3 else ""
    selection = _ensure_history_selection(context)

    if action == "restart":
        selection["phase"] = "start"
        selection["start"] = {}
        selection["end"] = {}
        return await _prompt_history_custom_level(
            update, context, language, phase="start", level="year"
        )

    if action == "ok":
        start_dt: Optional[datetime] = selection.get("start", {}).get("datetime")
        end_dt: Optional[datetime] = selection.get("end", {}).get("datetime")
        if start_dt is None or end_dt is None:
            LOGGER.warning("Incomplete custom range selection during confirmation")
            selection["phase"] = "start"
            selection["start"] = {}
            selection["end"] = {}
            return await _prompt_history_custom_level(
                update, context, language, phase="start", level="year"
            )
        if end_dt < start_dt:
            end_dt = start_dt
        await query.message.edit_text(
            with_cancel_hint(
                get_text("history_fetching_range", language).format(
                    label=get_text("history_range_custom", language)
                ),
                language,
            ),
            reply_markup=None,
        )
        return await _show_history(
            update, context, start_date=start_dt, end_date=end_dt
        )

    LOGGER.warning("Unknown history confirmation action: %s", action)
    return HISTORY_DATES


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
    message = await target.reply_text(
        with_cancel_hint(get_text("language_prompt", language), language),
        reply_markup=language_keyboard(language),
    )
    _remember_prompt_message(update, context, getattr(message, "message_id", None))
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
        message = await target.reply_text(
            with_cancel_hint(get_text("language_prompt_codes", language), language),
            reply_markup=language_keyboard(language),
        )
        _remember_prompt_message(update, context, getattr(message, "message_id", None))
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
    application.add_handler(CommandHandler("people", show_people_list))
    application.add_handler(CallbackQueryHandler(show_dashboard, pattern="^menu:dashboard$"))
    application.add_handler(CallbackQueryHandler(show_people_list, pattern="^menu:list_people$"))
    application.add_handler(CallbackQueryHandler(go_back_to_main_menu, pattern="^menu:back_to_main$"))

    export_conv = ConversationHandler(
        entry_points=_wrap_handlers(
            [
                CommandHandler("export", start_export_transactions),
                CallbackQueryHandler(start_export_transactions, pattern="^menu:export$"),
            ]
        ),
        states={
            EXPORT_MODE: _wrap_handlers(
                [
                    CallbackQueryHandler(handle_export_mode, pattern="^export:mode:"),
                    CommandHandler("skip", skip_export_contacts),
                    CallbackQueryHandler(cancel, pattern="^workflow:cancel$"),
                ]
            ),
            EXPORT_CONTACT_CHOICE: _wrap_handlers(
                [
                    CallbackQueryHandler(
                        handle_export_contact_choice, pattern="^export:contacts:"
                    ),
                    CommandHandler("skip", skip_export_contacts),
                    CallbackQueryHandler(cancel, pattern="^workflow:cancel$"),
                ]
            ),
            EXPORT_PERSON: _wrap_handlers(
                [
                    MessageHandler(
                        filters.TEXT & ~filters.COMMAND, receive_person_reference
                    ),
                    CommandHandler("skip", skip_export_contacts),
                    CallbackQueryHandler(handle_selection_method, pattern="^method:"),
                    CallbackQueryHandler(
                        handle_person_menu_navigation, pattern="^person_page:"
                    ),
                    CallbackQueryHandler(
                        handle_person_menu_search, pattern="^person_search"
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
        name="export",
    )
    application.add_handler(export_conv)

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
                    CallbackQueryHandler(
                        handle_person_menu_search, pattern="^person_search"
                    ),
                    CallbackQueryHandler(handle_person_selection, pattern="^select_person:"),
                    CallbackQueryHandler(cancel, pattern="^workflow:cancel$"),
                ]
            ),
            DEBT_AMOUNT: _wrap_handlers(
                [
                    MessageHandler(
                        filters.TEXT & ~filters.COMMAND, receive_debt_amount
                    ),
                    CallbackQueryHandler(cancel, pattern="^workflow:cancel$"),
                ]
            ),
            DEBT_DESCRIPTION: _wrap_handlers(
                [
                    MessageHandler(
                        filters.TEXT & ~filters.COMMAND, receive_debt_description
                    ),
                    CommandHandler("skip", skip_debt_description),
                    CallbackQueryHandler(
                        skip_debt_description, pattern="^skip:debt_description$"
                    ),
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
                    CallbackQueryHandler(
                        handle_person_menu_search, pattern="^person_search"
                    ),
                    CallbackQueryHandler(handle_person_selection, pattern="^select_person:"),
                    CallbackQueryHandler(cancel, pattern="^workflow:cancel$"),
                ]
            ),
            PAYMENT_AMOUNT: _wrap_handlers(
                [
                    MessageHandler(
                        filters.TEXT & ~filters.COMMAND, receive_payment_amount
                    ),
                    CallbackQueryHandler(cancel, pattern="^workflow:cancel$"),
                ]
            ),
            PAYMENT_DESCRIPTION: _wrap_handlers(
                [
                    MessageHandler(
                        filters.TEXT & ~filters.COMMAND, receive_payment_description
                    ),
                    CommandHandler("skip", skip_payment_description),
                    CallbackQueryHandler(
                        skip_payment_description, pattern="^skip:payment_description$"
                    ),
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
                    CallbackQueryHandler(
                        handle_person_menu_search, pattern="^person_search"
                    ),
                    CallbackQueryHandler(handle_person_selection, pattern="^select_person:"),
                    CallbackQueryHandler(cancel, pattern="^workflow:cancel$"),
                ]
            ),
            HISTORY_DATES: _wrap_handlers(
                [
                    MessageHandler(filters.TEXT & ~filters.COMMAND, fetch_history),
                    CommandHandler("skip", fetch_history),
                    CallbackQueryHandler(
                        handle_history_range_selection, pattern="^history:range:"
                    ),
                    CallbackQueryHandler(
                        handle_history_custom_selection, pattern="^history:custom:"
                    ),
                    CallbackQueryHandler(
                        handle_history_confirmation, pattern="^history:confirm:"
                    ),
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
        name="history",
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
    db = Database(config.database_path, backup_config=config.backup)
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
