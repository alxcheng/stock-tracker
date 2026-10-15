"""Stock Tracker: local web app for the watchlist, positions and alerts.

  python app.py            # then open http://127.0.0.1:8050

Adding/removing stocks here edits watchlist.json and positions.json, which the
2-hour alert job also reads.
"""

from __future__ import annotations

import io
import json
import os
import re
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

import notifiers
import paths
import positions
import scheduler
import settings
import tracker
import updater

HERE = Path(__file__).parent
INDEX = HERE / "web" / "index.html"
PORT = paths.PORT
RESTART_EXIT_CODE = 75  # tells bootstrap.py to start the newly installed version
TOPIC = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
CACHE_SECONDS = 60
SYMBOL = re.compile(r"^[A-Z0-9][A-Z0-9.^=\-]{0,14}$|^\^[A-Z0-9.]{1,14}$")

watchlist_lock = threading.Lock()
cache_lock = threading.Lock()
MIN_REFRESH_SECONDS = 15  # rapid Refresh clicks reuse the last fetch

# yfinance's sqlite caches open one connection per thread and never close it, so
# calling it from the server's per-request threads leaks file handles until the
# process hits "Too many open files". Funnel every call through one long-lived thread.
_yf_thread = ThreadPoolExecutor(max_workers=1, thread_name_prefix="yfinance")
_cache: dict = {"at": 0.0, "tickers": None, "payload": None}
_last_good: dict[str, dict] = {}  # ticker -> most recent successful row


def _fetch(tickers: list[str]) -> dict:
    return _yf_thread.submit(tracker.fetch, tickers, threads=False).result(timeout=90)


def snapshot(fresh: bool = False) -> dict:
    """Market data for the watchlist plus any held tickers that aren't on it."""
    watchlist = tracker.load_watchlist()
    tickers = list(dict.fromkeys(watchlist + positions.tickers()))
    with cache_lock:
        age = time.time() - _cache["at"]
        if _cache["tickers"] == tickers and age < (MIN_REFRESH_SECONDS if fresh else CACHE_SECONDS):
            return _cache["payload"]
        cfg = tracker.load_config()
        try:
            readings, _ = tracker.analyze(_fetch(tickers), cfg) if tickers else ([], [])
        except Exception as exc:
            # Yahoo hiccup or rate limit: fall back to the last good data if we have it.
            print(f"[warn] fetch failed: {exc!r}", file=sys.stderr)
            if not any(t in _last_good for t in tickers):
                raise
            readings = []
        for r in readings:
            _last_good[r.ticker] = {
                "ticker": r.ticker,
                "price": r.price,
                "change": r.change,
                "change_pct": r.change_pct,
                "k": r.k,
                "d": r.d,
                "zone": r.zone,
                "is_new": r.is_new,
                "sma": r.sma,
                "sma_pct": r.sma_pct,
                "below_sma": r.below_sma,
                "crossed_below": r.crossed_below,
            }
        fresh_tickers = {r.ticker for r in readings}
        rows = []
        for t in watchlist:
            if t in fresh_tickers:
                rows.append(_last_good[t])
            elif t in _last_good:
                rows.append({**_last_good[t], "stale": True})
            else:
                rows.append({"ticker": t, "error": "No data"})
        payload = {
            "updated": datetime.now(timezone.utc).isoformat(),
            "overbought": cfg["alerts"]["overbought"],
            "oversold": cfg["alerts"]["oversold"],
            "ma_period": tracker.ma_settings(cfg)["period"],
            "gain_alert_pct": tracker.position_settings(cfg)["gain_alert_pct"],
            "stocks": rows,
        }
        # Only cache complete results, so a partial failure is retried next time.
        _cache.update(at=time.time() if len(fresh_tickers) == len(tickers) else 0.0,
                      tickers=tickers, payload=payload)
        return payload


def run_check_now() -> dict:
    # Same single yfinance thread as the page uses (see _yf_thread above).
    return _yf_thread.submit(tracker.run_check).result(timeout=300)


def schedule_times() -> list[str]:
    return tracker.load_config().get("schedule", {}).get("times_et", scheduler.DEFAULT_TIMES)


