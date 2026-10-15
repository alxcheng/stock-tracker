#!/bin/sh
# Publish a new version that every install picks up within a day:
#   ./release.sh 1.1.0 "What changed, in a sentence or two"
set -eu
V="${1:?usage: ./release.sh <version> [notes]}"
NOTES="${2:-}"
cd "$(dirname "$0")"

[ -z "$(git status --porcelain)" ] || { echo "Commit or stash your changes first."; exit 1; }
uv run pytest -q

printf '%s\n' "$V" > VERSION
sed -i '' "s/^version = .*/version = \"$V\"/" pyproject.toml
uv lock -q
git commit -qam "Release $V"
git tag "v$V"

rm -rf dist && mkdir dist
git archive --format=zip -o dist/stock-tracker.zip HEAD
(cd dist && shasum -a 256 stock-tracker.zip > stock-tracker.zip.sha256)

git push -q origin HEAD "v$V"
gh release create "v$V" dist/stock-tracker.zip dist/stock-tracker.zip.sha256 install.sh install.ps1 \
  --title "Stock Tracker $V" --notes "${NOTES:-Version $V}"
echo "Released $V. Installs will update within a day."
