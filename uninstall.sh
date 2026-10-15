#!/bin/sh
# Removes Stock Tracker from this Mac. Your watchlist and positions are kept in
# the data folder unless you pass --delete-data.
set -eu
ROOT="${STOCK_TRACKER_HOME:-$HOME/Library/Application Support/Stock Tracker}"
LABEL="${STOCK_TRACKER_LABEL:-com.stocktracker.app}"
launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
rm -f "$HOME/Library/LaunchAgents/$LABEL.plist"
rm -rf "${STOCK_TRACKER_APP:-$HOME/Applications/Stock Tracker.app}"
if [ "${1:-}" = "--delete-data" ]; then
  rm -rf "$ROOT"
  echo "Stock Tracker and all its data were removed."
else
  find "$ROOT" -mindepth 1 -maxdepth 1 ! -name data -exec rm -rf {} +
  echo "Stock Tracker was removed. Your data is still in: $ROOT/data"
fi
