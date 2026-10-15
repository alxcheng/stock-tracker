#!/bin/sh
# Stock Tracker installer for macOS. Paste into Terminal:
#
#   curl -fsSL https://github.com/alxcheng/stock-tracker/releases/latest/download/install.sh | sh
#
# No admin password, Python or Xcode needed. Running it again repairs the install
# and moves to the latest version. Your watchlist and positions are kept.
set -eu

REPO="${STOCK_TRACKER_REPO:-alxcheng/stock-tracker}"
ROOT="${STOCK_TRACKER_HOME:-$HOME/Library/Application Support/Stock Tracker}"
ZIP_URL="${STOCK_TRACKER_ZIP_URL:-https://github.com/$REPO/releases/latest/download/stock-tracker.zip}"
LABEL="${STOCK_TRACKER_LABEL:-com.stocktracker.app}"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
APP="${STOCK_TRACKER_APP:-$HOME/Applications/Stock Tracker.app}"
PORT="${STOCK_TRACKER_PORT:-8050}"
URL="http://127.0.0.1:$PORT"
export UV_MANAGED_PYTHON=1  # use uv's own Python, never whatever else is on the Mac

say() { printf '\n\033[1m==> %s\033[0m\n' "$*"; }
die() { printf '\n\033[31mError:\033[0m %s\n' "$*" >&2; exit 1; }

[ "$(uname -s)" = "Darwin" ] || die "This installer is for macOS. On Windows, use install.ps1."

say "Getting uv (runs the app's Python; installs into your home folder)"
UV="$HOME/.local/bin/uv"
if [ ! -x "$UV" ]; then
  curl -LsSf https://astral.sh/uv/install.sh | env UV_NO_MODIFY_PATH=1 sh >/dev/null
fi
[ -x "$UV" ] || die "Couldn't install uv."

say "Downloading the latest Stock Tracker"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
curl -fsSL "$ZIP_URL" -o "$TMP/stock-tracker.zip" || die "Download failed: $ZIP_URL"
curl -fsSL "$ZIP_URL.sha256" -o "$TMP/stock-tracker.zip.sha256" || die "Checksum download failed"
EXPECTED="$(cut -d' ' -f1 < "$TMP/stock-tracker.zip.sha256")"
ACTUAL="$(shasum -a 256 "$TMP/stock-tracker.zip" | cut -d' ' -f1)"
[ "$EXPECTED" = "$ACTUAL" ] || die "Checksum mismatch; the download may be corrupted. Try again."
mkdir -p "$TMP/x" && unzip -q "$TMP/stock-tracker.zip" -d "$TMP/x"
VERSION="$(tr -d '[:space:]' < "$TMP/x/VERSION")"
VDIR="$ROOT/versions/$VERSION"

# Stop a running copy so its files can be replaced.
launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
if [ "$LABEL" = "com.stocktracker.app" ]; then
  # Remove the pre-installer dev setup (separate web + alert jobs), if this Mac had it.
  for old in com.stochastic.web com.stochastic.tracker; do
    launchctl bootout "gui/$(id -u)/$old" 2>/dev/null || true
    rm -f "$HOME/Library/LaunchAgents/$old.plist"
  done
fi

say "Installing version $VERSION (first install downloads Python, about a minute)"
mkdir -p "$ROOT/versions" "$ROOT/data" "$ROOT/logs"
rm -rf "$VDIR" && mv "$TMP/x" "$VDIR"
"$UV" sync --frozen --no-dev --project "$VDIR" -q || die "Installing dependencies failed."
"$VDIR/.venv/bin/python" "$VDIR/selftest.py" >/dev/null || die "Self-test failed."

CURRENT="$(cat "$ROOT/current.txt" 2>/dev/null || true)"
if [ -n "$CURRENT" ] && [ "$CURRENT" != "$VERSION" ]; then printf '%s\n' "$CURRENT" > "$ROOT/previous.txt"; fi
printf '%s\n' "$VERSION" > "$ROOT/current.txt"
rm -f "$ROOT/trial.txt"
printf '{"uv": "%s", "repo": "%s", "port": %s, "releases_api": "%s"}\n' \
  "$UV" "$REPO" "$PORT" "${STOCK_TRACKER_RELEASES_API:-}" > "$ROOT/install.json"
cp "$VDIR/bootstrap.py" "$VDIR/uninstall.sh" "$ROOT/"
chmod +x "$ROOT/uninstall.sh"
PY="$("$UV" python find 3.12)"

say "Starting Stock Tracker in the background (it also starts when you log in)"
mkdir -p "$HOME/Library/LaunchAgents"
cat > "$PLIST" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>$LABEL</string>
  <key>ProgramArguments</key><array><string>$PY</string><string>$ROOT/bootstrap.py</string></array>
  <key>WorkingDirectory</key><string>$ROOT</string>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>StandardOutPath</key><string>$ROOT/logs/bootstrap.log</string>
  <key>StandardErrorPath</key><string>$ROOT/logs/bootstrap.log</string>
</dict></plist>
PLIST
launchctl bootstrap "gui/$(id -u)" "$PLIST"

# A small app in ~/Applications that opens the page (find it in Launchpad or Spotlight).
mkdir -p "$HOME/Applications"
rm -rf "$APP"
osacompile -o "$APP" -e "open location \"$URL\"" >/dev/null 2>&1 || true

printf 'Waiting for it to start'
i=0
until curl -fs "$URL/api/status" >/dev/null 2>&1; do
  i=$((i + 1)); [ $i -gt 60 ] && die "It didn't start. See $ROOT/logs/app.log"
  printf '.'; sleep 1
done
echo

say "Done! Stock Tracker $VERSION is running at $URL"
cat <<DONE
  - Open it any time from "Stock Tracker" in Launchpad or Spotlight.
  - Phone alerts: open the page and follow "Alerts & updates" at the bottom.
  - It updates itself. To uninstall: "$ROOT/uninstall.sh"
DONE
[ -n "${STOCK_TRACKER_NO_OPEN:-}" ] || open "$URL"
