#!/usr/bin/env bash
set -euo pipefail

repository_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
tauri="${repository_root}/node_modules/.bin/tauri"
source_icon="${repository_root}/src-tauri/icons/logo.png"
icons_dir="${repository_root}/src-tauri/icons"

if [[ ! -x "$tauri" ]]; then
    printf 'Required lock-selected Tauri CLI is missing: %s\n' "$tauri" >&2
    exit 1
fi

temporary="$(mktemp -d "${TMPDIR:-/tmp}/qq-dictation-icons.XXXXXX")"
cleanup() {
    rm -rf "$temporary"
}
trap cleanup EXIT

"$tauri" icon --output "$temporary" --png 32,128,256 "$source_icon"
install -m 0644 "$temporary/32x32.png" "$icons_dir/32x32.png"
install -m 0644 "$temporary/128x128.png" "$icons_dir/128x128.png"
install -m 0644 "$temporary/256x256.png" "$icons_dir/128x128@2x.png"
