# AccountingBot cPanel Deployment Guide

This guide walks you through hosting AccountingBot on a cPanel server. It assumes your hosting plan provides SSH access and the **Setup Python App** feature (also called Application Manager). If any option mentioned below is missing, ask your hosting provider to enable it before continuing.

## 1. Create the Python application
1. Log in to cPanel and open **Setup Python App**.
2. Click **Create Application** and choose:
   - **Python version:** Python 3.10 or newer.
   - **Application root:** `accountingbot` (or any empty folder).
   - **Application URL:** leave blank because AccountingBot is a Telegram bot.
   - **Startup file / WSGI file:** leave the default for now.
3. Click **Create**. cPanel provisions a virtual environment and shows the path in the **Application Root** card. Copy the **Virtual environment** path; you will use it in the next steps.

## 2. Upload the project
You can use either the cPanel Terminal, SSH, or Git Version Control. One simple option is the Terminal:
```bash
cd ~/accountingbot        # replace with the Application Root you selected
rm -rf *
git clone https://github.com/your-org/AccountingBot.git .
```
If Git is unavailable, upload the repository ZIP through **File Manager** and extract it inside the Application Root.

## 3. Install dependencies
Still inside the Application Root, activate the cPanel-created virtual environment and install packages:
```bash
source ~/virtualenv/accountingbot/3.10/bin/activate  # adjust path & version shown in cPanel
pip install --upgrade pip
pip install -r requirements.txt
```
When you are done, keep the environment path handy; you will need it whenever you run manual commands.

## 4. Configure environment variables
Create a `.env` file in the Application Root:
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
- `BOT_TOKEN` is required. Obtain it from @BotFather.
- Leave the cPanel-related values empty unless you plan to use the optional UAPI helpers.
- Keep `.env` out of version control.

## 5. Tell cPanel how to start the bot
1. Return to **Setup Python App** and click **Edit** on the application you created.
2. In **Startup file**, enter `start.py`.
3. Create `start.py` in the Application Root with the following content:
   ```python
   import os
   from pathlib import Path

   from dotenv import load_dotenv

   BASE_DIR = Path(__file__).resolve().parent
   load_dotenv(BASE_DIR / ".env")

   if __name__ == "__main__":
       os.system("python -m accountingbot.bot")
   ```
4. Set **Application startup command** to `python start.py`.
5. Click **Save** and then **Restart** to make cPanel run the bot.

cPanel will now execute `python start.py` through Passenger whenever it detects the application needs to restart (for example, after saving changes or clicking **Restart**).

## 6. Manage the running bot
- **Restarting:** Use the **Restart** button in **Setup Python App** after editing files or updating dependencies.
- **Viewing logs:** Click **View Logs** inside **Setup Python App** or open `~/accountingbot/accounting_bot.log` via SSH.
- **Updating code:** Pull the latest changes with `git pull` (or upload new files) inside the Application Root, reinstall dependencies if `requirements.txt` changed, and restart the app.
- **Database location:** The SQLite database lives at `DATABASE_PATH`. Download it via SFTP for backups, or connect with `sqlite3 accounting.db` from SSH while the virtual environment is active.

## 7. Optional cPanel automation
If you set the `CPANEL_HOST`, `CPANEL_USERNAME`, and `CPANEL_API_TOKEN` variables, the bot's `accountingbot/cpanel.py` module can call UAPI endpoints. Ensure the API token has only the permissions you need and keep it secret.

---
Need help later? Re-open **Setup Python App** to review or adjust the configuration. For Telegram-specific issues, ensure your server can reach api.telegram.org over HTTPS.
