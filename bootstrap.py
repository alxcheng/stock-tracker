"""Keeps Stock Tracker running. Standard library only.

The installer copies this file to the install folder and the OS starts it at
login (launchd on macOS, Task Scheduler on Windows). It runs the version named
in current.txt and:
  - restarts it right away when the app exits with RESTART (after an update),
  - restarts it with a growing delay if it crashes,
  - rolls back to previous.txt if a just-installed version keeps crashing.
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent
LOG = ROOT / "logs" / "app.log"
RESTART = 75
QUICK_CRASH = 60  # seconds; dying faster than this counts as a failed start


def log(msg: str) -> None:
    LOG.parent.mkdir(exist_ok=True)
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(f"{datetime.now():%Y-%m-%d %H:%M:%S} [bootstrap] {msg}\n")


def read(name: str) -> str:
    try:
        return (ROOT / name).read_text().strip()
    except FileNotFoundError:
        return ""


def write(name: str, text: str) -> None:
    tmp = ROOT / f"{name}.tmp"
    tmp.write_text(text + "\n")
    tmp.replace(ROOT / name)


def venv_python(vdir: Path) -> Path:
    if sys.platform == "win32":
        return vdir / ".venv" / "Scripts" / "python.exe"
    return vdir / ".venv" / "bin" / "python"


def rotate_log() -> None:
    if LOG.exists() and LOG.stat().st_size > 5_000_000:
        LOG.replace(LOG.with_suffix(".old.log"))


def install_info() -> dict:
    try:
        return json.loads((ROOT / "install.json").read_text())
    except (FileNotFoundError, ValueError):
        return {}


def run(version: str, port: int) -> int:
    vdir = ROOT / "versions" / version
    env = {
        **os.environ,
        "STOCK_TRACKER_PORT": str(port),
        "STOCK_TRACKER_ROOT": str(ROOT),
        "STOCK_TRACKER_DATA": str(ROOT / "data"),
        "STOCK_TRACKER_PARENT_PID": str(os.getpid()),
        "PYTHONUNBUFFERED": "1",
        "PYTHONIOENCODING": "utf-8",
    }
    with open(LOG, "a", encoding="utf-8") as out:
        return subprocess.call(
            [str(venv_python(vdir)), str(vdir / "app.py")],
            cwd=vdir, env=env, stdout=out, stderr=subprocess.STDOUT,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )


def main() -> None:
    port = int(install_info().get("port") or 8050)
    lock = socket.socket()  # held for our lifetime so a second copy exits immediately
    try:
        lock.bind(("127.0.0.1", port + 9))
    except OSError:
        log("another copy is already running; exiting")
        return

    crashes = 0
    while True:
        rotate_log()
        version = read("current.txt")
        log(f"starting version {version}")
        started = time.time()
        code = run(version, port)
        if code == RESTART:
            log("restarting (update installed)")
            crashes = 0
            continue
        ran = time.time() - started
        log(f"version {version} exited with code {code} after {ran:.0f}s")
        crashes = crashes + 1 if ran < QUICK_CRASH else 0

        previous = read("previous.txt")
        if crashes >= 3 and read("trial.txt") == version and previous and previous != version:
            log(f"version {version} keeps failing; rolling back to {previous}")
            write("bad.txt", f"{read('bad.txt')} {version}".strip())
            write("current.txt", previous)
            (ROOT / "trial.txt").unlink(missing_ok=True)
            crashes = 0
            continue
        time.sleep(min(300, 5 * 2 ** min(crashes, 6)))


if __name__ == "__main__":
    main()
