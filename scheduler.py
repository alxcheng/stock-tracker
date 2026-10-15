"""Runs the alert check at set US-market times while the app is running.

This replaces a per-OS scheduler (launchd / Task Scheduler): the app is already
kept running in the background, so it can keep its own clock. A slot missed
because the computer was asleep still runs if it wakes within GRACE.
"""

from __future__ import annotations

import json
import threading
import time
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from paths import DATA

ET = ZoneInfo("America/New_York")
STATE = DATA / "schedule_state.json"
GRACE = timedelta(hours=2)
DEFAULT_TIMES = ["10:00", "12:00", "14:00", "16:00"]


def _slots(day: datetime, times: list[str]) -> list[datetime]:
    if day.weekday() >= 5:  # Saturday, Sunday
        return []
    out = []
    for t in times:
        hour, minute = (int(x) for x in t.split(":"))
        out.append(day.replace(hour=hour, minute=minute, second=0, microsecond=0))
    return sorted(out)


def due_slot(now: datetime, times: list[str], last: str | None) -> datetime | None:
    """The latest scheduled time at or before `now` that hasn't run yet, if recent."""
    now = now.astimezone(ET)
    past = [s for s in _slots(now, times) if s <= now and now - s < GRACE]
    if not past:
        return None
    slot = past[-1]
    if last and datetime.fromisoformat(last) >= slot:
        return None
    return slot


def next_slot(now: datetime, times: list[str]) -> datetime | None:
    now = now.astimezone(ET)
    for days in range(8):
        day = now + timedelta(days=days)
        for s in _slots(day, times):
            if s > now:
                return s
    return None


def load_state() -> dict:
    try:
        return json.loads(STATE.read_text())
    except (FileNotFoundError, ValueError):
        return {}


def _save_state(state: dict) -> None:
    tmp = STATE.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=1) + "\n")
    tmp.replace(STATE)


def record_run(result: dict, slot: datetime | None = None) -> None:
    state = load_state()
    if slot is not None:
        state["last_slot"] = slot.isoformat()
    state["last_run"] = {"at": datetime.now(ET).isoformat(), **{k: result.get(k) for k in ("status", "title", "error")}}
    _save_state(state)


def start(run_check, get_times) -> threading.Thread:
    """Background loop: every 30s, run `run_check()` if a slot is due."""

    def loop() -> None:
        while True:
            try:
                slot = due_slot(datetime.now(ET), get_times(), load_state().get("last_slot"))
                if slot:
                    print(f"[schedule] running check for {slot:%a %H:%M} ET", flush=True)
                    try:
                        result = run_check()
                    except Exception as exc:
                        result = {"status": "error", "error": str(exc)}
                        print(f"[schedule] check failed: {exc!r}", flush=True)
                    record_run(result, slot)
            except Exception as exc:  # never let the scheduler thread die
                print(f"[schedule] error: {exc!r}", flush=True)
            time.sleep(30)

    t = threading.Thread(target=loop, name="scheduler", daemon=True)
    t.start()
    return t
