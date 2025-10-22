"""Telegram bot entry point for AccountingBot."""
from __future__ import annotations

import asyncio
import logging
import signal
from datetime import datetime
from html import escape
from typing import Optional

from telegram import Update, constants
from telegram.ext import (
    AIORateLimiter,
    Application,
    ApplicationBuilder,
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
from .localization import available_languages, get_text

# Conversation states
ADD_PERSON_NAME = 1
DEBT_PERSON, DEBT_AMOUNT, DEBT_DESCRIPTION, DEBT_CONFIRM = range(10, 14)
PAYMENT_PERSON, PAYMENT_AMOUNT, PAYMENT_DESCRIPTION, PAYMENT_CONFIRM = range(20, 24)
HISTORY_PERSON, HISTORY_DATES = range(30, 32)
SEARCH_QUERY = 40
LANGUAGE_SELECTION = 50

LOGGER = logging.getLogger(__name__)


async def get_language(context: ContextTypes.DEFAULT_TYPE, user_id: int) -> str:
    language = context.user_data.get("language")
    if language:
        return language
    db: Database = context.bot_data["db"]
    language = await db.get_user_language(user_id)
    context.user_data["language"] = language
    return language


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


def compose_start_message(language: str) -> str:
    lines = [get_text("start_message", language), ""]
    lines.append(get_text("start_command_overview", language))
    commands = [
        ("/add_person", get_text("add_person", language)),
        ("/add_debt", get_text("add_debt", language)),
        ("/record_payment", get_text("pay_debt", language)),
        ("/history", get_text("history", language)),
        ("/dashboard", get_text("dashboard", language)),
        ("/search", get_text("search", language)),
        ("/language", get_text("language", language)),
        ("/help", get_text("help_label", language)),
        ("/cancel", get_text("cancel", language)),
    ]
    lines.extend(f"{command} — {description}" for command, description in commands)
    lines.append("")
    lines.append(get_text("start_search_hint", language))
    lines.append(get_text("start_cancel_hint", language))
    return "\n".join(lines)


async def send_start_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    language = await get_language(context, update.effective_user.id)
    message = compose_start_message(language)
    if update.message:
        await update.message.reply_text(message)
    elif update.callback_query:
        await update.callback_query.answer()
        await update.callback_query.message.reply_text(message)


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
    if update.message:
        await update.message.reply_text(text)
    elif update.callback_query:
        await update.callback_query.answer()
        await update.callback_query.message.reply_text(text)
    clear_workflow(context)


def clear_workflow(context: ContextTypes.DEFAULT_TYPE) -> None:
    for key in (
        "flow",
        "person",
        "amount",
        "description",
        "person_state",
        "person_next_state",
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
            get_text("action_cancelled", language)
        )
    return ConversationHandler.END


async def prompt_person_name(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    language = await get_language(context, update.effective_user.id)
    clear_workflow(context)
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
        get_text("person_added", language).format(name=person.name, id=person.id)
    )
    clear_workflow(context)
    return ConversationHandler.END


async def prompt_person_selection(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> int:
    language = await get_language(context, update.effective_user.id)
    await update.message.reply_text(get_text("prompt_person_identifier", language))
    await update.message.reply_text(get_text("person_id_hint", language))
    return context.user_data.get("person_state", ConversationHandler.END)


async def receive_person_reference(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> int:
    language = await get_language(context, update.effective_user.id)
    db: Database = context.bot_data["db"]
    text = update.message.text.strip()
    state = context.user_data.get("person_state", ConversationHandler.END)
    if not text:
        await update.message.reply_text(get_text("person_id_hint", language))
        return state

    normalized = text.lstrip("#")
    person: Optional[Person] = None
    if normalized.isdigit():
        person = await db.get_person(int(normalized))
        if not person:
            await update.message.reply_text(get_text("not_found", language))
            await update.message.reply_text(get_text("person_id_hint", language))
            return state
    else:
        response = await db.search_people(text)
        if response.matches:
            formatted = format_search_results(language, response)
            await update.message.reply_text(formatted)
        else:
            message = get_text("not_found", language)
            if response.suggestions:
                message = get_text("search_suggestions", language).format(
                    suggestions=", ".join(response.suggestions)
                )
            await update.message.reply_text(message)
        await update.message.reply_text(get_text("person_id_hint", language))
        return state

    context.user_data["person"] = person
    next_state = context.user_data.get("person_next_state", ConversationHandler.END)
    if next_state == DEBT_AMOUNT:
        await update.message.reply_text(
            get_text("enter_debt_amount", language).format(name=person.name)
        )
        return DEBT_AMOUNT
    if next_state == PAYMENT_AMOUNT:
        await update.message.reply_text(
            get_text("enter_payment_amount", language).format(name=person.name)
        )
        return PAYMENT_AMOUNT
    if next_state == HISTORY_DATES:
        await update.message.reply_text(get_text("prompt_date_range", language))
        return HISTORY_DATES
    return ConversationHandler.END


# ---- Add Debt ----
async def start_add_debt(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    clear_workflow(context)
    context.user_data["flow"] = "debt"
    context.user_data["person_state"] = DEBT_PERSON
    context.user_data["person_next_state"] = DEBT_AMOUNT
    return await prompt_person_selection(update, context)


async def receive_debt_amount(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    language = await get_language(context, update.effective_user.id)
    try:
        amount = float(update.message.text)
        if amount <= 0:
            raise ValueError
    except ValueError:
        await update.message.reply_text(get_text("invalid_number", language))
        return DEBT_AMOUNT
    context.user_data["amount"] = amount
    await update.message.reply_text(get_text("enter_debt_description", language))
    return DEBT_DESCRIPTION


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
    instructions = get_text("confirm_instructions", language).format(
        confirm=get_text("confirm_keyword", language),
        back=get_text("back", language),
    )
    await update.message.reply_text(f"{text}\n\n{instructions}")
    return DEBT_CONFIRM


async def finalize_debt(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    language = await get_language(context, update.effective_user.id)
    text = update.message.text.strip().casefold()
    confirm_word = get_text("confirm_keyword", language).casefold()
    back_word = get_text("back", language).casefold()

    person: Person = context.user_data["person"]
    if text == back_word:
        context.user_data.pop("amount", None)
        context.user_data.pop("description", None)
        await update.message.reply_text(
            get_text("enter_debt_amount", language).format(name=person.name)
        )
        return DEBT_AMOUNT
    if text != confirm_word:
        await update.message.reply_text(
            get_text("confirm_invalid", language).format(
                confirm=get_text("confirm_keyword", language),
                back=get_text("back", language),
            )
        )
        return DEBT_CONFIRM

    db: Database = context.bot_data["db"]
    amount = context.user_data["amount"]
    description = context.user_data.get("description", "")
    await db.add_transaction(person.id, amount, description)
    balance = await db.get_balance(person.id)
    await update.message.reply_text(
        get_text("debt_recorded", language).format(
            name=person.name, amount=amount, balance=balance
        )
    )
    LOGGER.info(
        "Debt recorded for person_id=%s amount=%s description=%s", person.id, amount, description
    )
    clear_workflow(context)
    return ConversationHandler.END


# ---- Payments ----
async def start_payment(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    clear_workflow(context)
    context.user_data["flow"] = "payment"
    context.user_data["person_state"] = PAYMENT_PERSON
    context.user_data["person_next_state"] = PAYMENT_AMOUNT
    return await prompt_person_selection(update, context)


async def receive_payment_amount(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    language = await get_language(context, update.effective_user.id)
    try:
        amount = float(update.message.text)
        if amount <= 0:
            raise ValueError
    except ValueError:
        await update.message.reply_text(get_text("invalid_number", language))
        return PAYMENT_AMOUNT
    context.user_data["amount"] = amount
    context.user_data.pop("description", None)
    await update.message.reply_text(get_text("enter_payment_description", language))
    return PAYMENT_DESCRIPTION


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
    instructions = get_text("confirm_instructions", language).format(
        confirm=get_text("confirm_keyword", language),
        back=get_text("back", language),
    )
    await update.message.reply_text(f"{text}\n\n{instructions}")
    return PAYMENT_CONFIRM


async def finalize_payment(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    language = await get_language(context, update.effective_user.id)
    text = update.message.text.strip().casefold()
    confirm_word = get_text("confirm_keyword", language).casefold()
    back_word = get_text("back", language).casefold()

    person: Person = context.user_data["person"]
    if text == back_word:
        context.user_data.pop("amount", None)
        context.user_data.pop("description", None)
        await update.message.reply_text(
            get_text("enter_payment_amount", language).format(name=person.name)
        )
        return PAYMENT_AMOUNT
    if text != confirm_word:
        await update.message.reply_text(
            get_text("confirm_invalid", language).format(
                confirm=get_text("confirm_keyword", language),
                back=get_text("back", language),
            )
        )
        return PAYMENT_CONFIRM

    db: Database = context.bot_data["db"]
    amount = -abs(context.user_data["amount"])
    description = context.user_data.get("description", "")
    await db.add_transaction(person.id, amount, description)
    balance = await db.get_balance(person.id)
    await update.message.reply_text(
        get_text("payment_recorded", language).format(
            name=person.name, balance=balance
        )
    )
    LOGGER.info(
        "Payment recorded for person_id=%s amount=%s description=%s", person.id, amount, description
    )
    clear_workflow(context)
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
            await update.message.reply_text(get_text("invalid_date_range", language))
            return HISTORY_DATES

    history = await db.get_history(person.id, start=start_date, end=end_date)
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
        parse_mode=constants.ParseMode.HTML,
    )
    clear_workflow(context)
    return ConversationHandler.END


# ---- Search ----
async def start_search(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    language = await get_language(context, update.effective_user.id)
    prompt = "\n".join(
        [get_text("search_prompt", language), get_text("search_filters_hint", language)]
    )
    await update.message.reply_text(prompt)
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
        await update.message.reply_text(get_text("search_filters_hint", language))
        return SEARCH_QUERY

    formatted = format_search_results(language, response)
    await update.message.reply_text(formatted)
    await update.message.reply_text(get_text("search_filters_hint", language))
    return SEARCH_QUERY


# ---- Language ----
async def start_language(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    language = await get_language(context, update.effective_user.id)
    options = ", ".join(
        f"{code} ({label})" for code, label in available_languages().items()
    )
    message = "\n".join(
        [
            get_text("language_prompt", language),
            get_text("language_prompt_codes", language).format(codes=options),
        ]
    )
    await update.message.reply_text(message)
    return LANGUAGE_SELECTION


async def change_language(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    requested = update.message.text.strip().casefold()
    languages = available_languages()
    matched_code: Optional[str] = None
    for code, label in languages.items():
        if requested == code.casefold() or requested == label.casefold():
            matched_code = code
            break

    if not matched_code:
        language = await get_language(context, update.effective_user.id)
        options = ", ".join(
            f"{code} ({label})" for code, label in languages.items()
        )
        await update.message.reply_text(
            get_text("language_prompt_codes", language).format(codes=options)
        )
        return LANGUAGE_SELECTION

    db: Database = context.bot_data["db"]
    await db.set_user_language(update.effective_user.id, matched_code)
    context.user_data["language"] = matched_code
    label = languages[matched_code]
    await update.message.reply_text(
        get_text("language_updated", matched_code).format(language=label)
    )
    clear_workflow(context)
    return ConversationHandler.END


async def unknown(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await send_start_message(update, context)


def build_application(config) -> Application:
    application = (
        ApplicationBuilder()
        .token(config.token)
        .rate_limiter(AIORateLimiter())
        .build()
    )
    return application


def register_handlers(application: Application) -> None:
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", show_help))
    application.add_handler(CommandHandler("dashboard", show_dashboard))

    add_person_conv = ConversationHandler(
        entry_points=[CommandHandler("add_person", prompt_person_name)],
        states={
            ADD_PERSON_NAME: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, save_person_name)
            ],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
        name="add_person",
        persistent=False,
    )
    application.add_handler(add_person_conv)

    add_debt_conv = ConversationHandler(
        entry_points=[CommandHandler("add_debt", start_add_debt)],
        states={
            DEBT_PERSON: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, receive_person_reference)
            ],
            DEBT_AMOUNT: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, receive_debt_amount)
            ],
            DEBT_DESCRIPTION: [
                MessageHandler(
                    filters.TEXT & ~filters.COMMAND, receive_debt_description
                ),
                CommandHandler("skip", skip_description),
            ],
            DEBT_CONFIRM: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, finalize_debt)
            ],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
        name="add_debt",
    )
    application.add_handler(add_debt_conv)

    payment_conv = ConversationHandler(
        entry_points=[CommandHandler("record_payment", start_payment)],
        states={
            PAYMENT_PERSON: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, receive_person_reference)
            ],
            PAYMENT_AMOUNT: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, receive_payment_amount)
            ],
            PAYMENT_DESCRIPTION: [
                MessageHandler(
                    filters.TEXT & ~filters.COMMAND, receive_payment_description
                ),
                CommandHandler("skip", skip_description),
            ],
            PAYMENT_CONFIRM: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, finalize_payment)
            ],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
        name="payment",
    )
    application.add_handler(payment_conv)

    history_conv = ConversationHandler(
        entry_points=[CommandHandler("history", start_history)],
        states={
            HISTORY_PERSON: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, receive_person_reference)
            ],
            HISTORY_DATES: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, fetch_history),
                CommandHandler("skip", fetch_history),
            ],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
        name="history",
    )
    application.add_handler(history_conv)

    application.add_handler(
        ConversationHandler(
            entry_points=[
                CommandHandler("search", start_search),
            ],
            states={
                SEARCH_QUERY: [
                    MessageHandler(filters.TEXT & ~filters.COMMAND, search_people)
                ],
            },
            fallbacks=[CommandHandler("cancel", cancel)],
            name="search",
        )
    )

    application.add_handler(
        ConversationHandler(
            entry_points=[CommandHandler("language", start_language)],
            states={
                LANGUAGE_SELECTION: [
                    MessageHandler(filters.TEXT & ~filters.COMMAND, change_language)
                ]
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
