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
  arm64) asset_suffix="aarch64.dmg" ;;
  x86_64) asset_suffix="x64.dmg" ;;
  *)
    echo "Unsupported Mac architecture: $arch"
    exit 1
    ;;
esac

tmp="$(mktemp -d)"
mount_point="${tmp}/volume"
cleanup() {
  if [[ -d "$mount_point" ]]; then
    hdiutil detach "$mount_point" -quiet >/dev/null 2>&1 || true
  fi
  rm -rf "$tmp"
}
trap cleanup EXIT

echo "→ Finding latest Desk Paw release…"
api="https://api.github.com/repos/${REPO}/releases/latest"
dmg_url="$(
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

dmg_path="${tmp}/DeskPaw.dmg"
echo "→ Downloading…"
curl -fL --progress-bar "$dmg_url" -o "$dmg_path"

echo "→ Installing to /Applications…"
mkdir -p "$mount_point"
hdiutil attach "$dmg_path" -nobrowse -readonly -mountpoint "$mount_point" >/dev/null

src_app=""
# Prefer an exact match; fall back to the only .app in the volume.
if [[ -d "${mount_point}/${APP_NAME}" ]]; then
  src_app="${mount_point}/${APP_NAME}"
else
  while IFS= read -r -d '' candidate; do
    src_app="$candidate"
    break
  done < <(find "$mount_point" -maxdepth 2 -name "*.app" -print0)
fi

if [[ -z "$src_app" || ! -d "$src_app" ]]; then
  echo "Could not find Desk Paw.app inside the DMG."
  exit 1
fi

# Replace any previous install
if [[ -e "$DEST" ]]; then
  rm -rf "$DEST"
fi
cp -R "$src_app" "$DEST"

echo "→ Clearing macOS quarantine so it opens without the malware dialog…"
xattr -cr "$DEST"

echo "→ Launching Desk Paw…"
open "$DEST"

echo
echo "Installed: $DEST"
echo "Optional: System Settings → Privacy & Security → Accessibility (for typing detection)."
