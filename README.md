# AccountingBot on cPanel — friendly setup guide

This guide walks you from a blank cPanel account to a running copy of AccountingBot. It assumes you have never used SSH, GNU Screen, or Python on a hosting provider before. Every step tells you exactly what to click or type, so read slowly and follow the order shown.

If something does not work, take a break, then try the step again. None of these instructions can damage your computer or your hosting account.

---

## 1. Understand what you need

### 1.1 What is cPanel?
A control panel provided by many hosting companies. It shows icons for managing files, email, and applications. We only need a few tools inside it.

### 1.2 Checklist before you start
Make sure you have these items ready:

| Item | Why it matters |
| --- | --- |
| cPanel login (username + password or login link) | Needed to open the dashboard. |
| Telegram bot token | Required so AccountingBot can log in to Telegram. Create one with [BotFather](https://t.me/BotFather) → `/start` → follow prompts. |
| SSH/Terminal access and the **Setup Python App** feature | Some hosting plans hide them. Ask hosting support to enable both features. |

Also note the following details—you will use them later:

| Information to copy | Where it is used |
| --- | --- |
| Your cPanel username | Part of many file paths. |
| Server hostname (for example `server42.host.com`) | Needed when you use SSH or Terminal. |
| Telegram bot token | Stored as an environment variable. |

### 1.3 Words you will see
* **Application root** – the folder that holds your app. We will call it `accountingbot`.
* **Virtual environment** – an isolated place where Python packages are installed.
* **Terminal / SSH** – a black command window where you type commands that start with `$` or end with `>`.

---

## 2. Create the Python application shell in cPanel
1. Log in to cPanel.
2. Scroll to **Software** → click **Setup Python App**.
3. Click **Create Application**.
4. Fill in the form:
   * **Python version**: choose the newest option that is 3.10 or higher.
   * **Application root**: type `accountingbot` (or another name—just use the same name everywhere else in this guide).
   * Leave **Application URL**, **Startup file**, and **Application startup command** empty.
5. Click **Create**.

Keep this browser tab open. On the summary page, copy the **Virtual environment** line (looks like `/home/YOURUSERNAME/virtualenv/accountingbot/3.10/bin/activate`). You will use it later.

---

## 3. Open the Terminal inside cPanel
1. Return to the cPanel home page.
2. Click **Terminal** (search for "terminal" if you do not see it).
3. A black window opens with a prompt like `YOURUSERNAME@server [~]$`.

Helpful tips:
* If Terminal is disabled, ask your hosting provider to enable SSH.
* Paste commands with `Ctrl + Shift + V` (Windows/Linux) or `Cmd + V` (macOS).

---

## 4. Download AccountingBot
We will pull the files from GitHub straight into the folder created in step 2.

In the Terminal, run each line separately and press **Enter** after every line:

```bash
cd ~/accountingbot
rm -rf -- * .* 2>/dev/null || true
git clone https://github.com/ManiTheFerfriGuy/AccountingBot.git .
```

What the commands do:
* `cd` moves you into the application root folder.
* `rm -rf ...` empties the folder. It is safe—if the folder is already empty, nothing happens.
* `git clone ... .` copies the GitHub repository into the current folder.

When the clone finishes, run `ls`. You should see `requirements.txt`, `start.py`, and an `accountingbot` folder. If Git is not installed, you can upload the ZIP from GitHub through **File Manager** → `accountingbot` folder → **Upload** → **Extract**.

---

## 5. Activate the virtual environment and install packages
1. Copy the **Virtual environment** path from the Setup Python App page (step 2).
2. In the Terminal, activate it. Example command:

   ```bash
   source /home/YOURUSERNAME/virtualenv/accountingbot/3.10/bin/activate
   ```

   You know it worked when the prompt starts with `(accountingbot:3.10)` or similar. Many people combine the activation and directory change in one command:

   ```bash
   source /home/YOURUSERNAME/virtualenv/accountingbot/3.10/bin/activate && cd ~/accountingbot
   ```

3. Install dependencies:

   ```bash
   pip install --upgrade pip
   pip install -r requirements.txt
   ```

   Installation can take a few minutes. If you see red error text, read it carefully, fix the issue, then run the command again.

Keep the Terminal open—you will need it again. Every new Terminal session requires you to rerun the `source .../activate` command.

---

## 6. Add secrets and configuration
### 6.1 Store secrets in cPanel
1. Go back to the **Setup Python App** tab. Click **Edit** if the page is not already editable.
2. In the **Environment variables** section, click **Add Variable** for each item below. Names are case-sensitive.

| Name | Value |
| --- | --- |
| `BOT_TOKEN` | Paste the Telegram bot token from BotFather. Required. |
| `DATABASE_PATH` | `accounting.db` (or another filename). |
| `LOG_FILE` | `accounting_bot.log` (or a custom log file). |
| `CPANEL_HOST` | Optional. Only needed if you plan to use helpers in `accountingbot/cpanel.py`. |
| `CPANEL_USERNAME` | Optional. Used with `CPANEL_HOST`. |
| `CPANEL_API_TOKEN` | Optional. Used with `CPANEL_HOST`. |
| `CPANEL_VERIFY_SSL` | `true` (set to `false` only if hosting support tells you to). |
| `DB_BACKUP_ENABLED` | `true`. Set to `false` if you want to turn off automatic database backups. |
| `DB_BACKUP_DIR` | `Database_Backups`. Change it if you prefer another folder name for backups. |
| `DB_BACKUP_COMPRESS_AFTER_DAYS` | `7`. After this many days old backups are zipped. Leave blank to keep the raw `.db` files. |
| `DB_BACKUP_RETENTION_LIMIT` | `30`. The bot keeps this many backups (including `.zip` files) and removes the oldest extras. Leave blank for unlimited. |

3. Save the environment variables (some themes save automatically when you leave the field).

### 6.2 Optional: `secrets.json` for local testing
If you want a local copy of the secrets, create `secrets.json` next to `start.py` with content similar to:

```json
{
  "BOT_TOKEN": "123456:ABC-DEF",
  "DATABASE_PATH": "accounting.db",
  "LOG_FILE": "accounting_bot.log"
}
```

Upload it through **File Manager** if you want the server to use it. Environment variables always take priority over the file.

### 6.3 Automatic database backups (optional but recommended)

AccountingBot quietly creates a fresh copy of your database each time you add or edit information. By default it:

1. Stores backups inside `~/accountingbot/Database_Backups/`.
2. Compresses backups older than 7 days into `.zip` files to save space.
3. Keeps the 30 most recent backups and deletes older ones.

You can change these numbers with the `DB_BACKUP_*` environment variables listed above. A simple routine is:

* Use **File Manager** or SFTP to download the newest backup when you want an extra copy.
* Delete old `.zip` files only after you are sure you have the data you need.

If you do not want any automatic backups, set `DB_BACKUP_ENABLED` to `false` and save the change in cPanel.

---

## 7. Make sure the startup script exists
The repository already includes `start.py`. If it is missing for any reason, recreate it:

```bash
cat > start.py <<'PY'
import subprocess
import sys
from pathlib import Path

from accountingbot.secrets import load_secrets

BASE_DIR = Path(__file__).resolve().parent
load_secrets(BASE_DIR / "secrets.json")

if __name__ == "__main__":
    subprocess.run([sys.executable, "-m", "accountingbot.bot"], check=True)
PY
```

Run `cat start.py` to verify the file matches the content above.

---

## 8. Run AccountingBot with GNU Screen
`screen` keeps programs alive when you close the Terminal tab.

1. Activate the virtual environment and switch to the project folder if you have not already:

   ```bash
   source /home/YOURUSERNAME/virtualenv/accountingbot/3.10/bin/activate
   cd ~/accountingbot
   ```

2. Start (or reconnect to) a session named `accountingbot`:

   ```bash
   screen -S accountingbot -R
   ```

   If `screen` is missing, ask hosting support to enable it.

3. Inside the screen session, start the bot:

   ```bash
   python -m accountingbot.bot
   ```

4. Leave the session running by pressing `Ctrl + A`, then `D` (detach). The bot continues in the background.
5. Reconnect later by repeating steps 1–2.
6. To stop the bot, reattach to the session and press `Ctrl + C`. Type `exit` to close screen completely.

Tips:
* After pulling new code or installing packages, stop the bot (`Ctrl + C`), run the updates, then start it again inside the same screen session.
* You can create other screen sessions for different projects by changing the session name in the command.

---

## 9. Confirm that the bot is working
1. Reattach to the screen session to watch the live output:

   ```bash
   screen -r accountingbot
   ```

   Detach again with `Ctrl + A`, then `D` when you are done.

2. Watch the log file from another Terminal tab if you prefer a quieter view:

   ```bash
   tail -f accounting_bot.log
   ```

   Press `Ctrl + C` to stop watching.

3. Send a message to your Telegram bot. If it replies, the deployment is working.

---

## 9.1 Learn the basic bot buttons
Open your bot in Telegram and tap **Start** to see the main menu. Every button also has a matching slash command if you prefer typing.

| Button / command | What it does |
| --- | --- |
| **➕ Add Contact** (`/add_person`) | Save a new person and get their numeric ID. |
| **🧾 Log a Charge** (`/add_debt`) | Record money someone now owes you. You can type `ID amount description` in one line (for example `42 150 Lunch with client`). |
| **💳 Record a Payment** (`/record_payment`) | Track money that was paid back. The quick format `42 75 Refund` works here too. Use `#` before the ID if you like (for example `#42`). |
| **📜 View History** (`/history`) | Browse every charge and payment for one contact. Optionally type a date range like `2024-01-01,2024-01-31`. |
| **📊 Dashboard** (`/dashboard`) | See totals, top balances, and the latest activity. |
| **👥 All Contacts** (`/people`) | Print the full contact list in chat. |
| **✏️ Edit/Delete Contact** (`/manage_contact`) | Rename a contact or delete them (including their history). |
| **🌐 Change Language** (`/language`) | Switch between English and فارسی. |
| **📁 Export Transactions** (`/export`) | Download a CSV file. Pick whether you want all activity, only charges, only payments, or a single contact. |

At any time you can send `/cancel` or tap ✖️ Cancel to stop the current flow.

---

## 10. Routine maintenance
* **Restart after updates**: attach to screen, press `Ctrl + C`, run any updates, then start the bot with `python -m accountingbot.bot`.
* **Update the code**: with the virtual environment active, run `git pull` followed by `pip install -r requirements.txt`.
* **Back up data**: download the file defined by `DATABASE_PATH` (default `accounting.db`) using cPanel **File Manager** or SFTP.
* **Protect secrets**: never share the values of `BOT_TOKEN`, `CPANEL_API_TOKEN`, or other sensitive variables.

---

## 11. Troubleshooting quick reference
| Problem | What to try |
| --- | --- |
| Terminal says “No such file or directory” | Check your spelling, especially the username in the path. Run `pwd` to see the current folder. |
| `source .../activate` fails | Copy the exact path from the Setup Python App screen again. |
| `pip` cannot connect to the internet | Wait and retry. If it keeps failing, ask your hosting provider whether outbound connections are blocked. |
| Bot does not answer in Telegram | Open **Setup Python App** → **View Logs** to check for errors. Confirm the `BOT_TOKEN` value. |
| Terminal closed unexpectedly | Reopen Terminal, re-run `source .../activate`, then reattach to screen. |
| Permission errors writing logs or database | Make sure `LOG_FILE` and `DATABASE_PATH` point to locations inside your home directory. |
| SSL errors when calling Telegram or cPanel | Confirm the server allows HTTPS. If your host uses a self-signed certificate, temporarily set `CPANEL_VERIFY_SSL=false` while you install a trusted certificate. |

If you encounter a different issue, copy the exact error message and share it with your hosting support or the AccountingBot maintainers. Exact wording helps others diagnose the problem.

---

## 12. Recap
You now have:
1. A Python application created through cPanel.
2. AccountingBot files cloned into the application root.
3. Dependencies installed inside a virtual environment.
4. Secrets stored as environment variables (and optional `secrets.json`).
5. A screen session running `python -m accountingbot.bot`.

Keep this guide bookmarked so you can repeat or adjust any step later. For Telegram-specific questions (webhook bans, rate limits, etc.), review the [Telegram Bot API FAQ](https://core.telegram.org/bots/faq).
