#!/usr/bin/env bash
# Desk Paw — one-step macOS installer
# Usage: curl -fsSL https://raw.githubusercontent.com/klockitier/desk-paw/main/install.sh | bash
set -euo pipefail

REPO="klockitier/desk-paw"
APP_NAME="Desk Paw.app"
DEST="/Applications/${APP_NAME}"

if [[ "$(uname -s)" != "Darwin" ]]; then
  echo "Desk Paw’s prebuilt installer is macOS-only right now."
  exit 1
fi

arch="$(uname -m)"
case "$arch" in
  arm64) asset_suffix="aarch64.app.tar.gz" ;;
  x86_64) asset_suffix="x64.app.tar.gz" ;;
  *)
    echo "Unsupported Mac architecture: $arch"
    exit 1
    ;;
esac

tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT

echo "→ Finding latest Desk Paw release…"
api="https://api.github.com/repos/${REPO}/releases/latest"
# Close stdin so a `curl | bash` pipe can't feed the rest of this script into child commands.
exec </dev/null

archive_url="$(
  python3 - "$api" "$asset_suffix" <<'PY'
import json, sys, urllib.request
api, suffix = sys.argv[1], sys.argv[2]
with urllib.request.urlopen(api) as r:
    data = json.load(r)
for asset in data.get("assets", []):
    name = asset.get("name", "")
    if name.endswith(suffix):
        print(asset["browser_download_url"])
        raise SystemExit(0)
raise SystemExit(f"No release asset ending in {suffix}")
PY
)"

archive_path="${tmp}/DeskPaw.app.tar.gz"
echo "→ Downloading (~4–5 MB from GitHub)…"
curl -fL --progress-bar "$archive_url" -o "$archive_path"

echo "→ Installing to /Applications…"
extract_dir="${tmp}/extract"
mkdir -p "$extract_dir"
tar -xzf "$archive_path" -C "$extract_dir"

src_app="${extract_dir}/${APP_NAME}"
if [[ ! -d "$src_app" ]]; then
  # Fallback: first .app directory in the archive root
  for candidate in "$extract_dir"/*.app; do
    if [[ -d "$candidate" ]]; then
      src_app="$candidate"
      break
    fi
  done
fi

if [[ ! -d "$src_app" ]]; then
  echo "Could not find Desk Paw.app inside the release archive."
  exit 1
fi

if [[ -e "$DEST" ]]; then
  rm -rf "$DEST"
fi
# ditto preserves macOS app bundle metadata better than cp -R
ditto "$src_app" "$DEST"

echo "→ Clearing macOS quarantine so it opens without the malware dialog…"
xattr -cr "$DEST"

echo "→ Launching Desk Paw…"
open "$DEST"

echo
echo "Installed: $DEST"
echo "Optional: System Settings → Privacy & Security → Accessibility (for typing detection)."
