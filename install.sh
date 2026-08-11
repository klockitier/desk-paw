#!/usr/bin/env bash
# Desk Paw — macOS installer (no Python, no DMG mount, safe one-liner)
#
# Install with:
#   curl -fsSL https://raw.githubusercontent.com/klockitier/desk-paw/main/install.sh -o /tmp/install-desk-paw.sh && bash /tmp/install-desk-paw.sh
#
set -euo pipefail

REPO="klockitier/desk-paw"
APP_NAME="Desk Paw.app"
DEST="/Applications/${APP_NAME}"

die() {
  echo "✗ $*" >&2
  exit 1
}

on_err() {
  die "Install failed (line $1)."
}
trap 'on_err $LINENO' ERR

[[ "$(uname -s)" == "Darwin" ]] || die "Desk Paw’s installer is macOS-only."

arch="$(uname -m)"
case "$arch" in
  arm64) archive_name="Desk.Paw_aarch64.app.tar.gz" ;;
  x86_64) archive_name="Desk.Paw_x64.app.tar.gz" ;;
  *) die "Unsupported Mac architecture: $arch" ;;
esac

# Stable GitHub “latest” URL — no API / Python needed.
download_url="https://github.com/${REPO}/releases/latest/download/${archive_name}"

tmp="$(mktemp -d /tmp/desk-paw-install.XXXXXX)"
cleanup() { rm -rf "$tmp"; }
trap cleanup EXIT
trap 'on_err $LINENO' ERR

echo "→ Desk Paw installer"
echo "  arch: $arch"
echo "  package: $archive_name"

archive_path="${tmp}/${archive_name}"
echo "→ Downloading from GitHub Releases (~4–5 MB)…"
curl -fL --connect-timeout 30 --retry 3 --retry-delay 2 \
  --progress-bar "$download_url" -o "$archive_path" \
  || die "Download failed. Check https://github.com/${REPO}/releases/latest"

[[ -s "$archive_path" ]] || die "Downloaded file is empty."

echo "→ Unpacking…"
extract_dir="${tmp}/extract"
mkdir -p "$extract_dir"
tar -xzf "$archive_path" -C "$extract_dir" || die "Could not unpack the archive."

src_app="${extract_dir}/${APP_NAME}"
if [[ ! -d "$src_app" ]]; then
  shopt -s nullglob
  apps=("$extract_dir"/*.app)
  shopt -u nullglob
  [[ ${#apps[@]} -gt 0 && -d "${apps[0]}" ]] || die "Desk Paw.app missing from the archive."
  src_app="${apps[0]}"
fi

echo "→ Installing to ${DEST}…"
if [[ -e "$DEST" ]]; then
  # A running instance keeps the old code loaded — `open` on an already-running
  # app just refocuses it instead of relaunching, so an update would silently
  # never take effect until the user thought to quit it manually.
  osascript -e 'quit app "Desk Paw"' >/dev/null 2>&1 || true
  pkill -f "${DEST}/Contents/MacOS/" >/dev/null 2>&1 || true
  rm -rf "$DEST"
fi
ditto "$src_app" "$DEST" || die "Could not copy app into /Applications (permissions?)."

echo "→ Clearing Gatekeeper quarantine…"
xattr -cr "$DEST" || true

echo "→ Launching…"
open "$DEST"

echo
echo "✓ Installed: $DEST"
echo "  For typing in other apps, enable Desk Paw under:"
echo "  System Settings → Privacy & Security → Accessibility"
echo "  System Settings → Privacy & Security → Input Monitoring"
echo "  Then quit and reopen Desk Paw."