def status_payload() -> dict:
    s = settings.load()
    times = schedule_times()
    nxt = scheduler.next_slot(datetime.now(scheduler.ET), times)
    return {
        "version": paths.VERSION,
        "installed": paths.ROOT is not None,
        "platform": sys.platform,
        "update": updater.status(),
        "last_update": updater.last_update(),
        "schedule": {
            "times_et": times,
            "last_run": scheduler.load_state().get("last_run"),
            "next": nxt.isoformat() if nxt else None,
        },
        "settings": {
            "ntfy_topic": s.get("ntfy_topic", ""),
            "ntfy_server": (s.get("ntfy_server") or "https://ntfy.sh").rstrip("/"),
            "macos_notify": bool(s.get("macos_notify")),
        },
    }


def ntfy_qr_svg() -> bytes:
    import segno

    st = status_payload()["settings"]
    buf = io.BytesIO()
    segno.make(f"{st['ntfy_server']}/{st['ntfy_topic']}", error="m").save(
        buf, kind="svg", scale=5, border=2, dark="#111111", light="#ffffff"
    )
    return buf.getvalue()


def has_data(symbol: str) -> bool:
    return symbol in _last_good or symbol in _fetch([symbol])


def with_positions(payload: dict) -> dict:
    """Positions change independently of prices, so they're computed per request."""
    return {**payload, "positions": positions.summarize(positions.load(), _last_good)}


def _number(value) -> float | None:
    try:
        n = float(str(value).replace(",", "").replace("$", "").strip())
    except (TypeError, ValueError):
        return None
    return n if n > 0 and n < 1e12 else None


class Handler(BaseHTTPRequestHandler):
    def _send(self, status: int, body: bytes, ctype: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, status: int, obj) -> None:
        self._send(status, json.dumps(obj).encode(), "application/json")

    def do_GET(self) -> None:
        url = urlparse(self.path)
        if url.path == "/":
            self._send(200, INDEX.read_bytes(), "text/html; charset=utf-8")
        elif url.path == "/api/status":
            self._json(200, status_payload())
        elif url.path == "/api/ntfy-qr.svg":
            self._send(200, ntfy_qr_svg(), "image/svg+xml")
        elif url.path == "/api/stocks":
            try:
                self._json(200, with_positions(snapshot(fresh="fresh=1" in url.query)))
            except Exception as exc:
                self._json(502, {"error": f"Couldn't load market data: {exc}"})
        else:
            self._json(404, {"error": "Not found"})

    def _body(self) -> dict:
        length = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(length))
        if not isinstance(body, dict):
            raise ValueError
        return body

    def do_POST(self) -> None:
        path = urlparse(self.path).path
        if path == "/api/positions":
            return self.add_position()
        if path == "/api/settings":
            return self.save_settings()
        if path == "/api/test-notification":
            sent = notifiers.send(
                "Stock Tracker test", "Alerts are working. You'll get pings like this when something needs attention.",
                False, settings.load(),
            )
            if sent:
                return self._json(200, {"sent": sent})
            return self._json(502, {"error": "Couldn't send. Check your internet connection and topic."})
        if path == "/api/run-check":
            try:
                result = run_check_now()
            except Exception as exc:
                result = {"status": "error", "error": str(exc)}
            scheduler.record_run(result)
            return self._json(200, result)
        if path == "/api/check-updates":
            result = updater.check()
            self._json(200, result)
            if result["status"] == "updated":
                request_restart()
            return
        if path != "/api/tickers":
            return self._json(404, {"error": "Not found"})
        try:
            symbol = str(self._body()["symbol"]).strip().upper()
        except (ValueError, KeyError, TypeError):
            return self._json(400, {"error": "Send JSON like {\"symbol\": \"AAPL\"}"})
        if not SYMBOL.match(symbol):
            return self._json(400, {"error": f"“{symbol}” doesn't look like a ticker"})
        if symbol in tracker.load_watchlist():
            return self._json(409, {"error": f"{symbol} is already on your list"})
        if not has_data(symbol):
            return self._json(404, {"error": f"No market data found for {symbol}"})
        with watchlist_lock:
            tickers = tracker.load_watchlist()
            if symbol not in tickers:
                tracker.save_watchlist(tickers + [symbol])
        self._json(201, {"added": symbol})

    def save_settings(self) -> None:
        try:
            body = self._body()
        except (ValueError, TypeError):
            return self._json(400, {"error": "Send JSON settings"})
        changes = {}
        if "ntfy_topic" in body:
            topic = str(body["ntfy_topic"]).strip()
            if not TOPIC.match(topic):
                return self._json(400, {"error": "Topic can use letters, numbers, - and _ (up to 64)"})
            changes["ntfy_topic"] = topic
        if "macos_notify" in body:
            changes["macos_notify"] = bool(body["macos_notify"])
        settings.save(changes)
        self._json(200, status_payload())

    def add_position(self) -> None:
        try:
            body = self._body()
            symbol = str(body["ticker"]).strip().upper()
        except (ValueError, KeyError, TypeError):
            return self._json(400, {"error": "Send ticker, shares and price"})
        shares, price = _number(body.get("shares")), _number(body.get("price"))
        if not SYMBOL.match(symbol):
            return self._json(400, {"error": f"“{symbol}” doesn't look like a ticker"})
        if shares is None:
            return self._json(400, {"error": "Shares must be a number above 0"})
        if price is None:
            return self._json(400, {"error": "Buy price must be a number above 0"})
        if not has_data(symbol):
            return self._json(404, {"error": f"No market data found for {symbol}"})
        self._json(201, positions.add(symbol, shares, price))

    def do_DELETE(self) -> None:
        url = urlparse(self.path)
        path = url.path
        if path == "/api/positions":  # ?ticker=AAPL removes every lot of that ticker
            ticker = parse_qs(url.query).get("ticker", [""])[0].upper()
            n = positions.remove(ticker=ticker) if ticker else 0
            return self._json(200 if n else 404, {"removed": n} if n else {"error": f"No {ticker} position"})
        if path.startswith("/api/positions/"):
            n = positions.remove(lot_id=unquote(path.removeprefix("/api/positions/")))
            return self._json(200 if n else 404, {"removed": n} if n else {"error": "Position not found"})
        if not path.startswith("/api/tickers/"):
            return self._json(404, {"error": "Not found"})
        symbol = unquote(path.removeprefix("/api/tickers/")).upper()
        with watchlist_lock:
            tickers = tracker.load_watchlist()
            if symbol not in tickers:
                return self._json(404, {"error": f"{symbol} isn't on your list"})
            tracker.save_watchlist([t for t in tickers if t != symbol])
        self._json(200, {"removed": symbol})

    def log_request(self, code="-", size="-") -> None:
        if str(code).isdigit() and int(code) >= 400:  # keep the log to problems
            super().log_request(code, size)

    def log_message(self, fmt, *args) -> None:
        print(f"[http] {fmt % args}", flush=True)


