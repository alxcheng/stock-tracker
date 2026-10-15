"""Silent auto-update from GitHub Releases (installed copies only).

Install layout (ROOT):
  bootstrap.py        keeps the app running; restarts it after an update
  install.json        {"uv": "<path to uv>"} written by the installer
  current.txt         version that runs
  previous.txt        version to roll back to
  trial.txt           set right after an update; the app clears it once it's healthy
  bad.txt             versions that crashed after updating; never retried
  versions/<v>/       each version's code and its own .venv
  data/               the person's watchlist, positions and settings (never touched)

An update is staged completely (download, checksum, dependencies, self-test)
before current.txt is switched, so a failed download or install changes nothing.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
import zipfile
from datetime import datetime, timezone
from pathlib import Path

from paths import CODE, DATA, ROOT, VERSION

REPO = os.environ.get("STOCK_TRACKER_REPO", "alxcheng/stock-tracker")
RELEASES_API = os.environ.get(
    "STOCK_TRACKER_RELEASES_API", f"https://api.github.com/repos/{REPO}/releases/latest"
)
ASSET = "stock-tracker.zip"
CHECK_EVERY = 24 * 3600
FIRST_CHECK_DELAY = 120
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)  # Windows: no console flash

LAST_UPDATE = DATA / "last_update.json"
_status: dict = {}
_lock = threading.Lock()


def parse_version(v: str) -> tuple[int, ...]:
    try:
        return tuple(int(x) for x in v.strip().lstrip("v").split("."))
    except ValueError:
        return (0,)


def _get(url: str, timeout: int = 30) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": f"stock-tracker/{VERSION}",
                                               "Accept": "application/vnd.github+json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()


def _install_info() -> dict:
    try:
        return json.loads((ROOT / "install.json").read_text())
    except (FileNotFoundError, ValueError, TypeError):
        return {}


def latest_release() -> dict:
    rel = json.loads(_get(_install_info().get("releases_api") or RELEASES_API))
    assets = {a["name"]: a["browser_download_url"] for a in rel.get("assets", [])}
    if ASSET not in assets or f"{ASSET}.sha256" not in assets:
        raise RuntimeError(f"release {rel.get('tag_name')} is missing {ASSET} or its checksum")
    return {
        "version": rel["tag_name"].lstrip("v"),
        "notes": (rel.get("body") or "").strip(),
        "zip": assets[ASSET],
        "sha256": assets[f"{ASSET}.sha256"],
    }


def _read(name: str) -> str:
    try:
        return (ROOT / name).read_text().strip()
    except FileNotFoundError:
        return ""


def _write(name: str, text: str) -> None:
    tmp = ROOT / f"{name}.tmp"
    tmp.write_text(text + "\n")
    tmp.replace(ROOT / name)


def venv_python(vdir: Path) -> Path:
    if sys.platform == "win32":
        return vdir / ".venv" / "Scripts" / "python.exe"
    return vdir / ".venv" / "bin" / "python"


def _uv() -> str:
    return _install_info()["uv"]


def stage(rel: dict, vdir: Path) -> None:
    """Download, verify, install dependencies and self-test a release into vdir.

    Nothing points at vdir until switch(), so a failure here leaves the running
    version untouched; the half-built folder is removed.
    """
    shutil.rmtree(vdir, ignore_errors=True)
    vdir.mkdir(parents=True)
    try:
        expected = _get(rel["sha256"]).decode().split()[0].lower()
        with tempfile.TemporaryDirectory() as tmp:
            zpath = Path(tmp) / ASSET
            zpath.write_bytes(_get(rel["zip"], timeout=300))
            if hashlib.sha256(zpath.read_bytes()).hexdigest() != expected:
                raise RuntimeError(f"checksum mismatch for {rel['version']}")
            with zipfile.ZipFile(zpath) as zf:
                zf.extractall(vdir)
        for cmd in (
            [_uv(), "sync", "--frozen", "--no-dev", "--project", str(vdir)],
            [str(venv_python(vdir)), str(vdir / "selftest.py")],
        ):
            subprocess.run(cmd, check=True, capture_output=True, cwd=vdir, creationflags=NO_WINDOW,
                           env={**os.environ, "UV_MANAGED_PYTHON": "1"})
    except subprocess.CalledProcessError as exc:
        shutil.rmtree(vdir, ignore_errors=True)
        out = (exc.stderr or exc.stdout or b"").decode(errors="replace")[-500:]
        raise RuntimeError(f"{Path(exc.cmd[0]).name} {exc.cmd[1]} failed: {out}") from exc
    except Exception:
        shutil.rmtree(vdir, ignore_errors=True)
        raise


def switch(version: str, notes: str) -> None:
    vdir = ROOT / "versions" / version
    _write("previous.txt", VERSION)
    _write("trial.txt", version)
    shutil.copy(vdir / "bootstrap.py", ROOT / "bootstrap.py")
    _write("current.txt", version)
    LAST_UPDATE.write_text(json.dumps({
        "from": VERSION, "to": version, "notes": notes,
        "at": datetime.now(timezone.utc).isoformat(),
    }, indent=1) + "\n")


def cleanup(keep: set[str]) -> None:
    for d in (ROOT / "versions").iterdir():
        if d.name not in keep:
            shutil.rmtree(d, ignore_errors=True)


def check(apply: bool = True) -> dict:
    """Check GitHub for a newer release and install it. Returns a status dict;
    status "updated" means the app should restart into the new version."""
    with _lock:
        result = {"at": datetime.now(timezone.utc).isoformat()}
        if ROOT is None:
            result.update(status="dev", message="Development copy; automatic updates are off.")
        else:
            try:
                rel = latest_release()
                bad = set(_read("bad.txt").split())
                if parse_version(rel["version"]) <= parse_version(VERSION):
                    result.update(status="current", latest=rel["version"])
                elif rel["version"] in bad:
                    result.update(status="skipped", latest=rel["version"],
                                  message=f"{rel['version']} failed to start before; staying on {VERSION}")
                elif apply:
                    stage(rel, ROOT / "versions" / rel["version"])
                    switch(rel["version"], rel["notes"])
                    cleanup({VERSION, rel["version"]})
                    result.update(status="updated", latest=rel["version"])
                else:
                    result.update(status="available", latest=rel["version"])
            except Exception as exc:
                result.update(status="error", message=str(exc)[:300])
        _status.clear()
        _status.update(result)
        print(f"[update] {result}", flush=True)
        return result


def status() -> dict:
    return dict(_status)


def last_update() -> dict | None:
    try:
        return json.loads(LAST_UPDATE.read_text())
    except (FileNotFoundError, ValueError):
        return None


def mark_healthy() -> None:
    """Called once the app has been running fine for a while after starting."""
    if ROOT is not None and _read("trial.txt") == VERSION:
        (ROOT / "trial.txt").unlink(missing_ok=True)


def start(on_updated) -> threading.Thread:
    def loop() -> None:
        time.sleep(FIRST_CHECK_DELAY)
        mark_healthy()
        while True:
            if check()["status"] == "updated":
                on_updated()
                return
            time.sleep(CHECK_EVERY)

    t = threading.Thread(target=loop, name="updater", daemon=True)
    t.start()
    return t
