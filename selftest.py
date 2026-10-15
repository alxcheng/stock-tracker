"""Quick check that a freshly installed version can run, before switching to it.

Imports every module and runs the core math on made-up prices, with no network.
Exits non-zero on failure.
"""

import os
import sys
import tempfile

import pandas as pd

# Point data at a throwaway folder so the check never touches real files.
os.environ["STOCK_TRACKER_DATA"] = tempfile.mkdtemp()

import app  # noqa: E402,F401  (imports the server, scheduler and updater)
import positions  # noqa: E402
import tracker  # noqa: E402

closes = pd.Series([100.0 + (i % 9) for i in range(80)])
df = pd.DataFrame({"High": closes + 1, "Low": closes - 1, "Close": closes})
readings, _ = tracker.analyze({"TEST": df}, tracker.load_config())
assert readings and 0 <= readings[0].k <= 100 and readings[0].sma is not None
summary = positions.summarize(
    [{"id": "x", "ticker": "TEST", "shares": 1, "price": 50.0}], {"TEST": {"price": 100.0, "change": 1.0}}
)
assert round(summary["total"]["gain_pct"]) == 100
print("selftest ok")
sys.exit(0)