class Server(ThreadingHTTPServer):
    # On Windows SO_REUSEADDR lets a second server steal a port in use, so only use it elsewhere.
    allow_reuse_address = sys.platform != "win32"
    daemon_threads = True


_server: Server | None = None
_restart = threading.Event()


def request_restart() -> None:
    """Stop serving so main() exits with RESTART_EXIT_CODE and bootstrap.py
    starts the newly installed version."""
    _restart.set()
    if _server:
        threading.Thread(target=_server.shutdown, daemon=True).start()


def _parent_alive(pid: int) -> bool:
    if sys.platform == "win32":
        import ctypes

        kernel32 = ctypes.windll.kernel32
        handle = kernel32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
        if not handle:
            return False
        code = ctypes.c_ulong()
        kernel32.GetExitCodeProcess(handle, ctypes.byref(code))
        kernel32.CloseHandle(handle)
        return code.value == 259  # STILL_ACTIVE
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        pass
    return True


def watch_parent() -> None:
    """Exit if bootstrap.py goes away (e.g. the user logged out), so an orphaned
    server never holds the port when the next session starts."""
    pid = int(os.environ.get("STOCK_TRACKER_PARENT_PID") or 0)
    if not pid:
        return

    def loop() -> None:
        while _parent_alive(pid):
            time.sleep(5)
        os._exit(0)

    threading.Thread(target=loop, name="parent-watch", daemon=True).start()


def main() -> int:
    global _server
    paths.ensure_data()
    settings.ensure()
    _server = Server(("127.0.0.1", PORT), Handler)
    watch_parent()
    scheduler.start(run_check_now, schedule_times)
    updater.start(request_restart)
    print(f"Stock Tracker {paths.VERSION} on http://127.0.0.1:{PORT}  (data: {paths.DATA})", flush=True)
    try:
        _server.serve_forever()
    except KeyboardInterrupt:
        pass
    return RESTART_EXIT_CODE if _restart.is_set() else 0


if __name__ == "__main__":
    sys.exit(main())
