from datetime import datetime

import scheduler
import updater

ET = scheduler.ET
TIMES = ["10:00", "12:00", "14:00", "16:00"]


def at(*args):
    return datetime(*args, tzinfo=ET)


def test_due_right_after_a_slot():
    assert scheduler.due_slot(at(2026, 10, 15, 12, 0, 30), TIMES, None) == at(2026, 10, 15, 12)


def test_not_due_twice():
    last = at(2026, 10, 15, 12).isoformat()
    assert scheduler.due_slot(at(2026, 10, 15, 13, 30), TIMES, last) is None


def test_catches_up_after_sleep_but_not_hours_later():
    assert scheduler.due_slot(at(2026, 10, 15, 11, 40), TIMES, None) == at(2026, 10, 15, 10)
    assert scheduler.due_slot(at(2026, 10, 15, 9, 0), TIMES, None) is None  # before the first slot
    assert scheduler.due_slot(at(2026, 10, 15, 19, 0), TIMES, None) is None  # 3h after the last


def test_weekends_skipped_and_next_slot_is_monday():
    sat = at(2026, 10, 17, 12, 0, 30)
    assert scheduler.due_slot(sat, TIMES, None) is None
    assert scheduler.next_slot(sat, TIMES) == at(2026, 10, 19, 10)


def test_slot_time_follows_new_york_across_dst():
    # 10:00 ET is 14:00 UTC in summer and 15:00 UTC in winter.
    assert scheduler.next_slot(at(2026, 7, 1, 9), TIMES).utcoffset().total_seconds() == -4 * 3600
    assert scheduler.next_slot(at(2026, 12, 1, 9), TIMES).utcoffset().total_seconds() == -5 * 3600


def test_version_ordering():
    assert updater.parse_version("v1.10.0") > updater.parse_version("1.9.3")
    assert updater.parse_version("dev") < updater.parse_version("0.0.1")
