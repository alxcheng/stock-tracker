from pathlib import PureWindowsPath

import desktop


def test_windows_shortcuts_point_at_launcher_and_quote_paths():
    root = PureWindowsPath(r"C:\Users\Pat O'Brien\AppData\Local\StockTracker")
    ps = desktop.windows_script(PureWindowsPath(r"C:\py\pythonw.exe"), root)
    assert "'C:\\Users\\Pat O''Brien\\AppData\\Local\\StockTracker\\open.pyw'" in ps
    assert "Stock Tracker (background).lnk" in ps and "bootstrap.py" in ps
    assert "Stock Tracker.url" in ps  # old page-only shortcuts are removed


def test_mac_launcher_starts_job_then_opens_page():
    script = desktop.mac_script(8052, "com.stocktracker.test")
    assert "http://127.0.0.1:8052/api/status" in script
    assert "launchctl kickstart gui/$(id -u)/com.stocktracker.test" in script
    assert script.strip().endswith('open location "http://127.0.0.1:8052"')


def test_ensure_runs_once_per_version(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(desktop, "ROOT", tmp_path)
    monkeypatch.setattr(desktop, "_mac", lambda: calls.append("mac"))
    monkeypatch.setattr(desktop, "_windows", lambda: calls.append("win"))
    desktop.ensure()
    desktop.ensure()
    assert len(calls) <= 1 and (tmp_path / "desktop.txt").read_text().strip() == desktop.VERSION
    assert (tmp_path / "open.pyw").exists() and (tmp_path / "icon.ico").exists()


def test_dev_checkout_leaves_desktop_alone(monkeypatch):
    monkeypatch.setattr(desktop, "ROOT", None)
    desktop.ensure()  # no error, nothing to do
