#!/usr/bin/env bash
set -euo pipefail

repository_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
source_app_dir="${repository_root}/.local-build/Handy.AppDir"
install_parent="${HOME}/.local/opt/qq-dictation"
install_app_dir="${install_parent}/Handy.AppDir"
local_bin="${HOME}/.local/bin"
user_units="${HOME}/.config/systemd/user"
settings_path="${XDG_DATA_HOME:-${HOME}/.local/share}/com.pais.handy/settings_store.json"
timestamp="$(date -u +%Y%m%dT%H%M%SZ)"

if [[ ! -x "${source_app_dir}/AppRun" ]]; then
    printf 'Build artifact is missing; run scripts/build-local.sh first.\n' >&2
    exit 1
fi

"${repository_root}/scripts/fetch-fdt-model.sh"

mkdir -p "$install_parent" "$local_bin" "$user_units"
staging_dir="$(mktemp -d "${install_parent}/Handy.AppDir.staging.XXXXXX")"
cleanup() {
    rm -rf "$staging_dir"
}
trap cleanup EXIT
cp -a "${source_app_dir}/." "$staging_dir/"

systemctl --user stop handy-ptt.service 2>/dev/null || true
pkill -x handy 2>/dev/null || true

if [[ -d "$install_app_dir" ]]; then
    backup_dir="${install_parent}/Handy.AppDir.backup.${timestamp}"
    mv "$install_app_dir" "$backup_dir"
    printf 'Previous qq-dictation AppDir retained at %s\n' "$backup_dir"
fi
mv "$staging_dir" "$install_app_dir"
trap - EXIT

for existing in "${local_bin}/handy" "${local_bin}/handy-ptt-bridge.py" \
    "${user_units}/handy-ptt.service"; do
    if [[ -e "$existing" && ! -e "${existing}.before-qq-dictation" ]]; then
        cp -a "$existing" "${existing}.before-qq-dictation"
    fi
done

install -m 0755 "${repository_root}/packaging/handy" "${local_bin}/handy"
install -m 0755 \
    "${repository_root}/packaging/handy-ptt-bridge.py" \
    "${local_bin}/handy-ptt-bridge.py"
install -m 0644 \
    "${repository_root}/packaging/handy-ptt.service" \
    "${user_units}/handy-ptt.service"

if [[ -f "$settings_path" ]]; then
    cp -a "$settings_path" "${settings_path}.before-qq-dictation.${timestamp}"
    python3 - "$settings_path" <<'PY'
import json
import os
import sys
from pathlib import Path

path = Path(sys.argv[1])
data = json.loads(path.read_text(encoding="utf-8"))
data.setdefault("settings", {})["overlay_style"] = "minimal"
temporary = path.with_suffix(path.suffix + ".tmp")
temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
os.replace(temporary, path)
PY
fi

python3 -c 'import Xlib' >/dev/null
systemctl --user daemon-reload
systemctl --user enable --now handy-ptt.service

"${local_bin}/handy" --start-hidden >/dev/null 2>&1 &
disown

printf 'Installed qq-dictation at %s\n' "$install_app_dir"
printf 'The right-Control bridge and native minimal recording overlay are active.\n'
