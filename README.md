# AccountingBot

AccountingBot is a multilingual Telegram bot that helps you track people, debts, and payments while remaining friendly to cPanel deployments. The bot stores data in SQLite, presents an organized keyboard-driven interface, and can optionally reach cPanel's UAPI for remote automation.

## Feature highlights
- People registry with automatic numeric IDs and name/ID search.
- Debt and repayment workflows that confirm actions before writing to the database.
- Transaction history browser with optional date filtering.
- English/Persian language toggle for the entire interface.
- Inline and reply keyboards that keep the chat experience tidy.
- SQLite storage with WAL mode so the bot remains lightweight and portable.
- Structured logging to stdout and a log file for auditing purposes.
- cPanel UAPI client (Optional) for running remote administrative actions.

## Step-by-step setup

1. **Install prerequisites**
   ```bash
   python3 --version
   sudo apt-get update
   sudo apt-get install -y python3 python3-venv python3-pip git
   ```

2. **Clone the repository**
   ```bash
   git clone https://github.com/your-org/AccountingBot.git
   cd AccountingBot
   ```

3. **Create and activate a virtual environment**
   ```bash
   python3 -m venv .venv
   source .venv/bin/activate
   ```

4. **Install Python dependencies**
   ```bash
   pip install --upgrade pip
   pip install -r requirements.txt
   ```

5. **Configure environment variables**
   ```bash
   cat > .env <<'ENV'
   BOT_TOKEN=replace-with-your-telegram-token
   DATABASE_PATH=accounting.db
   LOG_FILE=accounting_bot.log
   CPANEL_HOST=
   CPANEL_USERNAME=
   CPANEL_API_TOKEN=
   CPANEL_VERIFY_SSL=true
   ENV
   ```
   - `BOT_TOKEN` is required and must match the token issued by BotFather.
   - `DATABASE_PATH`, `LOG_FILE`, `CPANEL_HOST`, `CPANEL_USERNAME`, `CPANEL_API_TOKEN`, and `CPANEL_VERIFY_SSL` are optional overrides. Leave them blank or remove them if you do not need them.

6. **Load configuration before running commands**
   ```bash
   export $(grep -v '^#' .env | xargs)
   ```
   Use the command above in every new shell session or rely on tooling such as `direnv` to load the `.env` file automatically.

7. **Run the bot**
   ```bash
   python -m accountingbot.bot
   ```
   The bot will perform long polling, create the SQLite database if it is missing, and write logs both to stdout and to the file defined by `LOG_FILE`.

## Operational notes

- **Database schema** – The bot automatically manages three tables: `people`, `transactions`, and `user_settings`. Positive transaction amounts represent debts; negative amounts represent repayments.
- **Logging** – Rotate or truncate the file pointed to by `LOG_FILE` if it grows large. Each significant action (people, debts, payments, and cPanel calls) is recorded.
- **Security** – Keep `.env` outside of version control, rotate your Telegram bot token periodically, and update dependencies with `pip install --upgrade -r requirements.txt`.

## Optional tasks

- **Optional: Inspect the database**
  ```bash
  sqlite3 "$DATABASE_PATH" ".tables"
  sqlite3 "$DATABASE_PATH" "SELECT * FROM people LIMIT 5;"
  ```

- **Optional: Run the bot as a background process**
  ```bash
  nohup python -m accountingbot.bot > bot.log 2>&1 &
  tail -f bot.log
  ```

- **Optional: Configure cPanel integration**
  ```bash
  export CPANEL_HOST=your-cpanel-host
  export CPANEL_USERNAME=your-user
  export CPANEL_API_TOKEN=your-api-token
  export CPANEL_VERIFY_SSL=true
  ```
  With those values exported (or saved in `.env`), the helper located at `accountingbot/cpanel.py` can invoke UAPI endpoints for tasks such as remote backups.

- **Optional: Update translations**
  ```bash
  nano accountingbot/localization.py
  ```
  Edit the dictionaries inside the file to adjust English or Persian responses. Restart the bot after saving your changes.

## Development tips
- Keep one terminal running the bot and another for editing files.
- Use `/cancel` in Telegram to exit the current workflow at any time.
- Commit changes with clear messages when modifying handlers or localization.

