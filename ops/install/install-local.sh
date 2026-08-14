#!/usr/bin/env bash
set -euo pipefail

repository_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
source_app_dir="${repository_root}/.local-build/Handy.AppDir"
install_parent="${HOME}/.local/opt/qq-dictation"
install_app_dir="${install_parent}/Handy.AppDir"
local_bin="${HOME}/.local/bin"
user_units="${HOME}/.config/systemd/user"
settings_path="${XDG_DATA_HOME:-${HOME}/.local/share}/com.pais.handy/settings_store.json"
timestamp="$(date -u +%Y%m%dT%H%M%SZ)"

if [[ ! -x "${source_app_dir}/AppRun" ]]; then
    printf 'Build artifact is missing; run ops/build/build-local.sh first.\n' >&2
    exit 1
fi

/usr/bin/python3 -c 'import Xlib' >/dev/null
test -x /usr/bin/setsid

mkdir -p "$install_parent" "$local_bin" "$user_units"
/usr/bin/python3 "${repository_root}/ops/install/install-remote-workstation.py" \
    --home "$HOME"

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

install -m 0755 "${repository_root}/ops/package/handy" "${local_bin}/handy"
install -m 0755 \
    "${repository_root}/ops/install/handy-ptt-bridge.py" \
    "${local_bin}/handy-ptt-bridge.py"
install -m 0644 \
    "${repository_root}/ops/install/handy-ptt.service" \
    "${user_units}/handy-ptt.service"

mkdir -p "$(dirname "$settings_path")"
if [[ -f "$settings_path" ]]; then
    cp -a "$settings_path" "${settings_path}.before-qq-dictation.${timestamp}"
else
    (umask 077; printf '{"settings": {}}\n' >"$settings_path")
fi
/usr/bin/python3 "${repository_root}/ops/install/configure-local-settings.py" \
    "$settings_path"

systemctl --user daemon-reload
systemctl --user enable --now handy-ptt.service

/usr/bin/setsid --fork "${local_bin}/handy" --start-hidden \
    </dev/null >/dev/null 2>&1
for _ in {1..50}; do
    if pgrep -x handy >/dev/null; then
        break
    fi
    sleep 0.1
done
if ! pgrep -x handy >/dev/null; then
    printf 'Handy did not remain running after installation.\n' >&2
    exit 1
fi
handy_pid="$(pgrep -n -x handy)"
expected_executable="${install_app_dir}/usr/bin/handy"
actual_executable="$(readlink -f "/proc/${handy_pid}/exe")"
if [[ "$actual_executable" != "$expected_executable" ]]; then
    printf 'Unexpected Handy executable after installation: %s\n' \
        "$actual_executable" >&2
    exit 1
fi

printf 'Installed qq-dictation at %s\n' "$install_app_dir"
printf '%s\n' \
    'The q mode bridge is active: Left-Control arms/exits; Space starts/stops; Delete cancels.'
