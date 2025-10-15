"""Localization utilities for AccountingBot."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict


@dataclass(frozen=True)
class LanguagePack:
    code: str
    texts: Dict[str, str]

    def get(self, key: str) -> str:
        return self.texts.get(key, key)


_LANGUAGES = {
    "en": LanguagePack(
        code="en",
        texts={
            "start_message": "Welcome to AccountingBot! Choose an option below.",
            "add_person": "Add Person",
            "add_debt": "Add Debt",
            "pay_debt": "Pay Debt",
            "history": "History",
            "search": "Search",
            "language": "Language",
            "cancel": "Cancel",
            "enter_person_name": "Please send the person's name.",
            "person_added": "{name} added with ID #{id}.",
            "invalid_person_name": "Please send a non-empty name.",
            "duplicate_person_name": '"{name}" already exists. Try another name.',
            "enter_person_prompt": "Select a person using the buttons or send a name/ID.",
            "no_people": "No people found. Add someone first!",
            "enter_debt_amount": "Send the debt amount for {name} (positive number).",
            "enter_debt_description": "Optionally send a description, or type /skip.",
            "enter_payment_description": "Optionally send a description for this payment, or type /skip.",
            "debt_recorded": "Debt recorded: {name} now owes {amount:.2f}. Total balance: {balance:.2f}.",
            "enter_payment_amount": "Send the payment amount for {name} (positive number).",
            "payment_recorded": "Payment recorded. {name}'s balance is now {balance:.2f}.",
            "invalid_number": "Please send a valid positive number.",
            "history_header": "Transaction history for {name}:",
            "history_item_debt": "➕ {amount:.2f} — {description} ({date})",
            "history_item_payment": "➖ {amount:.2f} — {description} ({date})",
            "history_empty": "No transactions recorded yet.",
            "prompt_date_range": "Send a date range as YYYY-MM-DD,YYYY-MM-DD or type /skip.",
            "invalid_date_range": "Invalid date range. Use YYYY-MM-DD,YYYY-MM-DD or /skip.",
            "language_prompt": "Select your preferred language.",
            "language_updated": "Language updated to {language}.",
            "search_prompt": "Send a name or ID to search.",
            "search_results": "Search results:",
            "not_found": "No matching records found.",
            "action_cancelled": "Action cancelled.",
            "confirm_debt": "{name} will be charged {amount:.2f}. Confirm?",
            "confirm_debt_with_description": "{name} will be charged {amount:.2f} for \"{description}\". Confirm?",
            "confirm_payment": "{name} will pay {amount:.2f}. Confirm?",
            "confirm_payment_with_description": "{name} will pay {amount:.2f} for \"{description}\". Confirm?",
            "confirmed": "Confirmed!",
            "dismiss": "Dismiss",
            "back": "Back",
            "dashboard": "Dashboard",
            "dashboard_summary": "📊 Dashboard Summary",
            "total_debt": "Total debt",
            "total_payments": "Total payments",
            "outstanding_balance": "Outstanding balance",
            "top_debtors": "Top debtors",
            "no_debtors": "No outstanding debtors right now.",
            "recent_transactions": "Recent activity",
            "no_transactions": "No recent transactions.",
            "recent_transaction_debt": "➕ {name} — {amount:.2f} on {date} ({description})",
            "recent_transaction_payment": "➖ {name} — {amount:.2f} on {date} ({description})",
            "balance_debtor": "owes {amount:.2f}",
            "balance_creditor": "is owed {amount:.2f}",
            "balance_settled": "settled",
            "search_result_item": "{index}. {name} (#{id}) — {status} • {score}% match",
            "search_suggestions": "No direct matches. Did you mean: {suggestions}?",
            "search_filters_hint": "Tip: Try filters like \"balance>0\", \"debtors\", or an ID.",
        },
    ),
    "fa": LanguagePack(
        code="fa",
        texts={
            "start_message": "به AccountingBot خوش آمدید! یکی از گزینه‌ها را انتخاب کنید.",
            "add_person": "افزودن شخص",
            "add_debt": "ثبت بدهی",
            "pay_debt": "پرداخت بدهی",
            "history": "تاریخچه",
            "search": "جستجو",
            "language": "زبان",
            "cancel": "انصراف",
            "enter_person_name": "نام شخص را ارسال کنید.",
            "person_added": "{name} با شناسه #{id} افزوده شد.",
            "invalid_person_name": "لطفاً نام خالی ارسال نکنید.",
            "duplicate_person_name": '"{name}" از قبل وجود دارد. نام دیگری انتخاب کنید.',
            "enter_person_prompt": "با دکمه‌ها انتخاب کنید یا نام/شناسه را ارسال کنید.",
            "no_people": "هیچ شخصی یافت نشد. ابتدا شخصی اضافه کنید!",
            "enter_debt_amount": "مبلغ بدهی برای {name} را ارسال کنید (عدد مثبت).",
            "enter_debt_description": "در صورت تمایل توضیح بدهید یا /skip را بفرستید.",
            "enter_payment_description": "در صورت تمایل توضیح پرداخت را ارسال کنید یا /skip را وارد کنید.",
            "debt_recorded": "بدهی ثبت شد: {name} اکنون {amount:.2f} بدهی دارد. مانده کل: {balance:.2f}.",
            "enter_payment_amount": "مبلغ پرداخت برای {name} را ارسال کنید (عدد مثبت).",
            "payment_recorded": "پرداخت ثبت شد. مانده {name} اکنون {balance:.2f} است.",
            "invalid_number": "لطفاً یک عدد مثبت معتبر ارسال کنید.",
            "history_header": "تاریخچه تراکنش‌های {name}:",
            "history_item_debt": "➕ {amount:.2f} — {description} ({date})",
            "history_item_payment": "➖ {amount:.2f} — {description} ({date})",
            "history_empty": "هیچ تراکنشی ثبت نشده است.",
            "prompt_date_range": "بازه تاریخ را به صورت YYYY-MM-DD,YYYY-MM-DD ارسال یا /skip را وارد کنید.",
            "invalid_date_range": "بازه تاریخ نامعتبر است. از قالب YYYY-MM-DD,YYYY-MM-DD یا /skip استفاده کنید.",
            "language_prompt": "زبان دلخواه خود را انتخاب کنید.",
            "language_updated": "زبان به {language} تغییر یافت.",
            "search_prompt": "نام یا شناسه را برای جستجو ارسال کنید.",
            "search_results": "نتایج جستجو:",
            "not_found": "موردی پیدا نشد.",
            "action_cancelled": "عملیات لغو شد.",
            "confirm_debt": "{name} به مبلغ {amount:.2f} بدهکار می‌شود. تایید؟",
            "confirm_debt_with_description": "{name} به مبلغ {amount:.2f} برای «{description}» بدهکار می‌شود. تایید؟",
            "confirm_payment": "{name} مبلغ {amount:.2f} پرداخت می‌کند. تایید؟",
            "confirm_payment_with_description": "{name} مبلغ {amount:.2f} برای «{description}» پرداخت می‌کند. تایید؟",
            "confirmed": "تایید شد!",
            "dismiss": "بستن",
            "back": "بازگشت",
            "dashboard": "داشبورد",
            "dashboard_summary": "📊 خلاصه داشبورد",
            "total_debt": "جمع بدهی",
            "total_payments": "جمع پرداخت‌ها",
            "outstanding_balance": "مانده کل",
            "top_debtors": "بیشترین بدهکاران",
            "no_debtors": "بدهکار فعالی وجود ندارد.",
            "recent_transactions": "تراکنش‌های اخیر",
            "no_transactions": "تراکنش اخیر یافت نشد.",
            "recent_transaction_debt": "➕ {name} — {amount:.2f} در تاریخ {date} ({description})",
            "recent_transaction_payment": "➖ {name} — {amount:.2f} در تاریخ {date} ({description})",
            "balance_debtor": "بدهکار {amount:.2f}",
            "balance_creditor": "طلبکار {amount:.2f}",
            "balance_settled": "تسویه شده",
            "search_result_item": "{index}. {name} (#{id}) — {status} • تطابق {score}٪",
            "search_suggestions": "نتیجه‌ای یافت نشد. آیا منظور شما این بود: {suggestions}؟",
            "search_filters_hint": "نکته: از فیلترهایی مانند «balance>0» یا «debtors» یا شناسه استفاده کنید.",
        },
    ),
}


def get_text(key: str, language: str) -> str:
    """Return the localized text for ``key`` and ``language``."""

    pack = _LANGUAGES.get(language, _LANGUAGES["en"])
    return pack.get(key)


def available_languages() -> Dict[str, str]:
    """Return the list of available language codes and human-readable titles."""

    return {"en": "English", "fa": "فارسی"}
