# AccountingBot on cPanel — ultra-beginner guide

This document teaches you how to put AccountingBot on a cPanel hosting account. It is written for people who have never used cPanel, never installed a Python app, and may not be familiar with command lines. Follow each step in order. Do not worry if you need to re-read a step—many people do when they are new.

If you get stuck, take a short break, then try again slowly. Nothing in these instructions can break your computer.

---

## 0. Get ready

### What is cPanel?
cPanel is the dashboard your hosting company gives you. It has many icons that let you manage files, create email addresses, and so on. We will only use a few of those icons.

### Things you must have before you start

1. **cPanel login** – a username and password (or a login link) from your hosting provider.
2. **A Telegram bot token** – if you do not have one, open the [BotFather chat](https://t.me/BotFather) in Telegram, type `/start`, and follow the steps to create a new bot. BotFather will give you a token that looks like a long string of letters and numbers. Keep that window open.
3. **SSH access and the “Setup Python App” feature** – some hosting plans hide these options. If you cannot find an icon named **Terminal**, **SSH Access**, or **Setup Python App**, contact your hosting support and ask them to enable these features for your account.

Write down or copy the following information—you will need it later:

| Item | Why you need it |
| ---- | ---------------- |
| Your cPanel username | Used in file paths and when logging in. |
| The server name (for example `server.example.com`) | Needed for SSH. Your host can tell you this value. |
| The Telegram bot token | Needed so the bot can log in to Telegram. |

### Words you will see

* **Application root** – a special folder where cPanel stores your program. We will call it `accountingbot`.
* **Virtual environment** – a private place where Python keeps the packages for your app. cPanel creates it for you.
* **Terminal / SSH** – a text window where you type commands. It looks scary, but we will tell you exactly what to type.

---

## 1. Create the empty Python app in cPanel

1. Log in to cPanel.
2. Scroll until you see the **Software** section and click **Setup Python App**.
3. Click the **Create Application** button.
4. Fill out the form:
   * **Python version** – pick the newest option that is 3.10 or higher.
   * **Application root** – type `accountingbot`. (If you choose a different name, use that same name in every step.)
   * Leave **Application URL**, **Startup file**, and **Application startup command** empty for now.
5. Click **Create**.

After a few seconds, cPanel shows a page with many lines. Look for **Virtual environment**. It will look similar to `/home/YOURUSERNAME/virtualenv/accountingbot/3.10/bin/activate`. Leave this browser tab open—you will copy information from it later.

---

## 2. Open the Terminal (command window)

1. Go back to the cPanel home page.
2. Find and click **Terminal**. (If you cannot see it, click the search bar at the top and type “terminal”.)
3. A black window opens. It shows a prompt similar to `YOURUSERNAME@server [~]$`. This is where you type commands.

Tips:

* If Terminal is disabled, ask your hosting support to enable SSH/Terminal access.
* You can copy commands from this guide and paste them into the Terminal. On Windows use `Ctrl + Shift + V`, on macOS use `Cmd + V`.

---

## 3. Download the AccountingBot files into cPanel

We will copy the bot from GitHub directly into the folder cPanel created.

1. In the Terminal, type the following commands one line at a time. Press **Enter** after each line. Replace `YOURUSERNAME` with your actual cPanel username.

   ```bash
   cd ~/accountingbot
   rm -rf -- * .* 2>/dev/null || true
   git clone https://github.com/your-org/AccountingBot.git .
   ```

   Explanation:

   * `cd ~/accountingbot` moves you into the Application root folder.
   * `rm -rf -- * .* ...` cleans the folder. If the folder is already empty, the command silently does nothing.
   * `git clone ... .` downloads the project files from GitHub.

2. When the command finishes, type `ls` and press **Enter**. You should see files named `README.md`, `requirements.txt`, and a folder named `accountingbot`. If you see an error about “git: command not found”, contact your host—they must install Git or you will need to upload the files as a ZIP through the File Manager instead.

ZIP alternative:

* On your own computer, visit the GitHub page for AccountingBot and download the ZIP.
* In cPanel, open **File Manager**, browse to the `accountingbot` folder, click **Upload**, choose the ZIP, then click **Extract**. Make sure the extracted files (for example `requirements.txt`) are inside `accountingbot`, not inside another subfolder.

---

## 4. Activate the Python environment and install packages

1. Keep the Terminal open.
2. Copy the **Virtual environment** path from the cPanel tab you left open earlier. It ends with `/bin/activate`.
3. In the Terminal, type the following line, but replace the path with your real one:

   ```bash
   source /home/YOURUSERNAME/virtualenv/accountingbot/3.10/bin/activate
   ```

   After pressing **Enter**, the prompt changes and shows something like `(accountingbot:3.10)` at the beginning. This means the environment is active.

4. Install the Python packages by running:

   ```bash
   pip install --upgrade pip
   pip install -r requirements.txt
   ```

   Wait until the installation finishes. It can take a few minutes. If you see red error text, read it carefully. Common issues include typos in the path or internet outages. Run the command again after fixing the problem.

5. Keep the Terminal window open—you will use it again soon. Each time you open a fresh Terminal session, you must run the `source .../activate` command again before using `pip` or running the bot.

---

## 5. Create the settings file (`.env`)

1. You are still in the Terminal with the virtual environment active.
2. Type the following block exactly as written, then press **Enter** after the final `ENV` line. Replace the placeholder values with your information.

   ```bash
   cat > .env <<'ENV'
   BOT_TOKEN=PASTE-YOUR-TELEGRAM-TOKEN-HERE
   DATABASE_PATH=accounting.db
   LOG_FILE=accounting_bot.log
   CPANEL_HOST=
   CPANEL_USERNAME=
   CPANEL_API_TOKEN=
   CPANEL_VERIFY_SSL=true
   ENV
   ```

3. Check that the file was created by running `cat .env`. If you make a mistake, run the block again.

What the options mean:

* `BOT_TOKEN` – required. Without it the bot cannot talk to Telegram.
* `DATABASE_PATH` – the file where AccountingBot stores information. You can keep the default.
* `LOG_FILE` – where the bot writes messages about what it is doing. Keep the default unless you have a reason to change it.
* `CPANEL_HOST`, `CPANEL_USERNAME`, `CPANEL_API_TOKEN` – optional. Leave them empty unless you plan to use the advanced automation helpers provided in `accountingbot/cpanel.py`.
* `CPANEL_VERIFY_SSL` – keep `true` unless your host tells you to set it to `false`.

Security tip: Never upload `.env` to public places. It holds secrets.

---

## 6. Create the startup script that runs the bot

1. Still in the Terminal, run the command below to create a new file named `start.py`:

   ```bash
   cat > start.py <<'PY'
   import subprocess
   import sys
   from pathlib import Path

   from dotenv import load_dotenv

   BASE_DIR = Path(__file__).resolve().parent
   load_dotenv(BASE_DIR / ".env")

   if __name__ == "__main__":
       subprocess.run([sys.executable, "-m", "accountingbot.bot"], check=True)
   PY
   ```

2. Confirm the file exists by typing `cat start.py`.

---

## 7. Tell cPanel how to start the bot

1. Go back to the **Setup Python App** tab.
2. Click the **Edit** button on your application.
3. Fill in the fields:
   * **Startup file** – type `start.py`.
   * **Application startup command** – type the same Python path you used earlier, followed by a space and `start.py`. Example:

     ```
     /home/YOURUSERNAME/virtualenv/accountingbot/3.10/bin/python start.py
     ```

4. Click **Save**.
5. After saving, click **Restart**. cPanel will now run the bot. Whenever you change the code or the `.env` file, click **Restart** again.

---

## 8. Check that the bot is running

1. In the **Setup Python App** screen, click **View Logs**. Scroll to the bottom. You should see lines showing that the bot started without errors. If you see an error, read the message and compare it with the previous steps to find the issue.
2. Still in the Terminal, you can watch the bot log file by running:

   ```bash
   tail -f accounting_bot.log
   ```

   Press `Ctrl + C` to stop watching.
3. Open Telegram and send a message to your bot. If the bot replies, everything is working.

---

## 9. Everyday tasks

* **Restart after updates** – whenever you change files or install packages, click **Restart** inside **Setup Python App**.
* **Update the code** – in the Terminal (with the virtual environment active), run `git pull`. Then run `pip install -r requirements.txt` again in case new packages were added. Finally, restart the app.
* **Back up your data** – the bot stores its database in the file named in `DATABASE_PATH` (default `accounting.db`). Download it from **File Manager** or via SFTP to keep a backup.
* **Keep secrets safe** – never share the `.env` file or the Telegram bot token.

---

## 10. Troubleshooting

| Problem | What to try |
| ------- | ----------- |
| The Terminal command says “No such file or directory” | Check for typos, especially in your username. Run `pwd` to see which folder you are in. |
| `source .../activate` fails | Make sure you copied the **exact** path from the Setup Python App screen. |
| `pip` errors about the internet | Wait a minute and run the command again. If it still fails, contact your host—they might block outgoing connections. |
| Bot does not answer in Telegram | Re-open **Setup Python App**, click **View Logs**, and look for error messages. Double-check the `BOT_TOKEN` in `.env`. |
| You closed the Terminal window | Open it again and repeat the `source .../activate` step before running any Python commands. |

If a problem is not listed, copy the exact error message and share it with someone who can help (for example, your hosting support or the AccountingBot maintainers). Exact words matter—avoid summarizing.

---

You did it! Deploying a Python bot on cPanel is a big achievement, especially if this was your first time working with these tools. Keep this guide handy for future updates.
* **Permission issues writing logs or databases:** Ensure the paths used for `LOG_FILE` and `DATABASE_PATH` are inside your home directory or otherwise writable by the cPanel user.
* **SSL errors when calling Telegram or cPanel:** Verify that the server has outbound HTTPS access. For self-signed cPanel certificates, set `CPANEL_VERIFY_SSL=false` temporarily while you install a trusted certificate.

---

Need to revisit configuration later? Re-open **Setup Python App** at any time to review the settings, restart the process, or adjust the Python version. For Telegram-specific problems (webhook bans, rate limits, etc.), check the official [Telegram Bot API FAQ](https://core.telegram.org/bots/faq) once you have confirmed the application is running.
