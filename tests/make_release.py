"""Build local releases for testing the installers and the updater.

    python tests/make_release.py OUTDIR

From the committed code (git archive HEAD) this writes, in OUTDIR:
  v1/stock-tracker.zip(.sha256)      VERSION 1.0.0, what the installer installs
  v2/stock-tracker.zip(.sha256)      VERSION 99.0.0, the update
  broken/stock-tracker.zip(.sha256)  VERSION 99.0.0 that passes the self-test but
                                     crashes on start, to exercise rollback
  latest-v2.json, latest-broken.json in GitHub's "latest release" API format
"""

import hashlib
import io
import json
import subprocess
import sys
import zipfile
from pathlib import Path

out = Path(sys.argv[1]).resolve()
repo = Path(__file__).resolve().parent.parent
base = subprocess.run(["git", "archive", "--format=zip", "HEAD"], cwd=repo, check=True, capture_output=True).stdout


def build(name: str, version: str, crash: bool = False) -> Path:
    d = out / name
    d.mkdir(parents=True, exist_ok=True)
    buf = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(base)) as src, zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as dst:
        for item in src.infolist():
            data = src.read(item)
            if item.filename == "VERSION":
                data = f"{version}\n".encode()
            if crash and item.filename == "app.py":
                data = data.replace(b"def main() -> int:\n", b"def main() -> int:\n    raise SystemExit('simulated broken release')\n")
            dst.writestr(item, data)
    zpath = d / "stock-tracker.zip"
    zpath.write_bytes(buf.getvalue())
    (d / "stock-tracker.zip.sha256").write_text(f"{hashlib.sha256(buf.getvalue()).hexdigest()}  stock-tracker.zip\n")
    return zpath


def api(name: str, version: str, zpath: Path) -> None:
    rel = {
        "tag_name": f"v{version}",
        "body": f"Test release {version} ({name}).",
        "assets": [
            {"name": "stock-tracker.zip", "browser_download_url": zpath.as_uri()},
            {"name": "stock-tracker.zip.sha256", "browser_download_url": zpath.with_suffix(".zip.sha256").as_uri()},
        ],
    }
    (out / f"latest-{name}.json").write_text(json.dumps(rel, indent=1))


build("v1", "1.0.0")
api("v2", "99.0.0", build("v2", "99.0.0"))
api("broken", "99.0.0", build("broken", "99.0.0", crash=True))
print(f"test releases in {out}")
