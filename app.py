"""Stock Tracker: local web app for the watchlist, positions and alerts.

  python app.py            # then open http://127.0.0.1:8050

Lists, positions and alert settings edited here live in the data folder, which
the scheduled alert check (scheduler.py) reads too.
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

import lists
import notifiers
import paths
import positions
import scheduler
import settings
import tracker
import updater
from market import Market, market_open

HERE = Path(__file__).parent
INDEX = HERE / "web" / "index.html"
PORT = paths.PORT
RESTART_EXIT_CODE = 75  # tells bootstrap.py to start the newly installed version
TOPIC = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
SYMBOL = lists.SYMBOL
REFRESH_LOOP_SECONDS = 30

# yfinance's sqlite caches open one connection per thread and never close it, so
# calling it from the server's per-request threads leaks file handles until the
# process hits "Too many open files". Funnel every call through one long-lived thread.
_yf_thread = ThreadPoolExecutor(max_workers=1, thread_name_prefix="yfinance")
market = Market(lambda tickers, period: tracker.fetch(tickers, threads=False, period=period))
_rows: dict = {"version": None, "rows": {}}
_rows_lock = threading.Lock()


def all_tickers() -> list[str]:
    """Every stock on any list, plus held stocks that aren't on one."""
    return list(dict.fromkeys(lists.all_tickers() + positions.tickers()))


def refresh_market(tickers: list[str] | None = None) -> str:
    """Fetch whatever market.py says is due, on the single yfinance thread."""
    return _yf_thread.submit(market.refresh, all_tickers() if tickers is None else tickers).result(timeout=300)


def start_market_loop() -> None:
    """The only regular source of Yahoo requests: every 30s, fetch what's due."""

    def loop() -> None:
        while True:
            try:
                refresh_market()
            except Exception as exc:  # never let the loop die
                print(f"[market] refresh failed: {exc!r}", flush=True)
            time.sleep(REFRESH_LOOP_SECONDS)

    threading.Thread(target=loop, name="market", daemon=True).start()


