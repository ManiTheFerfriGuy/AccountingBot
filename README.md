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

1. In the Terminal, type the following commands one line at a time. Press **Enter** after each line.

   ```bash
   cd ~/accountingbot
   rm -rf -- * .* 2>/dev/null || true
   git clone https://github.com/ManiTheFerfriGuy/AccountingBot.git .
   ```

   Explanation:

   * `cd ~/accountingbot` moves you into the Application root folder.
   * `rm -rf -- * .* ...` cleans the folder. If the folder is already empty, the command silently does nothing.
   * `git clone ... .` downloads the project files from GitHub.

   If you are using a different fork or GitHub username, replace `ManiTheFerfriGuy` with the owner of your repository.

2. When the command finishes, type `ls` and press **Enter**. You should see files named `README.md`, `requirements.txt`, and a folder named `accountingbot`. If you see an error about “git: command not found”, contact your host—they must install Git or you will need to upload the files as a ZIP through the File Manager instead.

ZIP alternative:

* On your own computer, visit the GitHub page for AccountingBot and download the ZIP.
* In cPanel, open **File Manager**, browse to the `accountingbot` folder, click **Upload**, choose the ZIP, then click **Extract**. Make sure the extracted files (for example `requirements.txt`) are inside `accountingbot`, not inside another subfolder.

---

## 4. Activate the Python environment and install packages

1. Keep the Terminal open.
2. Copy the **Virtual environment** path from the cPanel tab you left open earlier. It ends with `/bin/activate`.
3. In the Terminal, type the activation command shown in cPanel. For example, if cPanel shows the path
   `/home/kingserv/virtualenv/accountingbot/3.10/bin/activate`, you would run:

   ```bash
   source /home/kingserv/virtualenv/accountingbot/3.10/bin/activate
   ```

   After pressing **Enter**, the prompt changes and shows something like `(accountingbot:3.10)` at the beginning. This means the environment is active. If your username is different, replace `kingserv` with your own value. Many people like to chain the activation and directory change together, for example:

   ```bash
   source /home/kingserv/virtualenv/accountingbot/3.10/bin/activate && cd /home/kingserv/accountingbot
   ```

4. Install the Python packages by running:

   ```bash
   pip install --upgrade pip
   pip install -r requirements.txt
   ```

   Wait until the installation finishes. It can take a few minutes. If you see red error text, read it carefully. Common issues include typos in the path or internet outages. Run the command again after fixing the problem.

5. Keep the Terminal window open—you will use it again soon. Each time you open a fresh Terminal session, you must run the `source .../activate` command again before using `pip` or running the bot.

---

## 5. Add your secrets

AccountingBot needs a few secrets such as the Telegram bot token. Store them directly in cPanel so they are available to the application.

### Store secrets in cPanel

1. Go back to the browser tab that shows the **Setup Python App** page for your application. Click **Edit** if the page is not already in edit mode.
2. Scroll to the **Environment variables** section and click **Add Variable** for each item below. Type the name exactly as shown in the left column and put your value on the right:

   | Name | Value to enter |
   | ---- | -------------- |
   | `BOT_TOKEN` | Paste your Telegram bot token. This value is required or the bot will not start. |
   | `DATABASE_PATH` | `accounting.db` (or another filename if you want the database stored elsewhere). |
   | `LOG_FILE` | `accounting_bot.log` unless you prefer a different log filename. |
   | `CPANEL_HOST` | *(Optional)* Only needed if you will use the advanced helpers in `accountingbot/cpanel.py`. |
   | `CPANEL_USERNAME` | *(Optional)* Only needed with the helpers above. |
   | `CPANEL_API_TOKEN` | *(Optional)* Only needed with the helpers above. |
   | `CPANEL_VERIFY_SSL` | `true` (set to `false` only if your host’s support team tells you to). |

3. Click **Save** in the Environment variables box (some themes save automatically when you leave the field). Your values are now stored securely by cPanel.

### Mirror the secrets into a `.env` file for SSH/tmux sessions

When you start the bot from SSH (including inside `tmux`), the environment variables defined in the cPanel UI are **not** loaded into your shell automatically. Create a `.env` file in your project folder so the local commands can read the same secrets:

```bash
cat > .env <<'ENV'
BOT_TOKEN=PASTE_THE_SAME_VALUE_USED_IN_CPANEL
DATABASE_PATH=accounting.db
LOG_FILE=accounting_bot.log
# Add any other variables you defined in the cPanel UI.
ENV
```

Replace the placeholders with your real values. Update both cPanel and `.env` any time you change a secret so they stay in sync.

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

