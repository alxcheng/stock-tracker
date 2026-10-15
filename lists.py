"""Named stock lists, each with its own alert settings (lists.json in the data folder).

Each list: {"id", "name", "tickers": [...], "alerts": {"enabled", "overbought",
"oversold", "below_ma"}}. The first time this version runs, the old single
watchlist.json becomes a list called "Watchlist" with every alert on.

Lists are shared as a code ("ST1-...") that the recipient pastes into Import:
compressed JSON in URL-safe base64, holding only a name, tickers and alert switches.
"""

from __future__ import annotations

import base64
import json
import re
import secrets
import threading
import zlib

from paths import DATA, DEFAULTS

FILE = DATA / "lists.json"
LEGACY = DATA / "watchlist.json"
SYMBOL = re.compile(r"^[A-Z0-9][A-Z0-9.^=\-]{0,14}$|^\^[A-Z0-9.]{1,14}$")
ALERT_KEYS = ("overbought", "oversold", "below_ma")
SHARE_PREFIX = "ST1-"
MAX_TICKERS = 200

_lock = threading.RLock()


def _alerts(given: dict | None = None) -> dict:
    out = {"enabled": True, **{k: True for k in ALERT_KEYS}}
    for key in out:
        if given and key in given:
            out[key] = bool(given[key])
    return out


def _tickers(raw) -> list[str]:
    out = []
    for t in raw or []:
        t = str(t).strip().upper()
        if SYMBOL.match(t) and t not in out:
            out.append(t)
    return out[:MAX_TICKERS]


def _name(raw, taken: set[str]) -> str:
    base = " ".join(str(raw or "").split())[:40] or "List"
    name, n = base, 2
    while name.lower() in taken:
        name, n = f"{base} ({n})", n + 1
    return name


def _save(lists: list[dict]) -> None:
    tmp = FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps({"lists": lists}, indent=1) + "\n")
    tmp.replace(FILE)


def load() -> list[dict]:
    with _lock:
        if FILE.exists():
            return json.loads(FILE.read_text())["lists"]
        src = LEGACY if LEGACY.exists() else DEFAULTS / "watchlist.json"
        tickers = json.loads(src.read_text()) if src.exists() else []
        lists = [{"id": secrets.token_hex(4), "name": "Watchlist", "tickers": _tickers(tickers), "alerts": _alerts()}]
        _save(lists)
        return lists


def all_tickers() -> list[str]:
    return list(dict.fromkeys(t for lst in load() for t in lst["tickers"]))


def get(list_id: str) -> dict | None:
    return next((l for l in load() if l["id"] == list_id), None)


def create(name: str, tickers=(), alerts: dict | None = None) -> dict:
    with _lock:
        lists = load()
        lst = {
            "id": secrets.token_hex(4),
            "name": _name(name, {l["name"].lower() for l in lists}),
            "tickers": _tickers(tickers),
            "alerts": _alerts(alerts),
        }
        _save(lists + [lst])
        return lst


def update(list_id: str, name: str | None = None, alerts: dict | None = None) -> dict | None:
    with _lock:
        lists = load()
        lst = next((l for l in lists if l["id"] == list_id), None)
        if lst is None:
            return None
        if name is not None:
            lst["name"] = _name(name, {l["name"].lower() for l in lists if l is not lst})
        if alerts is not None:
            lst["alerts"] = _alerts({**lst["alerts"], **alerts})
        _save(lists)
        return lst


def delete(list_id: str) -> str:
    """Returns "deleted", "missing" or "last" (the last list can't be deleted)."""
    with _lock:
        lists = load()
        keep = [l for l in lists if l["id"] != list_id]
        if len(keep) == len(lists):
            return "missing"
        if not keep:
            return "last"
        _save(keep)
        return "deleted"


def add_ticker(list_id: str, symbol: str) -> str:
    """Returns "added", "exists" or "missing"."""
    with _lock:
        lists = load()
        lst = next((l for l in lists if l["id"] == list_id), None)
        if lst is None:
            return "missing"
        if symbol in lst["tickers"]:
            return "exists"
        lst["tickers"].append(symbol)
        _save(lists)
        return "added"


def remove_ticker(list_id: str, symbol: str) -> bool:
    with _lock:
        lists = load()
        lst = next((l for l in lists if l["id"] == list_id), None)
        if lst is None or symbol not in lst["tickers"]:
            return False
        lst["tickers"].remove(symbol)
        _save(lists)
        return True


# ---- sharing ----

def share_code(lst: dict) -> str:
    payload = {
        "v": 1,
        "n": lst["name"],
        "t": lst["tickers"],
        "a": {k: lst["alerts"][k] for k in ("enabled", *ALERT_KEYS)},
    }
    raw = zlib.compress(json.dumps(payload, separators=(",", ":")).encode(), 9)
    return SHARE_PREFIX + base64.urlsafe_b64encode(raw).decode().rstrip("=")


def parse_share(text: str) -> dict:
    """Read a share code, also when it's pasted inside a longer message.
    Returns {"name", "tickers", "alerts"}; raises ValueError if it isn't valid."""
    match = re.search(re.escape(SHARE_PREFIX) + r"([A-Za-z0-9_-]+)", text or "")
    if not match:
        raise ValueError("That doesn't contain a Stock Tracker list code (it starts with ST1-).")
    data = match.group(1)
    try:
        # Cap the decompressed size so a bogus code can't balloon in memory.
        raw = zlib.decompressobj().decompress(base64.urlsafe_b64decode(data + "=" * (-len(data) % 4)), 100_000)
        payload = json.loads(raw)
        tickers = _tickers(payload["t"])
        name = str(payload.get("n") or "Shared list")
        alerts = _alerts(payload.get("a") if isinstance(payload.get("a"), dict) else None)
    except Exception as exc:
        raise ValueError("That list code is damaged or incomplete. Ask for it again.") from exc
    if not tickers:
        raise ValueError("That shared list has no stocks in it.")
    return {"name": name, "tickers": tickers, "alerts": alerts}
