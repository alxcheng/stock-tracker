import json

import pytest

import lists
import tracker


@pytest.fixture
def data(tmp_path, monkeypatch):
    monkeypatch.setattr(lists, "FILE", tmp_path / "lists.json")
    monkeypatch.setattr(lists, "LEGACY", tmp_path / "watchlist.json")
    return tmp_path


def test_old_watchlist_becomes_first_list(data):
    (data / "watchlist.json").write_text(json.dumps(["AAPL", "nvda", "AAPL"]))
    [first] = lists.load()
    assert first["name"] == "Watchlist" and first["tickers"] == ["AAPL", "NVDA"]
    assert all(first["alerts"].values())


def test_create_rename_delete_and_unique_names(data):
    lists.load()
    a = lists.create("Tech", ["MSFT"])
    b = lists.create("tech")
    assert b["name"] == "tech (2)"
    assert lists.update(a["id"], name="  Big   Tech ")["name"] == "Big Tech"
    assert lists.update(a["id"], alerts={"oversold": False})["alerts"]["oversold"] is False
    assert lists.add_ticker(a["id"], "AMD") == "added" and lists.add_ticker(a["id"], "AMD") == "exists"
    assert lists.remove_ticker(a["id"], "MSFT")
    assert lists.get(a["id"])["tickers"] == ["AMD"]
    assert lists.delete(b["id"]) == "deleted"
    for l in lists.load()[:-1]:
        lists.delete(l["id"])
    assert lists.delete(lists.load()[0]["id"]) == "last"


def test_share_roundtrip_from_code_or_inside_a_message(data):
    lst = {"name": "Ideas", "tickers": ["RIVN", "BRK-B", "^GSPC"],
           "alerts": {"enabled": True, "overbought": False, "oversold": True, "below_ma": False}}
    code = lists.share_code(lst)
    assert code.startswith("ST1-")
    for text in (code, f"check these out:\n{code}\ncheers!"):
        got = lists.parse_share(text)
        assert got == {"name": "Ideas", "tickers": lst["tickers"], "alerts": lst["alerts"]}


def test_bad_share_codes_are_rejected(data):
    for bad in ("hello", "ST1-", "ST1-notbase64!!", "ST1-" + "A" * 40):
        with pytest.raises(ValueError):
            lists.parse_share(bad)


def test_stock_on_two_lists_is_reported_once_per_alert(data):
    r = tracker.Reading("X", price=10, prev_close=11, k=5, d=8, zone="oversold", prev_zone="neutral",
                        sma=12, crossed_below=True)
    cfg = {"alerts": {}, "moving_average": {"period": 50}}
    a = {"id": "a", "name": "A", "tickers": ["X"], "alerts": lists._alerts({"below_ma": False})}
    b = {"id": "b", "name": "B", "tickers": ["X"], "alerts": lists._alerts()}
    title, body, _ = tracker.build_alert({"X": r}, [a, b], cfg)
    assert title == "Stocks: 1 oversold, 1 fell below 50d MA"
    assert body == "A:\n" + r.line() + "\n\nB:\n" + r.ma_line(50)
