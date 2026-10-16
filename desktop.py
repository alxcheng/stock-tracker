"""Puts Stock Tracker's launchers in place on the person's computer.

Runs when an installed copy starts, once per version, so installs that got a
new version through auto-update (not by re-running the installer) still get
new shortcuts and helper files.

  Windows: "Stock Tracker" on the Desktop and in the Start menu runs open.pyw,
           which starts the app if needed and opens the page. A Startup-folder
           shortcut starts it at sign-in.
  macOS:   ~/Applications/Stock Tracker.app does the same through launchd.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from paths import CODE, PORT, ROOT, VERSION

NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
HELPERS = {  # copied from the code into the install folder, where shortcuts point
    "bootstrap.py": CODE / "bootstrap.py",
    "open.pyw": CODE / "open.pyw",
    "icon.ico": CODE / "web" / "icon.ico",
    "uninstall.ps1" if sys.platform == "win32" else "uninstall.sh":
        CODE / ("uninstall.ps1" if sys.platform == "win32" else "uninstall.sh"),
}


def _install_info() -> dict:
    try:
        return json.loads((ROOT / "install.json").read_text())
    except (OSError, ValueError, TypeError):
        return {}


def base_pythonw() -> Path:
    """pythonw.exe of the Python this app's venv was made from (it outlives
    each version's venv, so shortcuts can point at it)."""
    cfg = Path(sys.prefix) / "pyvenv.cfg"
    for line in cfg.read_text().splitlines() if cfg.exists() else []:
        key, _, value = line.partition("=")
        if key.strip() == "home":
            return Path(value.strip()) / "pythonw.exe"
    return Path(getattr(sys, "_base_executable", sys.executable)).with_name("pythonw.exe")


def _ps_quote(value) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def windows_script(pythonw: Path, root: Path) -> str:
    """PowerShell that creates (or refreshes) the three shortcuts."""
    return f"""
$shell = New-Object -ComObject WScript.Shell
function Make($path, $script, $icon) {{
    $l = $shell.CreateShortcut($path)
    $l.TargetPath = {_ps_quote(pythonw)}
    $l.Arguments = '"' + $script + '"'
    $l.WorkingDirectory = {_ps_quote(root)}
    if ($icon) {{ $l.IconLocation = $icon }}
    $l.Description = 'Open Stock Tracker'
    $l.Save()
}}
Make (Join-Path ([Environment]::GetFolderPath('Startup')) 'Stock Tracker (background).lnk') {_ps_quote(root / 'bootstrap.py')} ''
foreach ($dir in [Environment]::GetFolderPath('Desktop'), [Environment]::GetFolderPath('Programs')) {{
    Make (Join-Path $dir 'Stock Tracker.lnk') {_ps_quote(root / 'open.pyw')} {_ps_quote(str(root / 'icon.ico') + ',0')}
    Remove-Item (Join-Path $dir 'Stock Tracker.url') -ErrorAction SilentlyContinue
}}
"""


def mac_script(port: int, label: str) -> str:
    """AppleScript for Stock Tracker.app: start the background job if the app
    isn't answering, wait for it, open the page."""
    plist = Path.home() / "Library" / "LaunchAgents" / f"{label}.plist"
    return f'''set statusURL to "http://127.0.0.1:{port}/api/status"
try
	do shell script "curl -fs -m 2 " & quoted form of statusURL
on error
	do shell script "launchctl bootstrap gui/$(id -u) " & quoted form of "{plist}" & " 2>/dev/null; launchctl kickstart gui/$(id -u)/{label} 2>/dev/null; true"
	set started to false
	repeat 90 times
		try
			do shell script "curl -fs -m 1 " & quoted form of statusURL
			set started to true
			exit repeat
		end try
		delay 1
	end repeat
	if not started then
		display dialog "Stock Tracker didn't start. Details are in ~/Library/Application Support/Stock Tracker/logs/app.log" buttons {{"OK"}} default button 1 with title "Stock Tracker"
		return
	end if
end try
open location "http://127.0.0.1:{port}"
'''


def _windows() -> None:
    pythonw = base_pythonw()
    if not pythonw.exists():
        raise FileNotFoundError(pythonw)
    subprocess.run(
        ["powershell", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
         "-Command", windows_script(pythonw, ROOT)],
        check=True, capture_output=True, creationflags=NO_WINDOW,
    )


def _mac() -> None:
    info = _install_info()
    label = info.get("label") or "com.stocktracker.app"
    app = Path(info.get("app") or Path.home() / "Applications" / "Stock Tracker.app")
    app.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        src = Path(tmp) / "launcher.applescript"
        src.write_text(mac_script(int(info.get("port") or PORT), label))
        built = Path(tmp) / "Stock Tracker.app"
        subprocess.run(["osacompile", "-o", str(built), str(src)], check=True, capture_output=True)
        icns = built / "Contents" / "Resources" / "applet.icns"
        # Best effort: the app still works with the default script icon.
        subprocess.run(["sips", "-s", "format", "icns", str(CODE / "web" / "icon.png"), "--out", str(icns)],
                       capture_output=True)
        shutil.rmtree(app, ignore_errors=True)
        shutil.move(str(built), str(app))


def ensure() -> None:
    """Refresh helper files and launchers if this version hasn't yet."""
    if ROOT is None:  # dev checkout
        return
    marker = ROOT / "desktop.txt"
    if marker.exists() and marker.read_text().strip() == VERSION:
        return
    for name, src in HELPERS.items():
        if src.exists():
            shutil.copy2(src, ROOT / name)
    if sys.platform == "win32":
        _windows()
    elif sys.platform == "darwin":
        _mac()
    marker.write_text(VERSION + "\n")
    print(f"[desktop] launchers ready for {VERSION}", flush=True)


def ensure_quietly() -> None:
    try:
        ensure()
    except Exception as exc:  # shortcuts are a convenience; never block the app
        print(f"[desktop] couldn't set up launchers: {exc!r}", flush=True)