The script loads any values from `.env` before launching the bot so your secrets are available whether the process is started by cPanel or from a manual SSH session.

---

## 7. Run the bot with `tmux` (skip the cPanel restart button entirely)

`tmux` lets the bot keep running even if you close the Terminal tab. Follow these steps every time you want to start the bot:

1. Open the Terminal, activate your virtual environment (from Step 4), and move into the project folder:

   ```bash
   source /home/YOURUSERNAME/virtualenv/accountingbot/3.10/bin/activate
   cd ~/accountingbot
   ```

   Replace `YOURUSERNAME` and the Python version with the values shown in your cPanel dashboard.

2. Start (or reattach to) a session named `accountingbot` in one command:

   ```bash
   tmux new -As accountingbot
   ```

   *If `tmux` is not available, contact your hosting provider and ask them to enable it.*

3. When the green status bar appears, run the bot inside the session:

   ```bash
   python start.py
   ```

   This command loads the `.env` file (so the secrets are available) and then starts `accountingbot.bot`. Leave it running—you will see log messages as the bot starts.

4. Detach without stopping the bot by pressing `Ctrl + B`, then `D`. The bot keeps running in the background.

5. To check on the bot later, repeat step 1, then run `tmux new -As accountingbot` again. `tmux` automatically reattaches you to the existing session.

6. To stop the bot, reattach to the session and press `Ctrl + C`. When the Python process ends, type `exit` to close the `tmux` session if you no longer need it.

Tips:

* After pulling new code or changing dependencies, reattach to the session, stop the bot with `Ctrl + C`, run your update commands, and start it again with `python -m accountingbot.bot`.
* You can create additional sessions for other projects by replacing `accountingbot` with a different name in the commands above.

---

## 8. Check that the bot is running

1. Reattach to your `tmux` session to see the live output:

   ```bash
   tmux attach -t accountingbot
   ```

   You should see messages showing that the bot started successfully. If you need to step away, detach again with `Ctrl + B`, then `D`.
2. Keep an eye on the log file (from a separate Terminal tab or window) if you want a quieter view:

   ```bash
   tail -f accounting_bot.log
   ```

   Press `Ctrl + C` to stop watching.
3. Open Telegram and send a message to your bot. If the bot replies, everything is working.

---

## 9. Everyday tasks

* **Restart after updates** – attach to the `tmux` session, stop the bot with `Ctrl + C`, run any updates, then start it again with `python -m accountingbot.bot`.
* **Update the code** – in the Terminal (with the virtual environment active), run `git pull`. Then run `pip install -r requirements.txt` again in case new packages were added. Start the bot again afterwards.
* **Back up your data** – the bot stores its database in the file named in `DATABASE_PATH` (default `accounting.db`). Download it from **File Manager** or via SFTP to keep a backup.
* **Keep secrets safe** – never share your Telegram bot token or expose the values you entered in the Environment variables section or `.env` file.

---

## 10. Troubleshooting

| Problem | What to try |
| ------- | ----------- |
| The Terminal command says “No such file or directory” | Check for typos, especially in your username. Run `pwd` to see which folder you are in. |
| `source .../activate` fails | Make sure you copied the **exact** path from the Setup Python App screen. |
| `pip` errors about the internet | Wait a minute and run the command again. If it still fails, contact your host—they might block outgoing connections. |
| Bot does not answer in Telegram | Re-open **Setup Python App**, click **View Logs**, and look for error messages. Double-check the `BOT_TOKEN` value in the Environment variables list. |
| You closed the Terminal window | Open it again and repeat the `source .../activate` step before running any Python commands. |

If a problem is not listed, copy the exact error message and share it with someone who can help (for example, your hosting support or the AccountingBot maintainers). Exact words matter—avoid summarizing.

---

You did it! Deploying a Python bot on cPanel is a big achievement, especially if this was your first time working with these tools. Keep this guide handy for future updates.
* **Permission issues writing logs or databases:** Ensure the paths used for `LOG_FILE` and `DATABASE_PATH` are inside your home directory or otherwise writable by the cPanel user.
* **SSL errors when calling Telegram or cPanel:** Verify that the server has outbound HTTPS access. For self-signed cPanel certificates, set `CPANEL_VERIFY_SSL=false` temporarily while you install a trusted certificate.

---

Need to revisit configuration later? Re-open **Setup Python App** at any time to review the settings, restart the process, or adjust the Python version. For Telegram-specific problems (webhook bans, rate limits, etc.), check the official [Telegram Bot API FAQ](https://core.telegram.org/bots/faq) once you have confirmed the application is running.
