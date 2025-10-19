import subprocess
import sys
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")

if __name__ == "__main__":
    subprocess.run([sys.executable, "-m", "accountingbot.bot"], check=True)