def stock_rows() -> dict[str, dict]:
    """Indicator rows for every stock with data, recomputed only when data changes."""
    with _rows_lock:
        version = market.version
        if _rows["version"] != version:
            readings, _ = tracker.analyze(market.data(list(market.history)), tracker.load_config())
            _rows["rows"] = {
                r.ticker: {
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
                for r in readings
            }
            _rows["version"] = version
        return _rows["rows"]


def _iso(dt: datetime | None) -> str | None:
    return dt.isoformat() if dt else None


def snapshot() -> dict:
    """What the page shows. Normally no Yahoo request happens here; the market loop
    keeps data fresh. Only missing or overdue data (just started, woke from sleep,
    a stock just added) is fetched before answering."""
    tickers = all_tickers()
    now = datetime.now(scheduler.ET)
    if tickers and not market.backing_off(now) and market.overdue(tickers, now):
        try:
            refresh_market(tickers)
        except Exception as exc:
            print(f"[market] refresh failed: {exc!r}", flush=True)
    cfg = tracker.load_config()
    have = stock_rows()
    rows = {}
    for t in tickers:
        if t in have:
            rows[t] = {**have[t], "stale": True} if t in market.failed else have[t]
        else:
            rows[t] = {"ticker": t, "error": "No data"}
    return {
        "prices_at": _iso(market.oldest_update(tickers)),
        "market_open": market_open(now),
        "backoff_until": _iso(market.backoff_until) if market.backing_off(now) else None,
        "overbought": cfg["alerts"]["overbought"],
        "oversold": cfg["alerts"]["oversold"],
        "ma_period": tracker.ma_settings(cfg)["period"],
        "stocks": rows,
    }


def run_check_now() -> dict:
    """The scheduled alert check. Reuses the app's data when it's current: the
    refresh only fetches what's due (prices older than 5 minutes during market
    hours, nothing after the close once the closing prices are in)."""
    tickers = list(dict.fromkeys(lists.all_tickers() + [l["ticker"] for l in positions.load()]))
    try:
        refresh_market(tickers)
    except Exception as exc:
        print(f"[market] refresh before check failed: {exc!r}", flush=True)
    return tracker.run_check(data=market.data(tickers))


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


def has_data(symbol: str) -> bool | None:
    """Whether Yahoo has data for a symbol, fetching it now if it's new.
    None means we can't tell right now because Yahoo is limiting requests."""
    if symbol in market.history:
        return True
    if market.backing_off(datetime.now(scheduler.ET)):
        return None
    refresh_market([symbol])
    return symbol in market.history


def with_user_data(payload: dict) -> dict:
    """Lists, positions and alert settings change independently of prices, so they're
    added per request on top of the cached market data."""
    return {
        **payload,
        "lists": lists.load(),
        "positions": positions.summarize(positions.load(), stock_rows()),
        "position_alerts": tracker.position_settings(tracker.load_config(), settings.load()),
    }


def request_problem(method: str, headers) -> str | None:
    """Why to refuse a request that didn't come from this app's own page, or None.

    Any website open in the browser can send requests to 127.0.0.1, so:
    - Host must name this computer. That stops DNS rebinding, where a site's own
      name is made to point at 127.0.0.1 so the browser lets it read our replies.
    - Changes (POST/DELETE) must not come from another site's page (browsers send
      Origin on those), and POSTs must be JSON, which a page on another site can't
      send here without a permission check (CORS preflight) that we never grant.
    """
    hosts = {f"127.0.0.1:{PORT}", f"localhost:{PORT}"}
    if (headers.get("Host") or "").lower() not in hosts:
        return "Requests must be made to 127.0.0.1"
    if method in ("POST", "DELETE"):
        origin = (headers.get("Origin") or "").lower()
        if origin and origin not in {f"http://{h}" for h in hosts}:
            return "Changes can only be made from the Stock Tracker page"
        if method == "POST" and not (headers.get("Content-Type") or "").lower().startswith("application/json"):
            return "Expected JSON"
    return None


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

    def _refused(self, method: str) -> bool:
        problem = request_problem(method, self.headers)
        if problem:
            self._json(403, {"error": problem})
        return problem is not None

    def do_GET(self) -> None:
        if self._refused("GET"):
            return
        url = urlparse(self.path)
        if url.path == "/":
            self._send(200, INDEX.read_bytes(), "text/html; charset=utf-8")
        elif url.path == "/api/status":
            self._json(200, status_payload())
        elif url.path == "/api/ntfy-qr.svg":
            self._send(200, ntfy_qr_svg(), "image/svg+xml")
        elif url.path.startswith("/api/lists/") and url.path.endswith("/share"):
            lst = lists.get(url.path.split("/")[3])
            if lst is None:
                return self._json(404, {"error": "List not found"})
            self._json(200, {"code": lists.share_code(lst)})
        elif url.path == "/api/stocks":
            try:
                self._json(200, with_user_data(snapshot()))
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
        if self._refused("POST"):
            return
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
        if path.startswith("/api/lists"):
            return self.lists_post(path.removeprefix("/api/lists").strip("/").split("/"))
        return self._json(404, {"error": "Not found"})

    def lists_post(self, parts: list[str]) -> None:
        try:
            body = self._body()
        except (ValueError, TypeError):
            return self._json(400, {"error": "Send JSON"})
        if parts == [""]:  # create
            return self._json(201, lists.create(str(body.get("name") or "New list")))
        if parts == ["import"]:  # a pasted share code
            try:
                shared = lists.parse_share(str(body.get("code") or ""))
            except ValueError as exc:
                return self._json(400, {"error": str(exc)})
            return self._json(201, lists.create(shared["name"], shared["tickers"], shared["alerts"]))
        list_id = parts[0]
        if len(parts) == 1:  # rename / alert switches
            alerts = body.get("alerts") if isinstance(body.get("alerts"), dict) else None
            lst = lists.update(list_id, name=body.get("name"), alerts=alerts)
            return self._json(200, lst) if lst else self._json(404, {"error": "List not found"})
        if parts[1:] == ["tickers"]:
            symbol = str(body.get("symbol") or "").strip().upper()
            if not SYMBOL.match(symbol):
                return self._json(400, {"error": f"“{symbol}” doesn't look like a ticker"})
            if lists.get(list_id) is None:
                return self._json(404, {"error": "List not found"})
            if symbol in lists.get(list_id)["tickers"]:
                return self._json(409, {"error": f"{symbol} is already on this list"})
            found = has_data(symbol)
            if found is None:
                return self._json(503, {"error": "Yahoo is limiting requests right now. Try again in a little while."})
            if not found:
                return self._json(404, {"error": f"No market data found for {symbol}"})
            lists.add_ticker(list_id, symbol)
            return self._json(201, {"added": symbol})
        return self._json(404, {"error": "Not found"})

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
        if "position_alerts" in body:
            changes["position_alerts"] = bool(body["position_alerts"])
        if "gain_alert_pct" in body:
            pct = _number(body["gain_alert_pct"])
            if pct is None or pct > 10000:
                return self._json(400, {"error": "Gain alert must be a percentage above 0"})
            changes["gain_alert_pct"] = pct
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
        found = has_data(symbol)
        if found is None:
            return self._json(503, {"error": "Yahoo is limiting requests right now. Try again in a little while."})
        if not found:
            return self._json(404, {"error": f"No market data found for {symbol}"})
        self._json(201, positions.add(symbol, shares, price))

    def do_DELETE(self) -> None:
        if self._refused("DELETE"):
            return
        url = urlparse(self.path)
        path = url.path
        if path == "/api/positions":  # ?ticker=AAPL removes every lot of that ticker
            ticker = parse_qs(url.query).get("ticker", [""])[0].upper()
            n = positions.remove(ticker=ticker) if ticker else 0
            return self._json(200 if n else 404, {"removed": n} if n else {"error": f"No {ticker} position"})
        if path.startswith("/api/positions/"):
            n = positions.remove(lot_id=unquote(path.removeprefix("/api/positions/")))
            return self._json(200 if n else 404, {"removed": n} if n else {"error": "Position not found"})
        parts = [unquote(p) for p in path.removeprefix("/api/lists/").split("/")] if path.startswith("/api/lists/") else []
        if len(parts) == 1:
            result = lists.delete(parts[0])
            if result == "last":
                return self._json(409, {"error": "You need at least one list."})
            return self._json(200 if result == "deleted" else 404, {"result": result})
        if len(parts) == 3 and parts[1] == "tickers":
            if lists.remove_ticker(parts[0], parts[2].upper()):
                return self._json(200, {"removed": parts[2].upper()})
            return self._json(404, {"error": f"{parts[2]} isn't on that list"})
        self._json(404, {"error": "Not found"})

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
    start_market_loop()
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
