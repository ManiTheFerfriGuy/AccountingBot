import subprocess
import sys
from pathlib import Path

from accountingbot.secrets import load_secrets

BASE_DIR = Path(__file__).resolve().parent
load_secrets(BASE_DIR / "secrets.json")

if __name__ == "__main__":
    subprocess.run(
        [sys.executable, "-m", "accountingbot.bot"],
        check=True,
        cwd=BASE_DIR,
    )
