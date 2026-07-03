#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DIST="$ROOT/dist"
rm -rf "$DIST"
mkdir -p "$DIST"

copy_if_exists() {
  local path="$1"
  if [ -e "$ROOT/$path" ]; then
    mkdir -p "$DIST/$(dirname "$path")"
    cp -R "$ROOT/$path" "$DIST/$path"
  fi
}

copy_if_exists index.html
copy_if_exists favicon.svg
copy_if_exists preview.png
copy_if_exists preview.svg
copy_if_exists preview_trending.png
copy_if_exists preview_v2.png
copy_if_exists robots.txt
copy_if_exists sitemap.xml
copy_if_exists assets
copy_if_exists static
copy_if_exists data/updates.json

touch "$DIST/.nojekyll"
find "$DIST" -type f | sort | sed "s#^$DIST/##"
