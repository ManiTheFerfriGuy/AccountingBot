"""Telegram bot entry point for AccountingBot."""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from typing import Optional

from telegram import InlineKeyboardMarkup, Update, constants
from telegram.ext import (AIORateLimiter, Application, ApplicationBuilder,
                          CallbackQueryHandler, CommandHandler,
                          ConversationHandler, ContextTypes, MessageHandler,
                          filters)

from .config import load_config
from .database import Database, Person
from .keyboards import confirmation_keyboard, language_keyboard, main_menu
from .localization import get_text

# Conversation states
ADD_PERSON_NAME = 1
SELECT_DEBT_PERSON, ENTER_DEBT_AMOUNT, ENTER_DEBT_DESCRIPTION, CONFIRM_DEBT = range(10, 14)
SELECT_PAYMENT_PERSON, ENTER_PAYMENT_AMOUNT, CONFIRM_PAYMENT = range(20, 23)
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


def build_person_keyboard(language: str, people: list[Person]) -> InlineKeyboardMarkup:
    from telegram import InlineKeyboardButton

    buttons = []
    row = []
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
    buttons.append(
        [
            InlineKeyboardButton(get_text("search", language), callback_data="search"),
            InlineKeyboardButton(get_text("cancel", language), callback_data="cancel"),
        ]
    )
    return InlineKeyboardMarkup(buttons)


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
    ):
        context.user_data.pop(key, None)


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
    person = await db.add_person(name)
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
    language = await get_language(context, update.effective_user.id)
    db: Database = context.bot_data["db"]
    people = await db.list_people(limit=20)
    if not people:
        message = get_text("no_people", language)
        if update.callback_query:
            await update.callback_query.answer()
            await update.callback_query.message.edit_text(
                message, reply_markup=main_menu(language)
            )
        else:
            await update.message.reply_text(message, reply_markup=main_menu(language))
        return ConversationHandler.END
    keyboard = build_person_keyboard(language, people)
    text = get_text("enter_person_prompt", language)
    if update.message:
        await update.message.reply_text(text, reply_markup=keyboard)
    elif update.callback_query:
        await update.callback_query.answer()
        await update.callback_query.message.edit_text(text, reply_markup=keyboard)
    context.user_data["selection_mode"] = True
    context.user_data["selection_state"] = selection_state
    context.user_data["after_state"] = after_state
    return selection_state


async def select_person_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    language = await get_language(context, update.effective_user.id)
    data = query.data
    if data == "cancel":
        return await cancel(update, context)
    if data == "search":
        await query.edit_message_text(get_text("search_prompt", language))
        return SEARCH_QUERY
    if data.startswith("person:"):
        person_id = int(data.split(":", 1)[1])
        context.user_data["selected_person"] = person_id
        context.user_data["selection_mode"] = False
        after_state = context.user_data.get("after_state")
        if after_state == ENTER_DEBT_AMOUNT:
            return await prompt_debt_amount(update, context)
        if after_state == ENTER_PAYMENT_AMOUNT:
            return await prompt_payment_amount(update, context)
        if after_state == ENTER_HISTORY_DATES:
            return await prompt_history_dates(update, context)
    return ConversationHandler.END


async def handle_person_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    language = await get_language(context, update.effective_user.id)
    db: Database = context.bot_data["db"]
    text = update.message.text.strip()

    if text.isdigit():
        person = await db.get_person(int(text))
        if not person:
            await update.message.reply_text(get_text("not_found", language))
            return context.user_data.get("selection_state", ConversationHandler.END)
        context.user_data["selected_person"] = person.id
        context.user_data["person"] = person
        context.user_data["selection_mode"] = False
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

    results = await db.search_people(text)
    if not results:
        await update.message.reply_text(get_text("not_found", language))
        return context.user_data.get("selection_state", ConversationHandler.END)
    keyboard = build_person_keyboard(language, results)
    await update.message.reply_text(
        get_text("search_results", language), reply_markup=keyboard
    )
    return context.user_data.get("selection_state", ConversationHandler.END)


async def search_people(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    language = await get_language(context, update.effective_user.id)
    db: Database = context.bot_data["db"]
    text = update.message.text.strip()
    results = await db.search_people(text)
    selection_mode = context.user_data.get("selection_mode")
    if not results:
        await update.message.reply_text(get_text("not_found", language))
        if selection_mode:
            return context.user_data.get("selection_state", ConversationHandler.END)
        return SEARCH_QUERY
    if selection_mode:
        keyboard = build_person_keyboard(language, results)
        await update.message.reply_text(
            get_text("search_results", language), reply_markup=keyboard
        )
        return context.user_data.get("selection_state", ConversationHandler.END)

    lines = [get_text("search_results", language)]
    for person in results:
        balance = await db.get_balance(person.id)
        lines.append(f"#{person.id} — {person.name} ({balance:.2f})")
    await update.message.reply_text("\n".join(lines))
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
    return await confirm_debt(update, context)


async def confirm_debt(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    language = await get_language(context, update.effective_user.id)
    person: Person = context.user_data["person"]
    amount = context.user_data["amount"]
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
    keyboard = confirmation_keyboard(language, "payment")
    person: Person = context.user_data["person"]
    text = get_text("confirm_payment", language).format(name=person.name, amount=amount)
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

    lines = [get_text("history_header", language).format(name=person.name)]
    for item in history:
        template = "history_item_payment" if item.is_payment else "history_item_debt"
        description = item.description or "-"
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
    await update.message.reply_text(get_text("search_prompt", language))
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
            SEARCH_QUERY: [
                CallbackQueryHandler(select_person_callback),
                MessageHandler(filters.TEXT & ~filters.COMMAND, search_people),
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
            CONFIRM_PAYMENT: [
                CallbackQueryHandler(finalize_payment, pattern=r"^confirm:payment$"),
                CallbackQueryHandler(handle_back, pattern=r"^back$"),
            ],
            SEARCH_QUERY: [
                CallbackQueryHandler(select_person_callback),
                MessageHandler(filters.TEXT & ~filters.COMMAND, search_people),
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
            SEARCH_QUERY: [
                CallbackQueryHandler(select_person_callback),
                MessageHandler(filters.TEXT & ~filters.COMMAND, search_people),
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
