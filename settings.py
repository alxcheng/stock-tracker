"""Per-user settings (settings.json in the data folder), edited from the web page.

Keys mirror the notifier environment variables in lower case, e.g. "ntfy_topic".
Environment variables still win, which keeps dev and CI overrides simple.
"""

from __future__ import annotations

import json
import secrets
import string
import sys
import threading

from paths import DATA

FILE = DATA / "settings.json"
_lock = threading.Lock()


def load() -> dict:
    return json.loads(FILE.read_text()) if FILE.exists() else {}


def save(values: dict) -> dict:
    with _lock:
        merged = {**load(), **values}
        tmp = FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps(merged, indent=1) + "\n")
        tmp.replace(FILE)
    return merged


def new_topic() -> str:
    # Anyone who knows a ntfy topic name can read it, so make it unguessable.
    alphabet = string.ascii_lowercase + string.digits
    return "stocks-" + "".join(secrets.choice(alphabet) for _ in range(14))


def ensure() -> dict:
    """Give every new install its own private ntfy topic and sensible defaults."""
    current = load()
    missing = {}
    if not current.get("ntfy_topic"):
        missing["ntfy_topic"] = new_topic()
    if "macos_notify" not in current:
        missing["macos_notify"] = sys.platform == "darwin"
    return save(missing) if missing else current
