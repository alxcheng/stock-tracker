"""End-to-end check against an installed copy: trigger an update and wait for
the app to come back on the new version.

    python tests/e2e_update.py EXPECTED_VERSION [PORT]
"""

import json
import sys
import time
import urllib.request

expected = sys.argv[1]
base = f"http://127.0.0.1:{sys.argv[2] if len(sys.argv) > 2 else 8050}"


def call(path, method="GET"):
    req = urllib.request.Request(base + path, data=b"{}" if method == "POST" else None, method=method,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=300) as r:
        return json.loads(r.read())


before = call("/api/status")["version"]
print("installed version:", before)
result = call("/api/check-updates", "POST")
print("update check:", result)
assert result["status"] == "updated", result

deadline = time.time() + 180
while time.time() < deadline:
    try:
        st = call("/api/status")
        if st["version"] == expected:
            print("now running:", st["version"], "| last_update:", st["last_update"])
            assert st["last_update"]["from"] == before
            assert call("/api/stocks").get("stocks") is not None
            print("OK")
            sys.exit(0)
    except Exception:
        pass
    time.sleep(2)
sys.exit(f"app did not come back on {expected}")
