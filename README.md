# AccountingBot

AccountingBot is a multilingual Telegram bot for managing personal accounting workflows with cPanel-friendly deployment. It allows you to register people, track debts and payments, inspect transaction histories, and optionally interact with cPanel's UAPI for remote actions.

## Features

- **People management** – add people with automatically generated numeric IDs and search by name or ID.
- **Debt tracking** – record new debts with optional descriptions and receive confirmations before saving.
- **Payment tracking** – register repayments, automatically updating the running balance.
- **History browser** – list transactions for a person with optional date-range filtering.
- **Smart search** – search people globally or while inside a workflow using inline buttons.
- **Language toggle** – switch the full interface between English and Persian at any time.
- **Glassy UI** – organized reply keyboard for the main menu and inline buttons for confirmations and selections.
- **SQLite storage** – lightweight, file-based database with WAL mode for concurrency.
- **Logging & security** – structured logging to both file and stdout, parameterized SQL queries, and secrets pulled from environment variables.
- **cPanel integration** – optional cPanel API client for invoking UAPI endpoints (e.g., remote backups) using secure tokens.

## Quick start

1. **Install dependencies**

   ```bash
   python -m venv .venv
   source .venv/bin/activate
   pip install -r requirements.txt
   ```

2. **Configure environment variables** (create a `.env` file or export variables directly):

   | Variable | Description |
   | --- | --- |
   | `BOT_TOKEN` | Telegram bot token issued by BotFather (required). |
   | `DATABASE_PATH` | Optional path to the SQLite database file (defaults to `accounting.db`). |
   | `LOG_FILE` | Optional log file location (defaults to `accounting_bot.log`). |
   | `CPANEL_HOST` | cPanel hostname (without protocol) if you need API access. |
   | `CPANEL_USERNAME` | cPanel username for UAPI requests. |
   | `CPANEL_API_TOKEN` | API token generated in cPanel for authentication. |
   | `CPANEL_VERIFY_SSL` | Set to `false` to skip SSL verification (not recommended). |

3. **Run the bot**

   ```bash
   python -m accountingbot.bot
   ```

The bot uses long polling by default. Deploying on cPanel typically involves creating a Python application, placing the project files in your cPanel account, and configuring the environment variables via the cPanel interface.

## Database schema

The SQLite database automatically creates the following tables:

- `people(id INTEGER PRIMARY KEY, name TEXT UNIQUE, created_at TEXT)`
- `transactions(id INTEGER PRIMARY KEY, person_id INTEGER, amount REAL, description TEXT, created_at TEXT)`
- `user_settings(user_id INTEGER PRIMARY KEY, language TEXT, updated_at TEXT)`

Positive transaction amounts represent debts, whereas negative amounts represent payments.

## cPanel integration

The optional `CPanelClient` wrapper (`accountingbot/cpanel.py`) enables calling cPanel's UAPI endpoints using environment-driven credentials. You can extend it to push database backups or trigger other administrative jobs once your credentials are configured.

## Logging

Logs are written both to `LOG_FILE` and to stdout. Important actions such as adding people, recording transactions, and cPanel interactions are captured for auditing. Rotate or archive the log file as needed for your deployment.

## Security notes

- Keep your Telegram bot token and cPanel credentials secret; rely on environment variables or cPanel's configuration UI instead of hardcoding them.
- SQLite queries are parameterized to mitigate injection attacks.
- Update dependencies regularly to receive security fixes.

## Development tips

- Run the bot in a separate terminal when actively developing handlers.
- Use the reply keyboard to quickly navigate between workflows.
- Press `/cancel` at any time to abort the current action and reset the temporary state.

