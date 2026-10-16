"""Stock Tracker launcher: what the Desktop and Start-menu shortcut runs on Windows.

Starts the app if it isn't running, waits until it answers, then opens the page.
Starting it when it's already running is harmless: bootstrap.py exits at once
if another copy holds its lock. Standard library only; desktop.py copies this
file into the install folder.
"""

import json
import os
import subprocess
import sys
import time
import urllib.request
import webbrowser
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def port() -> int:
    try:
        return int(json.loads((ROOT / "install.json").read_text()).get("port") or 8050)
    except (OSError, ValueError):
        return 8050


URL = f"http://127.0.0.1:{port()}/"


def running() -> bool:
    try:
        with urllib.request.urlopen(URL + "api/status", timeout=2):
            return True
    except OSError:
        return False


def message(text: str) -> None:
    if sys.platform == "win32":
        import ctypes

        ctypes.windll.user32.MessageBoxW(None, text, "Stock Tracker", 0x40)  # MB_ICONINFORMATION
    else:
        print(text)


def start_app() -> None:
    # Detached so the app keeps running after this launcher exits.
    flags = 0
    if sys.platform == "win32":
        flags = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
    subprocess.Popen(
        [sys.executable, str(ROOT / "bootstrap.py")], cwd=ROOT, creationflags=flags,
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        close_fds=True, start_new_session=sys.platform != "win32",
    )


def main() -> None:
    if not running():
        start_app()
        for _ in range(90):
            time.sleep(1)
            if running():
                break
        else:
            message(f"Stock Tracker didn't start.\n\nDetails are in:\n{ROOT / 'logs' / 'app.log'}")
            return
    if not os.environ.get("STOCK_TRACKER_NO_OPEN"):
        webbrowser.open(URL)


if __name__ == "__main__":
    main()
